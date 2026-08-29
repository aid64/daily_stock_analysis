# -*- coding: utf-8 -*-
"""从 AKShare 补采可转债 PIT 原始缓存（退市券优先路径，iFinD 兜底）。

背景：iFinD 的 fetch_convertible_meta 对 41 只券持续失败（missing_meta），
AKShare 实证可 100% 覆盖（bond_zh_cov_value_analysis 含已退市券的
日频 收盘价/转股溢价率，2019 起，溢价率为百分数值口径与 PIT 一致）。

产物与 data/pit/raw/ 现有 iFinD 缓存同构：
    <symbol>/<year>.csv   列 date,close,premium_rate（close 缺失写空）
    <symbol>/meta.json    list_date + ratings + source=akshare 标记

补采完成后用 build_pit_snapshot.py --assemble-only 离线重组 PIT。

用法：
    py scripts/backfill_cb_from_akshare.py                    # 自动读 coverage.json 缺失清单
    py scripts/backfill_cb_from_akshare.py --symbols 110052.SH,123020.SZ
    py scripts/backfill_cb_from_akshare.py --force            # 覆盖已有 akshare 缓存
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logger = logging.getLogger("backfill_cb_ak")

_RAW_DIR = _PROJECT_ROOT / "data" / "pit" / "raw"
_COVERAGE = _PROJECT_ROOT / "data" / "pit" / "coverage.json"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="AKShare 补采转债 PIT 原始缓存")
    p.add_argument("--symbols", default=None, help="逗号分隔，覆盖默认的 coverage.json 缺失清单")
    p.add_argument(
        "--universe-file",
        default=None,
        help="universe 文件（行=代码或 csv）：只采集其中 raw 缓存缺失的券",
    )
    p.add_argument("--raw-dir", default=None, help="缓存目录，默认 data/pit/raw")
    p.add_argument("--force", action="store_true", help="覆盖已有 akshare 来源缓存")
    p.add_argument("--throttle", type=float, default=1.2, help="逐券请求间隔秒数")
    p.add_argument(
        "--enrich-only",
        action="store_true",
        help="只补全 raw/*/meta.json 的静态字段（list_date 修正为上市时间、"
        "maturity_date、issue_size），不拉行情不动 CSV；对 iFinD 来源仅补缺不覆盖",
    )
    return p


def _enrich_meta(raw_dir: Path) -> int:
    """补全全部 raw 目录 meta.json 的静态生命周期字段。

    数据源与字段（2026-08-18 实证）：
      - bond_zh_cov（全量发行表 1050 只，含退市）：上市时间 / 申购日期 / 发行规模
      - bond_cb_jsl（集思录子集 ~30 只）：到期时间 / 剩余规模（真值，命中则优先）
      - 到期日近似：可转债期限基本为 6 年 → 申购日期 + 6 年（误差天数对
        "临期 1 年排除"规则无实质影响，maturity_note 标记来源）

    规则：akshare 来源的 list_date 由"数据首日"修正为真实上市时间；
    iFinD 来源的 list_date/ratings 保持原值，仅补 maturity_date/issue_size。
    """
    import akshare as ak

    try:
        cov_df = ak.bond_zh_cov()
    except Exception as e:  # noqa: BLE001
        logger.error(f"bond_zh_cov 拉取失败，无法 enrich: {e}")
        return 1
    bzc = {}
    for _, row in cov_df.iterrows():
        code = str(row.get("债券代码", "")).strip()
        if code:
            bzc[code] = row

    jsl = {}
    try:
        jsl_df = ak.bond_cb_jsl()
        for _, row in jsl_df.iterrows():
            code = str(row.get("代码", "")).strip()
            if code:
                jsl[code] = row
        logger.info(f"bond_cb_jsl {len(jsl)} 只（到期/剩余规模真值候选）")
    except Exception as e:  # noqa: BLE001 - jsl 为增强信息，失败降级
        logger.warning(f"bond_cb_jsl 拉取失败（到期日将用近似）: {e}")

    fixed_list, added_mat, added_size = 0, 0, 0
    for sym_dir in sorted(p for p in raw_dir.iterdir() if p.is_dir()):
        meta_path = sym_dir / "meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        code = sym_dir.name.split(".")[0]
        info = bzc.get(code)
        if info is None:
            logger.warning(f"{sym_dir.name}: 不在 bond_zh_cov，跳过 enrich")
            continue

        # list_date：仅 akshare 来源修正为真实上市时间
        if meta.get("source") == "akshare":
            listing = str(info.get("上市时间", "") or "").strip()
            if listing and listing != meta.get("list_date"):
                meta["list_date"] = listing
                fixed_list += 1

        # maturity_date：jsl 真值优先，否则 申购日期 + 6 年
        if not meta.get("maturity_date"):
            jrow = jsl.get(code)
            sub = str(info.get("申购日期", "") or "").strip()
            if jrow is not None and str(jrow.get("到期时间", "") or "").strip():
                meta["maturity_date"] = str(jrow["到期时间"]).strip()
                meta["maturity_note"] = "jsl"
                added_mat += 1
            elif sub:
                dt = pd.Timestamp(sub) + pd.DateOffset(years=6)
                meta["maturity_date"] = dt.strftime("%Y-%m-%d")
                meta["maturity_note"] = "approx_subscribe_plus_6y"
                added_mat += 1

        # issue_size：发行规模（静态；剩余规模历史免费源不可得，B 阶段用此近似）
        if not meta.get("issue_size"):
            size = info.get("发行规模")
            if size is not None and not (isinstance(size, float) and pd.isna(size)):
                meta["issue_size"] = float(size)
                added_size += 1

        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    logger.info(
        f"enrich 完成: list_date 修正 {fixed_list}, maturity_date 补全 {added_mat}, "
        f"issue_size 补全 {added_size}（共 {len(list(raw_dir.iterdir()))} 目录）"
    )
    logger.info("下一步: py scripts/build_pit_snapshot.py --assemble-only 重组 PIT")
    return 0


def _load_rating_map() -> dict[str, dict]:
    """bond_zh_cov（全量发行表，含已退市）→ 代码级 评级/申购日期/发行规模 映射。

    单次拉取失败不阻断补采（评级缺失时写空 ratings，策略层宽容处理）。
    """
    import akshare as ak

    try:
        df = ak.bond_zh_cov()
    except Exception as e:  # noqa: BLE001 - 评级是增强信息，失败降级为空
        logger.warning(f"bond_zh_cov 拉取失败（评级将缺失）: {e}")
        return {}
    out: dict[str, dict] = {}
    for _, row in df.iterrows():
        code = str(row.get("债券代码", "")).strip()
        if not code:
            continue
        out[code] = {
            "rating": str(row.get("信用评级", "") or "").strip() or None,
            "subscribe_date": str(row.get("申购日期", "") or "").strip() or None,
            "listing_date": str(row.get("上市时间", "") or "").strip() or None,
            "issue_size": row.get("发行规模"),
        }
    logger.info(f"bond_zh_cov 映射 {len(out)} 只（含历史发行）")
    return out


def _fetch_history(code: str) -> Optional[pd.DataFrame]:
    """bond_zh_cov_value_analysis：日频 收盘价/纯债价值/转股价值/溢价率。

    返回标准化 DataFrame（date/close/premium_rate），失败返回 None。
    """
    import akshare as ak

    for attempt in range(1, 4):
        try:
            df = ak.bond_zh_cov_value_analysis(symbol=code)
            if df is None or len(df) == 0:
                return None
            std = pd.DataFrame(
                {
                    "date": pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d"),
                    "close": pd.to_numeric(df["收盘价"], errors="coerce"),
                    "premium_rate": pd.to_numeric(df["转股溢价率"], errors="coerce"),
                }
            ).sort_values("date")
            return std
        except Exception as e:  # noqa: BLE001 - 东财偶发断连，重试
            logger.warning(f"{code} 第 {attempt} 次失败: {e}")
            time.sleep(2.0)
    return None


def _write_symbol_cache(
    raw_dir: Path,
    symbol: str,
    hist: pd.DataFrame,
    meta_info: dict,
    force: bool,
) -> None:
    sym_dir = raw_dir / symbol
    meta_path = sym_dir / "meta.json"
    if meta_path.exists():
        existing = json.loads(meta_path.read_text(encoding="utf-8"))
        if existing.get("source") == "akshare" and not force:
            logger.info(f"{symbol}: 已有 akshare 缓存，跳过")
            return
        if existing.get("source") != "akshare":
            logger.warning(f"{symbol}: 存在非 akshare 缓存（iFinD），不覆盖")
            return

    sym_dir.mkdir(parents=True, exist_ok=True)
    for year, g in hist.groupby(hist["date"].str[:4]):
        out = g[["date", "close", "premium_rate"]]
        out.to_csv(sym_dir / f"{year}.csv", index=False)

    first_day = hist["date"].iloc[0]
    rating = meta_info.get("rating")
    # 上市时间真值优先（bond_zh_cov 上市时间列），缺失时退回数据首日近似
    list_date = meta_info.get("listing_date") or first_day
    meta = {
        "list_date": list_date,
        "ratings": (
            [{"eff_date": list_date, "rating": rating}] if rating else []
        ),  # 当前评级静态近似（iFinD meta 同为稀疏事件结构）
        "source": "akshare",
        "rating_note": "bond_zh_cov 当前评级，静态近似" if rating else "",
        "issue_size": meta_info.get("issue_size"),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    logger.info(
        f"{symbol}: {len(hist)} 行写入 {sym_dir.name}/ "
        f"({first_day} ~ {hist['date'].iloc[-1]}, rating={rating or '无'})"
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = build_parser().parse_args(argv)
    raw_dir = Path(args.raw_dir) if args.raw_dir else _RAW_DIR

    if args.enrich_only:
        return _enrich_meta(raw_dir)

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    elif args.universe_file:
        uf = Path(args.universe_file)
        if not uf.exists():
            logger.error(f"universe 文件不存在: {uf}")
            return 2
        wanted: list[str] = []
        for line in uf.read_text(encoding="utf-8").splitlines():
            tok = line.strip()
            if not tok or tok.startswith("#"):
                continue
            # csv 格式（symbol,...）取首列
            sym = tok.split(",")[0].strip().upper()
            if sym:
                wanted.append(sym)
        # 只采集 raw 缓存缺失的（已缓存的跳过，天然断点续跑）
        symbols = [s for s in wanted if not (raw_dir / s / "meta.json").exists()]
        logger.info(f"universe {len(wanted)} 只，其中待采集 {len(symbols)} 只")
    else:
        if not _COVERAGE.exists():
            logger.error(f"coverage.json 不存在: {_COVERAGE}")
            return 2
        cov = json.loads(_COVERAGE.read_text(encoding="utf-8"))
        symbols = sorted(cov.get("missing_meta_symbols", []))
    if not symbols:
        logger.info("无待补采标的")
        return 0

    logger.info(f"待补采 {len(symbols)} 只 → {raw_dir}")
    rating_map = _load_rating_map()

    ok, fail = [], []
    for full in symbols:
        code = full.split(".")[0]
        hist = _fetch_history(code)
        if hist is None:
            logger.error(f"{full}: 行情拉取失败（重试耗尽）")
            fail.append(full)
            continue
        try:
            _write_symbol_cache(raw_dir, full, hist, rating_map.get(code, {}), args.force)
            ok.append(full)
        except Exception as e:  # noqa: BLE001 - 单券写入失败不拖垮批次
            logger.error(f"{full}: 缓存写入失败: {e}")
            fail.append(full)
        time.sleep(args.throttle)

    logger.info(f"完成: 成功 {len(ok)} / 失败 {len(fail)}")
    if fail:
        logger.warning(f"失败清单（重跑本脚本自动续传）: {fail}")
        return 1
    logger.info("下一步: py scripts/build_pit_snapshot.py --assemble-only 重组 PIT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
