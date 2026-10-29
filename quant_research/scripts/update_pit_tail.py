# -*- coding: utf-8 -*-
"""增量更新 raw 缓存尾部：为已有 akshare 缓存的券拉取末日之后的新行。

与 backfill_cb_from_akshare.py 的区别：backfill 只处理"缓存缺失"的券；
本脚本处理"缓存存在但停在旧日期"的券——paper tracking 每次运行前调用。

机制：读 meta.json 的 akshare 来源标记，逐券直连东财 value_analysis，
append 末日之后的新行到对应年份 CSV（不改历史行）；无新行跳过。
日更 limit 用活跃券优先 + 滚动游标，避免每次卡在同一批退市券上。
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

_QUANT_ROOT = Path(__file__).resolve().parents[1]
if str(_QUANT_ROOT) not in sys.path:
    sys.path.insert(0, str(_QUANT_ROOT))
from scripts import pit_lock

logger = logging.getLogger("pit_tail_update")

_RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "pit" / "raw"
_CURSOR_FILE = ".tail_cursor.json"
# 活跃券窗口：缓存末日在此范围内的券优先刷新。日更 limit=50 时
# 约 6 个交易日覆盖全部活跃券；退市/长期停牌券只在全量轮次处理。
_ACTIVE_WINDOW_DAYS = 120


def _last_csv_date(sym_dir: Path) -> str:
    year_files = sorted(sym_dir.glob("????.csv"))
    if not year_files:
        return ""
    try:
        with open(year_files[-1], encoding="utf-8-sig") as fh:
            lines = [line.strip() for line in fh if line.strip()]
    except OSError:
        return ""
    return lines[-1].split(",", 1)[0].strip() if lines else ""


def _load_cursor(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return str(payload.get("last_symbol") or "").upper()
    except (OSError, ValueError, AttributeError):
        return ""


def _save_cursor(path: Path, symbol: str, processed: int) -> None:
    payload = {
        "last_symbol": symbol.upper(),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "processed_last_run": processed,
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


_VALUE_API = "https://datacenter-web.eastmoney.com/api/data/get"
_VALUE_API_PARAMS = {
    "sty": "ALL",
    "token": "894050c76af8597a853f5b408b759f5d",
    "st": "date",
    "sr": "1",
    "source": "WEB",
    "type": "RPTA_WEB_KZZ_LS",
}


def _fetch_history(code: str) -> Optional[pd.DataFrame]:
    import requests

    # 不再经 akshare：其 bond_zh_cov_value_analysis 按固定列位重命名
    # （13 列），东财新增 FLOORPRICE_PREMIUM_RATIO_CB / BOND_NAME_ABBR
    # 后直接 ValueError，导致 PIT 增量自 09-18 起全线失败。这里按字段名
    # 取 DATE/FCLOSE/SWAPOR，列顺序变化不再影响解析。
    params = {**_VALUE_API_PARAMS, "filter": f'(zcode="{code}")',
              "p": "1", "ps": "8000"}
    for attempt in range(3):
        try:
            resp = requests.get(
                _VALUE_API, params=params, timeout=30,
                headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            rows = (resp.json().get("result") or {}).get("data") or []
            break
        except Exception:  # noqa: BLE001 - 网络抖动重试（东财源间歇性 10054）
            if attempt == 2:
                return None
            time.sleep(2 ** attempt)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    if not {"DATE", "FCLOSE"}.issubset(df.columns):
        logger.warning("%s: 东财返回缺少 DATE/FCLOSE 字段: %s",
                       code, sorted(df.columns))
        return None
    out = pd.DataFrame({
        "date": pd.to_datetime(df["DATE"], errors="coerce").dt.strftime("%Y-%m-%d"),
        "close": pd.to_numeric(df["FCLOSE"], errors="coerce"),
        "premium_rate": pd.to_numeric(
            df.get("SWAPOR", pd.Series(index=df.index, dtype=float)),
            errors="coerce"),
    })
    return out.dropna(subset=["date", "close"])


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="增量更新 PIT raw 缓存尾部")
    parser.add_argument("--raw-dir", default=str(_RAW_DIR))
    parser.add_argument("--throttle", type=float, default=0.8, help="逐券间隔秒")
    parser.add_argument("--limit", type=int, default=None, help="最多更新 N 只（调试用）")
    parser.add_argument("--symbols", default="",
                        help="优先更新的券（逗号分隔）——不传则自动读上一期 current_plan.json 目标")
    parser.add_argument("--time-budget", type=int, default=None,
                        help="墙钟预算（秒），超时优雅退出——增量断点安全，剩余券下一轮继续")
    parser.add_argument("--state-file", default="",
                        help=f"滚动游标文件（默认 raw_dir/{_CURSOR_FILE}）")
    parser.add_argument("--active-only", action="store_true",
                        help="只刷新活跃券（缓存末日在 120 天内），跳过退市/长期停牌券")
    args = parser.parse_args(argv)
    run_deadline = (time.monotonic() + args.time_budget) if args.time_budget else None

    raw_dir = Path(args.raw_dir)
    pit_dir = raw_dir.parent
    if not pit_lock.acquire(pit_dir):
        logger.info("另一个 PIT 更新/装配进程运行中，跳过本次增量")
        return 0
    try:
        state_path = Path(args.state_file) if args.state_file else raw_dir / _CURSOR_FILE
        updated, skipped_current, failed = 0, 0, 0

        # 优先队列：当前目标债必须当日有价，否则导出的 t1_close=0，
        # dsa 操作单全线退化为"缺 T-1 收盘价人工处理"。滚动 limit 不保证
        # 覆盖目标债，所以把上一期契约目标排到队首（limit 只约束其余券）。
        priority_symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if not priority_symbols:
            plan_path = Path(__file__).resolve().parent.parent / "output" / "paper_tracking" / "current_plan.json"
            try:
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                priority_symbols = [
                    str(s).upper() for s, payload in (plan.get("targets") or {}).items()
                    if payload and float(payload.get("weight") or 0) > 0
                ]
            except (OSError, ValueError, AttributeError, TypeError):
                priority_symbols = []

        records = []
        for sym_dir in sorted(d for d in raw_dir.iterdir() if d.is_dir()):
            source = ""
            try:
                source = str(json.loads(
                    (sym_dir / "meta.json").read_text(encoding="utf-8")).get("source") or "")
            except (OSError, ValueError, AttributeError):
                pass
            records.append((sym_dir, source, _last_csv_date(sym_dir)))

        priority_set = set(priority_symbols)
        priority = [r for r in records if r[0].name in priority_set]
        rest = [r for r in records if r[0].name not in priority_set]
        # 活跃券优先且按"最旧缓存在前"排序：每轮刷新 N 只后它们自然移到队尾，
        # 下一轮接力，不会像固定 sorted() 那样永久卡在同一批僵尸券上。
        active_cutoff = (date.today() - timedelta(days=_ACTIVE_WINDOW_DAYS)).isoformat()
        active = [r for r in rest
                  if r[1] == "akshare" and r[2] and r[2] >= active_cutoff]
        stale = [r for r in rest if r not in active]
        active.sort(key=lambda r: (r[2], r[0].name))
        stale.sort(key=lambda r: (r[2], r[0].name))
        tail = active if args.active_only else active + stale

        # 滚动游标：上一轮中断/limit 停下的位置之后继续；全量跑完则回到队首。
        cursor = _load_cursor(state_path)
        if cursor and tail:
            names = [r[0].name for r in tail]
            start = names.index(cursor) + 1 if cursor in names else 0
            if start >= len(tail):
                start = 0
            tail = tail[start:] + tail[:start]
            logger.info("增量游标 %s → 本轮从 %s 开始", cursor, tail[0][0].name)
        sym_dirs = [r[0] for r in priority + tail]
        if priority:
            logger.info("优先更新 %d 只: %s",
                        len(priority), ",".join(r[0].name for r in priority))
        logger.info("raw 缓存 %d 只（活跃 %d，陈旧 %d，优先 %d%s）",
                    len(sym_dirs), len(active), len(stale), len(priority),
                    "，仅活跃" if args.active_only else "")

        non_priority_processed = 0
        last_processed = ""
        for i, sym_dir in enumerate(sym_dirs, 1):
            if run_deadline is not None and time.monotonic() >= run_deadline:
                logger.info("时间预算 %ds 用尽——优雅退出（已处理 %d 只，剩余下一轮继续）",
                            args.time_budget, i - 1)
                break
            if args.limit and non_priority_processed >= args.limit:
                break
            symbol = sym_dir.name
            is_priority = symbol in priority_set
            try:
                meta_path = sym_dir / "meta.json"
                if not meta_path.exists():
                    continue
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("source") != "akshare":
                    continue  # iFinD 缓存不动（沿用 backfill 的来源保护语义）

                code = symbol.split(".")[0]
                year_files = sorted(sym_dir.glob("????.csv"))
                if not year_files:
                    continue
                latest_file = year_files[-1]
                try:
                    latest_df = pd.read_csv(latest_file)
                    last_date = str(latest_df["date"].iloc[-1])
                except Exception as exc:  # noqa: BLE001
                    logger.warning("%s: 读取失败 %s", symbol, exc)
                    failed += 1
                    continue

                hist = _fetch_history(code)
                if hist is None:
                    failed += 1
                    continue
                new_rows = hist[hist["date"] > last_date]
                if new_rows.empty:
                    skipped_current += 1
                else:
                    for _, row in new_rows.iterrows():
                        year = str(row["date"])[:4]
                        year_file = sym_dir / f"{year}.csv"
                        if year_file.exists():
                            existing = pd.read_csv(year_file)
                            if row["date"] in existing["date"].values:
                                continue
                            row_df = pd.DataFrame([row])
                            combined = pd.concat([existing, row_df], ignore_index=True)
                            combined = combined.drop_duplicates(subset="date", keep="last").sort_values("date")
                            combined.to_csv(year_file, index=False)
                        else:
                            row_df = pd.DataFrame([row])
                            row_df.to_csv(year_file, index=False)
                    updated += 1
                    new_count = len(new_rows)
                    logger.info("%s: +%d 行（%s → %s）", symbol, new_count,
                                last_date, str(new_rows["date"].iloc[-1]))

                if i % 50 == 0:
                    logger.info("进度 %d/%d（更新 %d, 已最新 %d, 失败 %d）",
                                i, len(sym_dirs), updated, skipped_current, failed)
                time.sleep(args.throttle)
            finally:
                if not is_priority:
                    last_processed = symbol
                    non_priority_processed += 1

        if last_processed:
            _save_cursor(state_path, last_processed, non_priority_processed)
            logger.info("增量游标已推进至 %s（本轮滚动 %d 只）",
                        last_processed, non_priority_processed)
        logger.info("完成: 更新 %d, 已最新 %d, 失败 %d", updated, skipped_current, failed)
        # 管线友好：只要有一只成功（或无需更新）就 rc=0；
        # 全部失败才 rc=1（让 daily_pipeline 判定步骤失败并告警）
        if run_deadline is not None and time.monotonic() >= run_deadline:
            return 0  # 预算退出 = 部分进度有效，不算失败
        if updated == 0 and failed > 0:
            return 1
        return 0
    finally:
        pit_lock.release(pit_dir)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    sys.exit(main())
