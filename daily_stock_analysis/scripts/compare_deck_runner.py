# -*- coding: utf-8 -*-
"""多策略对比盘确定性执行器（策略对比盘的唯一执行引擎）。

取代 ZCode 定时提示词的"AI 解释执行"：本脚本是普通 Python 程序，
由 Windows 计划任务 / main.py --schedule 触发，每次运行做且只做
模式规定的事，结果原子写台账，动作发飞书。所有决策都是确定性
函数，可单元测试、可审计、幂等。

模式：
  --mode intraday  盘中风控轮：事件套利扫描+核价差、熔断、信用、异动
  --mode close     收盘轮：净值重估 + 周一/月度调仓 + 收盘小结
  --dry-run        只计算与打印，不写台账、不发通知、不执行调仓

数据源契约：
  - 实时行情：data_provider.get_realtime_quote（六源降级+熔断，用户配置）
  - 小市值选股：tushare daily_basic/stock_basic（15000 积分接口）
  - A/B 信号：只读 quant_research/output/feeds_snapshot_v*.pkl（跨项目文件契约，不 import）
  - E 目标：子进程调 quant_research/scripts/run_paper_tracking.py --export-plan
  - 事件公告：EventArbitrageMonitorService + 东财公告正文 API
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import pandas as pd
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib import request as urlrequest

PROJECT_DIR = Path(__file__).resolve().parents[1]
LEDGER = PROJECT_DIR.parent / ".scratch" / "dsa-strategy-validation" / "compare_strategies.json"
CAPITAL = 12411.0
EX_DATE = date.today()  # 交易所日历日（main 启动时用行情时间戳校准；本机时钟不可信）
COMM_RATE, COMM_MIN = 0.0000845, 5.0
STAMP_SELL = 0.0005  # 股票卖出印花税（ETF 免）

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("compare_deck")

sys.path.insert(0, str(PROJECT_DIR))
os.chdir(PROJECT_DIR)  # .env / data/ 均以项目目录为根


# ---------------------------------------------------------------- 基础设施
_LOCK_PATH = PROJECT_DIR / "data" / "cache" / "compare_deck.lock"


def acquire_lock() -> bool:
    """O_EXCL 独占锁；超过 15 分钟的残留视为死锁清除（本机时钟不可信，阈值放宽）。"""
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(datetime.now()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            age = datetime.now() - datetime.fromtimestamp(_LOCK_PATH.stat().st_mtime)
            if age > timedelta(minutes=15):
                log.warning("锁超过15分钟视为残留, 强制清除")
                _LOCK_PATH.unlink()
                return acquire_lock()
        except Exception as exc:
            log.warning("锁清理失败: %r", exc)
        return False


def release_lock() -> None:
    try:
        _LOCK_PATH.unlink()
    except FileNotFoundError:
        pass


def load_ledger() -> dict:
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def save_ledger(led: dict) -> None:
    """原子写：临时文件 + os.replace；写前滚动保留一份 .bak（台账在 .scratch，防误删）。"""
    if not LEDGER.parent.exists():
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
    if LEDGER.exists():
        try:
            shutil.copyfile(LEDGER, LEDGER.with_suffix(".json.bak"))
        except Exception as exc:
            log.warning("台账备份失败(不影响主写): %r", exc)
    fd, tmp = tempfile.mkstemp(dir=str(LEDGER.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(led, f, ensure_ascii=False, indent=2)
        os.replace(tmp, LEDGER)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def notify(content: str, dedup_key: str, dry: bool = False) -> bool:
    if dry:
        log.info("[dry-run] 通知抑制: %s", dedup_key)
        return False
    try:
        from src.notification import NotificationService
        return NotificationService().send(content, route_type="strategy_compare", dedup_key=dedup_key)
    except Exception as exc:  # 通知失败不阻断主流程
        log.warning("通知发送失败(%s): %r", dedup_key, exc)
        return False


def _chunks(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def realtime_quotes(codes: list[str]) -> dict[str, dict]:
    """走项目实时行情链（六源降级+熔断）。先分批预热（tickflow 单批上限5），再并发逐只取缓存。"""
    out: dict[str, dict] = {c: {"price": 0.0, "pct": 0.0, "source": "unavailable"} for c in codes}
    try:
        from data_provider.base import DataFetcherManager
        from concurrent.futures import ThreadPoolExecutor
        mgr = DataFetcherManager()
        for chunk in _chunks(codes, 5):
            try:
                mgr.prefetch_realtime_quotes(chunk)
            except Exception as exc:
                log.warning("prefetch 失败(降级逐只) %s: %r", chunk, exc)

        def _one(code: str) -> tuple[str, dict]:
            try:
                q = mgr.get_realtime_quote(code, log_final_failure=False)
                if q is None or getattr(q, "price", None) in (None, 0):
                    return code, {"price": 0.0, "pct": 0.0, "source": "unavailable"}
                return code, {"price": float(q.price), "pct": float(q.change_pct or 0.0),
                              "source": getattr(q.source, "value", str(q.source)),
                              "ts": getattr(q, "provider_timestamp", None)}
            except Exception as exc:
                log.warning("行情失败 %s: %r", code, exc)
                return code, {"price": 0.0, "pct": 0.0, "source": "error"}

        with ThreadPoolExecutor(max_workers=4) as pool:
            for code, info in pool.map(_one, codes):
                out[code] = info
    except Exception as exc:
        log.error("实时行情链整体失败: %r", exc)
    return out


def is_trading_session(now: datetime | None = None) -> bool:
    """A股交易时段（含 15:00-15:10 收盘处理窗）。午休/开盘前/周末为 False。"""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 100 + now.minute
    return (930 <= hm <= 1130) or (1300 <= hm <= 1510)


def exchange_now() -> datetime | None:
    """用交易所行情时间戳作为真实时间基准（本机时钟不可信时唯一可靠来源）。"""
    try:
        raw = urlrequest.urlopen(urlrequest.Request(
            "http://qt.gtimg.cn/q=sh512890", headers={"User-Agent": "Mozilla/5.0"}),
            timeout=8).read().decode("gbk")
        ts = raw.split('="')[1].rstrip('";').split("~")[30]
        return datetime.strptime(ts[:14], "%Y%m%d%H%M%S")
    except Exception as exc:
        log.warning("交易所时间戳获取失败, 回退本机时钟: %r", exc)
        return None


def slot_prices(led: dict, extra: list[str] | None = None) -> dict[str, dict]:
    codes: list[str] = []
    for s in led["strategies"].values():
        for h in s.get("holds", []):
            codes.append(h["symbol"])
    codes += list(extra or [])
    return realtime_quotes(list(dict.fromkeys(codes)))


# ---------------------------------------------------------------- 交易原语
def trade_fee(notional: float, side: str, is_stock: bool) -> float:
    fee = max(notional * COMM_RATE, COMM_MIN)
    if side == "SELL" and is_stock:
        fee += notional * STAMP_SELL
    return round(fee, 2)


def apply_orders(slot: dict, sells: list[dict], buys: list[dict], px: dict) -> None:
    """先卖后买。sells/buys: {symbol,name,qty,price,is_stock}。现金不足的买入跳过。"""
    for o in sells:
        notional = o["qty"] * o["price"]
        fee = trade_fee(notional, "SELL", o.get("is_stock", False))
        slot["cash"] = round(slot.get("cash", 0) + notional - fee, 2)
        slot["fees_paid"] = round(slot.get("fees_paid", 0.0) + fee, 2)
        remaining = []
        for h in slot.get("holds", []):
            if h["symbol"] == o["symbol"]:
                left = h["qty"] - o["qty"]
                if left > 0:  # 部分减仓: 扣量保留, 成本价不变
                    h["qty"] = left
                    h["notional"] = round(left * h["price"], 2)
                    remaining.append(h)
                # left==0 → 移除
            else:
                remaining.append(h)
        slot["holds"] = remaining
        log.info("SELL %s %s x%s @%s fee=%s", o["symbol"], o.get("name", ""), o["qty"], o["price"], fee)
    for b in buys:
        notional = b["qty"] * b["price"]
        fee = trade_fee(notional, "BUY", b.get("is_stock", False))
        if notional + fee > slot.get("cash", 0):
            log.warning("BUY 跳过(现金不足) %s 需¥%.2f 现金¥%.2f", b["symbol"], notional + fee, slot["cash"])
            continue
        slot["cash"] = round(slot["cash"] - notional - fee, 2)
        slot["fees_paid"] = round(slot.get("fees_paid", 0.0) + fee, 2)
        slot["holds"].append({"symbol": b["symbol"], "name": b.get("name", ""), "qty": b["qty"],
                              "price": b["price"], "notional": round(notional, 2), "fee": fee})
        log.info("BUY %s %s x%s @%s fee=%s", b["symbol"], b.get("name", ""), b["qty"], b["price"], fee)


def dividend_accrued(ts_code: str, since: str, spot: float, qty: int) -> tuple[float, str]:
    """tushare fund_div 查询 since 之后的基金分红, 返回 (应计现金, 描述)。失败返回 (0.0, "")。"""
    try:
        import tushare as ts
        token = ""
        for line in (PROJECT_DIR / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("TUSHARE_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
        df = ts.pro_api(token).fund_div(ts_code=ts_code)
        if df is None or df.empty:
            return 0.0, ""
        df = df[df["ex_date"].astype(str) > since]
        total, detail = 0.0, []
        for _, row in df.iterrows():
            unit = float(row.get("div_cash") or 0)  # 每份分红(元)
            if unit > 0:
                total += unit * qty
                detail.append(f"{row.get('ex_date')} 每份{unit}元")
        if total > 0:
            return round(total, 2), "; ".join(detail)
        return 0.0, ""
    except Exception as exc:
        log.warning("分红查询失败 %s: %r", ts_code, exc)
        return 0.0, ""


def revalue(slot: dict, px: dict) -> float:
    eq = slot.get("cash", 0.0)
    for h in slot.get("holds", []):
        if h.get("fixed_value"):  # 逆回购等面值计价品种, 行情价不适用
            eq += h["notional"]
            continue
        p = px.get(h["symbol"], {}).get("price") or h.get("price", 0)
        eq += h["qty"] * p
    slot["equity"] = round(eq, 2)
    return slot["equity"]


# ---------------------------------------------------------------- 信号计算
def maybe_refresh_snapshot(dry: bool) -> str:
    """A/B 信号的历史快照超过 7 天未更新时，用 quant_research 数据链重建（tushare 优先）。

    失败时保留旧快照照常出信号（口径略陈旧，日志+消息标注）。"""
    out_dir = PROJECT_DIR.parent / "quant_research" / "output"
    snaps = sorted(out_dir.glob("feeds_snapshot_v*.pkl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if snaps and (datetime.now() - datetime.fromtimestamp(snaps[0].stat().st_mtime)).days < 7:
        return ""
    target = snaps[0].with_name("feeds_snapshot_v5.pkl") if snaps else out_dir / "feeds_snapshot_v5.pkl"
    if dry:
        return f"[dry] 快照已过期, 将重建→{target.name}"
    try:
        start = (EX_DATE - __import__("datetime").timedelta(days=450)).isoformat()
        subprocess.run([sys.executable, str(PROJECT_DIR.parent / "quant_research" / "scripts" / "build_feeds_snapshot.py"),
                        "--start", start, "--output", str(target)],
                       cwd=str(PROJECT_DIR.parent / "quant_research"), capture_output=True, text=True, timeout=900)
        if target.exists():
            log.info("快照已刷新: %s", target.name)
            return f"快照已刷新({target.name})"
        log.error("快照重建后未找到输出 %s", target)
        return "【异常】快照重建失败(信号沿用旧快照)"
    except Exception as exc:
        log.error("快照重建失败: %r", exc)
        return "【异常】快照重建失败(信号沿用旧快照)"


def _load_snapshot():
    import glob
    import pandas as pd
    snaps = sorted(glob.glob(str(PROJECT_DIR.parent / "quant_research" / "output" / "feeds_snapshot_v*.pkl")),
                   key=os.path.getmtime, reverse=True)
    if not snaps:
        return None
    return pd.read_pickle(snaps[0])


def pe_percentile(history: list, current: float, window_years: int = 10) -> float:
    """纯函数: 当前 PE 在近 N 年历史中的分位(0-100, 越低越便宜); 样本不足返回 -1。"""
    vals = [float(v) for v in history if v and float(v) > 0]
    if len(vals) < 250 or not current:
        return -1.0
    cutoff = vals[-250 * window_years:] if len(vals) > 250 * window_years else vals
    below = sum(1 for v in cutoff if v <= current)
    return round(below / len(cutoff) * 100, 1)


def erp_decision(percentile: float, state: str) -> str:
    """纯函数: PE分位+当前状态 → 动作。滞回阈值: >80 进防御, <60 退出(防震荡)。"""
    if percentile < 0:
        return "no_data"
    if state != "defensive" and percentile > 80:
        return "enter_defensive"
    if state == "defensive" and percentile < 60:
        return "exit_defensive"
    return "hold"


def pd_median(vals: list) -> float:
    vals = sorted(vals)
    n = len(vals)
    if not n:
        return 0.0
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2


def e2_gate_decision(median: float, state: str) -> str:
    """纯函数: 转债中位数+E2状态 → 动作。锚点插值阈值: ≥127(≈80%分位)减半, <118(≈65%)恢复。

    锚点(120.87→68.3% 的实测族): 122.87@68.3%, 130.05@~85%(2026-09-30), 133.77@92.4%。
    阈值属先验插值, 未回测校准——已知局限清单第 6 条。"""
    if not median:
        return "no_data"
    if state != "defensive" and median >= 127.0:
        return "enter_defensive"
    if state == "defensive" and median < 118.0:
        return "exit_defensive"
    return "hold"


def select_with_buffer(holds_syms: list, ranked_syms: list, buffer: int, slots: int):
    """纯函数: 缓冲带选股——持仓排名未跌出 buffer 位则保留, 空位按排名补新。返回 (keep, new)。"""
    rank = {s: i + 1 for i, s in enumerate(ranked_syms)}
    keep = [s for s in holds_syms if rank.get(s, 10 ** 9) <= buffer][:slots]
    new = [s for s in ranked_syms if s not in keep][:max(slots - len(keep), 0)]
    return keep, new


def _parse_engine_stdout(stdout: str) -> dict:
    """子进程 stdout 可能含 tushare login 等噪声——提取最后一个完整 JSON 对象。"""
    out = (stdout or "").strip()
    start = out.rfind('}\n{')  # JSON 主体前若有噪声行, 取最后一个 '{' 块
    if start >= 0:
        start += 1
    else:
        start = out.find("{")
    if start < 0:
        raise ValueError(f"引擎输出无 JSON: {out[:80]!r}")
    return json.loads(out[start:])


def momentum_target_from_engine(data: dict) -> dict[str, float]:
    """纯函数：引擎 JSON → 目标权重。防御态满仓 511010；否则 top_k 等权。可测。"""
    if data.get("defensive_mode"):
        return {"511010.SH": 1.0}
    ranked = [x["symbol"] for x in (data.get("ranked") or [])][: int(data.get("top_k", 3))]
    return {s: 1.0 / len(ranked) for s in ranked} if ranked else {}


def momentum_signal(led: dict, px: dict, dry: bool) -> list[dict]:
    """A_动量：引擎口径（extract_mwu_targets.py 探针提取 ranked/defensive_mode）。

    微调保留：货币腿 511880 权重以现金持有（一手 ¥1 万+，功能等价现金）。
    """
    slot = led["strategies"]["A_动量"]
    if dry:
        return ["A_动量(dry) 引擎信号提取"]
    try:
        r = subprocess.run(
            [sys.executable, str(PROJECT_DIR.parent / "quant_research" / "scripts" / "extract_mwu_targets.py"),
             "--strategy", "etf_momentum", "--strategy-config", "config/strategy/etf_momentum.yaml",
             "--config", "config/default.yaml"],
            cwd=str(PROJECT_DIR.parent / "quant_research"), capture_output=True, text=True, timeout=600)
        data = _parse_engine_stdout(r.stdout)
        target = momentum_target_from_engine(data)
        if not target:
            return ["【异常】A_动量 引擎输出为空, 持仓不变"]
        cur = {h["symbol"]: h["qty"] for h in slot.get("holds", [])}
        prices = {sym: (px.get(sym, {}).get("price") or 0) for sym in set(target) | set(cur)}
        sells, buys = mwu_target_orders(target, cur, prices)
        buys = [b for b in buys if b["symbol"] != "511880.SH"]  # 货币腿以现金持有(微调)
        for o in sells + buys:
            o["is_stock"] = False
        changed = bool(sells or buys)
        now = exchange_now()
        tradable = bool(now) and is_trading_session(now)
        if changed and not tradable:
            log.warning("非交易时段(交易所 %s), A 槽调仓抑制为只记录", now)
        if changed and tradable:
            apply_orders(slot, sells, buys, px)
        slot["signal"] = {"as_of": data.get("as_of"), "ranked": data.get("ranked"),
                          "defensive_mode": data.get("defensive_mode"), "target": target}
        slot["note"] = f"引擎信号 {data.get('as_of')} defensive={data.get('defensive_mode')}"
        tag = f"调仓→{sorted(target)}" if changed else f"目标未变(as_of {data.get('as_of')})"
        return [f"A_动量 {tag}"]
    except Exception as exc:
        log.error("A 动量引擎信号失败: %r", exc)
        return ["【异常】A_动量 引擎信号提取失败, 持仓不变"]


def rotation_signal(led: dict, px: dict, dry: bool) -> list[dict]:
    """B_轮动：核心3资产25日收益选最强，全负→空仓。"""
    feeds = _load_snapshot()
    if feeds is None:
        return []
    pool = ["518880.SH", "159915.SZ", "510300.SH"]
    scores = {}
    for sym in pool:
        close = feeds[sym].sort_values("date")["close"].values
        spot = px.get(sym, {}).get("price") or float(close[-1])
        scores[sym] = spot / float(close[-26]) - 1
    best = max(scores, key=scores.get)
    slot = led["strategies"]["B_轮动"]
    target = [] if scores[best] <= 0 else [best]
    current = {h["symbol"] for h in slot.get("holds", [])}
    if set(target) == current:
        return []
    sells = [{"symbol": h["symbol"], "qty": h["qty"], "price": px.get(h["symbol"], {}).get("price") or h["price"]}
             for h in slot.get("holds", []) if h["symbol"] not in target]
    buys = []
    if target:
        price = px.get(target[0], {}).get("price")
        qty = int(CAPITAL * 0.98 // (price * 100)) * 100
        if qty:
            buys.append({"symbol": target[0], "qty": qty, "price": price})
    if dry:
        return [f"B_轮动(dry) 25日收益{ {k: f'{v:+.1%}' for k, v in scores.items()} }→{target or '空仓'}"]
    apply_orders(slot, sells, buys, px)
    slot["note"] = f"信号 {datetime.now():%m-%d %H:%M} 25日{scores[best]:+.2%}→{target or '空仓'}"
    return [f"B_轮动 调仓→{target or '空仓'}"]


def smallcap_universe(month_end: str, exclude_new: bool, top: int) -> list[dict]:
    """tushare 15000 积分接口选小市值。返回候选（circ_mv 升序）。"""
    import tushare as ts
    token = ""
    for line in (PROJECT_DIR / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("TUSHARE_TOKEN="):
            token = line.split("=", 1)[1].strip().strip('"').strip("'")
    pro = ts.pro_api(token)
    db = pro.daily_basic(trade_date=month_end, fields="ts_code,circ_mv,close")
    sb = pro.stock_basic(list_status="L", fields="ts_code,name,list_date")
    df = db.merge(sb, on="ts_code", how="left").dropna(subset=["circ_mv", "name"])
    df = df[~df["name"].str.contains("ST", na=False)]
    df = df[~df["ts_code"].str.startswith(("8", "4", "9", "688"))]
    df = df[df["close"] > 0]
    if exclude_new:
        df["list_date"] = df["list_date"].astype(str)
        df = df[df["list_date"] <= (datetime.strptime(month_end, "%Y%m%d") - __import__("datetime").timedelta(days=365)).strftime("%Y%m%d")]
    df = df.sort_values("circ_mv").head(top * 2)
    return df.to_dict("records")


def smallcap_rebalance(led: dict, slot_name: str, slots: int, cap_per: float,
                       exclude_new: bool, px: dict, dry: bool) -> list[dict]:
    slot = led["strategies"][slot_name]
    month_end = _prev_month_end()
    recs = smallcap_universe(month_end, exclude_new, slots)
    main_board = ("000", "001", "002", "003", "600", "601", "603", "605")
    if "神帖" in slot_name:
        recs = [r for r in recs if r["ts_code"][:3] in main_board][:8]
    def tcode(ts_code: str) -> str:
        c, m = ts_code.split(".")
        return ("SH" if m == "SH" else "SZ") + c
    # 缓冲带: 持仓排名未跌出 buffer 位(2×slots)则保留, 空位按排名补新——月度换手减半以上
    rank = {tcode(r["ts_code"]): i + 1 for i, r in enumerate(recs)}
    buffer = slots * 2
    picks_syms = [h["symbol"] for h in slot.get("holds", []) if rank.get(h["symbol"], 10 ** 9) <= buffer][:slots]
    picks = []
    for r in recs:
        if len(picks_syms) >= slots:
            break
        sym = tcode(r["ts_code"])
        if sym in picks_syms:
            continue
        price = px.get(sym, {}).get("price")
        if not price or price * 100 > cap_per:
            continue
        picks_syms.append(sym)
        picks.append({"symbol": sym, "name": r["name"], "price": price, "qty": 100})
    sells = [{"symbol": h["symbol"], "qty": h["qty"], "price": px.get(h["symbol"], {}).get("price") or h["price"], "is_stock": True}
             for h in slot.get("holds", []) if h["symbol"] not in picks_syms]
    if dry:
        return [f"{slot_name}(dry) 目标{picks_syms}(缓冲带{buffer})"]
    apply_orders(slot, sells, [
        {"symbol": p["symbol"], "name": p["name"], "qty": p["qty"], "price": p["price"], "is_stock": True} for p in picks], px)
    slot["universe_rule"] = slot.get("universe_rule", "") + f" | 最后选股 {month_end}"
    return [f"{slot_name} 月度再平衡→{[p['symbol'] for p in picks] or '空仓'}"]


def pead_rebalance(led: dict, px: dict, dry: bool) -> list[str]:
    """I_PEAD: 月度窗重取候选(最近40天预增) → 与持仓 diff。无新预告维持旧仓。"""
    slot = led["strategies"].get("I_PEAD业绩预告")
    if not slot:
        return []
    if dry:
        return ["I_PEAD(dry) 候选重取"]
    try:
        cands = pead_universe()
        if not cands:
            slot["note"] = f"{EX_DATE} 无新预增候选, 维持旧仓"
            return [f"I_PEAD 无新预增候选, 维持旧仓"]
        picks = []
        for r in cands:
            sym = ("SH" if r["ts_code"].endswith(".SH") else "SZ") + r["ts_code"][:6]
            p = px.get(sym, {}).get("price") or 0
            if p and p * 100 <= 2500:
                picks.append({"symbol": sym, "name": names_of(cands).get(r["ts_code"], sym), "price": p, "qty": 100})
            if len(picks) >= 5:
                break
        sells = [{"symbol": h["symbol"], "qty": h["qty"], "price": px.get(h["symbol"], {}).get("price") or h.get("price", 0), "is_stock": True}
                 for h in slot.get("holds", []) if h["symbol"] not in {p["symbol"] for p in picks}]
        buys = [{"symbol": p["symbol"], "name": p["name"], "qty": p["qty"], "price": p["price"], "is_stock": True}
                for p in picks if p["symbol"] not in {h["symbol"] for h in slot.get("holds", [])}]
        apply_orders(slot, sells, buys, px)
        slot["note"] = f"{EX_DATE} 候选{len(cands)}→持仓{len(picks)}"
        return [f"I_PEAD 调仓→{[p['symbol'] for p in picks] or '空仓'}"]
    except Exception as exc:
        log.error("I_PEAD 调仓失败: %r", exc)
        return ["【异常】I_PEAD 候选重取失败, 持仓不变"]


def reversal_rebalance(led: dict, px: dict, dry: bool) -> list[str]:
    """J_反转: 月度窗重取 21 日跌幅最深候选 → 与持仓 diff。"""
    slot = led["strategies"].get("J_短期反转")
    if not slot:
        return []
    if dry:
        return ["J_反转(dry) 候选重取"]
    try:
        cands = reversal_universe()
        if not cands:
            return ["J_反转 无候选(全市场无合格超跌), 维持旧仓"]
        picks = []
        for r in cands:
            sym = ("SH" if r["ts_code"].endswith(".SH") else "SZ") + r["ts_code"][:6]
            p = px.get(sym, {}).get("price") or 0
            if p and p * 100 <= 2500:
                picks.append({"symbol": sym, "name": names_of(cands).get(r["ts_code"], sym),
                              "price": p, "qty": 100, "ret21": r["ret21"]})
            if len(picks) >= 5:
                break
        sells = [{"symbol": h["symbol"], "qty": h["qty"], "price": px.get(h["symbol"], {}).get("price") or h.get("price", 0), "is_stock": True}
                 for h in slot.get("holds", []) if h["symbol"] not in {p["symbol"] for p in picks}]
        buys = [{"symbol": p["symbol"], "name": p["name"], "qty": p["qty"], "price": p["price"], "is_stock": True}
                for p in picks if p["symbol"] not in {h["symbol"] for h in slot.get("holds", [])}]
        apply_orders(slot, sells, buys, px)
        slot["note"] = f"{EX_DATE} 21日跌幅最深候选: " + " ".join(f"{p['name']}{p['ret21']:+.0f}%" for p in picks[:3])
        return [f"J_反转 调仓→{[p['symbol'] for p in picks] or '空仓'}"]
    except Exception as exc:
        log.error("J_反转 调仓失败: %r", exc)
        return ["【异常】J_反转 候选重取失败, 持仓不变"]


def buywatch_universe(top: int = 5) -> list[dict]:
    """K_项目选股: 复用项目 buy_watchlist 漏斗+打分（东财快照缺列时用 tushare daily_basic 补列）。

    这是"用项目自带能力"的兑现——不做任何手搓因子。"""
    from src.services import market_scan_service as mss
    from src.services.buy_watchlist_service import build_buy_watchlist
    try:
        mss.fetch_and_store_snapshot()
    except Exception as exc:
        log.warning("东财快照刷新失败, 用现有快照+tushare补列: %r", exc)
    snap = mss.load_snapshot()
    merged = snap
    if snap is not None and snap.get("pe", pd.Series(dtype=float)).notna().sum() < 100:
        try:
            import tushare as ts
            token = ""
            for line in (PROJECT_DIR / ".env").read_text(encoding="utf-8").splitlines():
                if line.startswith("TUSHARE_TOKEN="):
                    token = line.split("=", 1)[1].strip().strip('"').strip("'")
            pro = ts.pro_api(token)
            db = None
            for back in range(0, 8):  # 休市日回退找最近有数据的交易日
                day = (EX_DATE - timedelta(days=back)).strftime("%Y%m%d")
                try:
                    db = pro.daily_basic(trade_date=day,
                                         fields="ts_code,turnover_rate,volume_ratio,pe,circ_mv,close")
                    if db is not None and len(db):
                        break
                except Exception:
                    continue
            if db is not None and len(db):
                db["code"] = db["ts_code"].apply(lambda x: ("sh" if x.endswith(".SH") else "sz") + x[:6])
                db = db.set_index("code")
                merged = snap.set_index("code").copy()
                for col, src, mult in (("turnover_rate", "turnover_rate", 1),
                                       ("volume_ratio", "volume_ratio", 1),
                                       ("pe", "pe", 1),
                                       ("float_market_cap", "circ_mv", 10000)):
                    merged[col] = db[src] * mult
                merged = merged.reset_index()
                merged["close"] = merged["close"].fillna(merged["code"].map(db["close"]))
                log.info("tushare daily_basic 补列完成(%s)", day)
        except Exception as exc:
            log.warning("tushare 补列失败: %r", exc)
            merged = snap
    orig = mss.load_snapshot
    mss.load_snapshot = lambda *a, **k: merged
    try:
        wl = build_buy_watchlist()
    finally:
        mss.load_snapshot = orig
    return wl.get("candidates", []) or []


def buywatch_rebalance(led: dict, px: dict, dry: bool) -> list[str]:
    """K_项目选股: 项目买入观察池 top5 → 与持仓 diff。"""
    slot = led["strategies"].get("K_项目选股")
    if not slot:
        return []
    if dry:
        return ["K_项目选股(dry) 候选重取"]
    try:
        cands = buywatch_universe()
        if not cands:
            slot["note"] = f"{EX_DATE} 项目漏斗无候选, 维持旧仓"
            return ["K_项目选股 无候选, 维持旧仓"]
        picks = []
        for c in cands:
            sym = ("SH" if str(c.get("symbol", "")).startswith("6") else "SZ") + str(c.get("symbol", ""))
            p = px.get(sym, {}).get("price") or c.get("close") or 0
            if p and p * 100 <= 2500:
                picks.append({"symbol": sym, "name": c.get("name", sym), "price": p, "qty": 100,
                              "score": c.get("score")})
            if len(picks) >= 5:
                break
        sells = [{"symbol": h["symbol"], "qty": h["qty"], "price": px.get(h["symbol"], {}).get("price") or h.get("price", 0), "is_stock": True}
                 for h in slot.get("holds", []) if h["symbol"] not in {p["symbol"] for p in picks}]
        buys = [{"symbol": p["symbol"], "name": p["name"], "qty": p["qty"], "price": p["price"], "is_stock": True}
                for p in picks if p["symbol"] not in {h["symbol"] for h in slot.get("holds", [])}]
        apply_orders(slot, sells, buys, px)
        slot["note"] = f"{EX_DATE} " + " ".join(f"{p['name']}(分{p['score']})" for p in picks[:3])
        return [f"K_项目选股 调仓→{[p['symbol'] for p in picks] or '空仓'}"]
    except Exception as exc:
        log.error("K_项目选股 调仓失败: %r", exc)
        return ["【异常】K_项目选股 候选重取失败, 持仓不变"]


def names_of(cands: list[dict]) -> dict[str, str]:
    try:
        sb = _tushare_pro().stock_basic(list_status="L", fields="ts_code,name")
        return dict(sb.values.tolist())
    except Exception:
        return {}


def _prev_month_end() -> str:
    import calendar
    t = EX_DATE.replace(day=1) - __import__("datetime").timedelta(days=1)
    return f"{t:%Y%m}{calendar.monthrange(t.year, t.month)[1]:02d}"[:8] if False else f"{t:%Y%m%d}"


def _is_month_first_trading_day() -> bool:
    """EX_DATE 是否本月首个交易日（tushare trade_cal；失败回退 day<=5 的旧窗）。"""
    if not is_exchange_trading_day(EX_DATE):
        return False
    if getattr(_is_month_first_trading_day, "_cache", None) == f"{EX_DATE:%Y%m}":
        return _is_month_first_trading_day._result  # type: ignore[attr-defined]
    result = False
    try:
        import tushare as ts
        token = ""
        for line in (PROJECT_DIR / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("TUSHARE_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
        cal = ts.pro_api(token).trade_cal(exchange="SSE", start_date=f"{EX_DATE:%Y%m}01",
                                          end_date=f"{EX_DATE:%Y%m%d}", fields="cal_date,is_open")
        open_days = sorted(str(x) for x in cal[cal["is_open"] == 1]["cal_date"])  # trade_cal 返回降序, 必须排序取月首
        if open_days:
            result = open_days[0] == f"{EX_DATE:%Y%m%d}"
    except Exception as exc:
        log.warning("月首交易日判定失败, 回退 day<=5 窗: %r", exc)
        result = EX_DATE.day <= 5
    _is_month_first_trading_day._cache = f"{EX_DATE:%Y%m}"  # type: ignore[attr-defined]
    _is_month_first_trading_day._result = result  # type: ignore[attr-defined]
    return result


def mwu_target_orders(target: dict[str, float], cur: dict[str, int], prices: dict[str, float],
                      capital: float = CAPITAL) -> tuple[list[dict], list[dict]]:
    """纯函数：MWU 权重目标 → 整手 sells/buys（511010 防御腿 10张/手，其余 100股）。可测。"""
    sells: list[dict] = []
    buys: list[dict] = []
    for sym, w in target.items():
        lot = 10 if sym == "511010.SH" else 100
        p = prices.get(sym) or 0
        if not p:
            continue
        want = int(float(w) * capital // (p * lot)) * lot
        have = cur.get(sym, 0)
        if want > have:
            buys.append({"symbol": sym, "qty": want - have, "price": p})
        elif want < have:
            sells.append({"symbol": sym, "qty": have - want, "price": p})
    for sym, qty in cur.items():
        if sym not in target and qty > 0:
            p = prices.get(sym) or 0
            if p:
                sells.append({"symbol": sym, "qty": qty, "price": p})
    return sells, buys


def mwu_rebalance(led: dict, px: dict, dry: bool, slot_name: str = "G_MWU融合",
                  strategy: str = "etf_mwu", strategy_config: str = "config/strategy/etf_mwu.yaml") -> list[str]:
    """MWU 槽位：跑引擎提取最新目标 → 与持仓 diff 先卖后买。失败不影响收盘落账。"""
    slot = led["strategies"].get(slot_name)
    if not slot:
        return []
    if dry:
        return [f"{slot_name}(dry) 引擎目标提取"]
    try:
        r = subprocess.run(
            [sys.executable, str(PROJECT_DIR.parent / "quant_research" / "scripts" / "extract_mwu_targets.py"),
             "--strategy", strategy, "--strategy-config", strategy_config, "--config", "config/default.yaml"],
            cwd=str(PROJECT_DIR.parent / "quant_research"), capture_output=True, text=True, timeout=600)
        data = _parse_engine_stdout(r.stdout)
        target = {k: float(v) for k, v in (data.get("target") or {}).items()}
        cur = {h["symbol"]: h["qty"] for h in slot.get("holds", [])}
        prices = {sym: (px.get(sym, {}).get("price") or 0) for sym in set(target) | set(cur)}
        sells, buys = mwu_target_orders(target, cur, prices)
        for o in sells + buys:
            o["is_stock"] = False
        now = exchange_now()
        tradable = bool(now) and is_trading_session(now)
        if (sells or buys) and not tradable:
            log.warning("非交易时段(交易所 %s), %s 调仓抑制为只记录", now, slot_name)
        if (sells or buys) and tradable:
            apply_orders(slot, sells, buys, px)
        slot["signal"] = {"as_of": data.get("as_of"), "expert_weights": data.get("expert_weights"),
                          "target": target}
        tag = f"调仓→{sorted(target)}" if (sells or buys) else f"目标未变(as_of {data.get('as_of')})"
        return [f"{slot_name} {tag}"]
    except Exception as exc:
        log.error("%s 调仓失败: %r", slot_name, exc)
        return [f"【异常】{slot_name} 引擎目标提取失败, 持仓保持不变"]


def cb_rebalance(led: dict, px: dict, dry: bool) -> list[dict]:
    """E 月度调仓：重取引擎目标 → 按 weight 换算整手张数 → 与持仓 diff 先卖后买。"""
    if dry:
        return ["E_可转债双低(dry) 月度重取目标"]
    try:
        subprocess.run([sys.executable, str(PROJECT_DIR.parent / "quant_research" / "scripts" / "run_paper_tracking.py"),
                        "--export-plan", "--once", "--capital", str(int(CAPITAL))],
                       cwd=str(PROJECT_DIR.parent / "quant_research"), capture_output=True, text=True, timeout=300)
        plan = json.loads((PROJECT_DIR.parent / "quant_research" / "output" / "paper_tracking" / "current_plan.json").read_text(encoding="utf-8"))
        targets = {k: v for k, v in (plan.get("targets") or {}).items()
                   if isinstance(v, dict) and v.get("signal_state") == "executable"}
        slot = led["strategies"]["E_可转债双低"]
        cur = {h["symbol"]: h["qty"] for h in slot.get("holds", [])}
        price_of = {}
        want = {}
        for sym, meta in targets.items():
            price = (px.get(sym, {}).get("price")) or meta.get("t1_close") or 0
            if not price:
                continue
            price_of[sym] = price
            lots = int(float(meta.get("weight", 0)) * CAPITAL // (price * 10)) * 10  # 转债 10张/手
            if lots > 0:
                want[sym] = lots
        sells = [{"symbol": h["symbol"], "qty": h["qty"] - want.get(h["symbol"], 0),
                  "price": px.get(h["symbol"], {}).get("price") or h.get("price", 0)}
                 for h in slot.get("holds", []) if h["qty"] > want.get(h["symbol"], 0)]
        sells = [s for s in sells if s["qty"] > 0]
        buys = [{"symbol": sym, "qty": lots - cur.get(sym, 0), "price": price_of[sym]}
                for sym, lots in want.items() if lots > cur.get(sym, 0)]
        apply_orders(slot, sells, buys, px)
        slot["note"] = f"月度调仓 {today} 目标{len(want)}只 executable"
        return [f"E_可转债双低 月度调仓→{sorted(want)}"]
    except Exception as exc:
        log.error("E 月度调仓失败: %r", exc)
        return []


# ---------------------------------------------------------------- 事件套利
OFFER_API = "https://np-cnotice-stock.eastmoney.com/api/content/ann?art_code={code}&client_source=web&page_index={page}"
ANN_LIST_API = "https://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size={size}&page_index=1&ann_type=A&stock_list={code}"
WATCH_TRIGGER = ("现金选择权", "要约收购", "吸收合并", "主动终止上市", "终止上市", "退市方案", "清算", "重组方案")
PRICE_PATTERNS = (r"要约价格(?:为)?\s*([\d.]+)\s*元",
                  r"现金选择权(?:价格)?(?:为)?\s*([\d.]+)\s*元",
                  r"换股吸收合并[^。]{0,40}([\d.]+)\s*元/股")


def classify_tender_offer(text: str) -> str:
    """纯函数：公告正文 → 要约类型（partial/full/unknown）。

    部分要约按预定比例收购，剩余持仓承担价格风险——不是确定性价差，
    一律转人工评估，不自动建仓。"""
    if "部分要约" in (text or ""):
        return "partial"
    if "全面要约" in (text or ""):
        return "full"
    return "unknown"


def parse_price_from_text(text: str) -> float | None:
    """从公告正文提取对价（要约价/现金选择权/吸收合并换股价），纯函数可测。"""
    for pat in PRICE_PATTERNS:
        m = re.search(pat, text or "")
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return None


def _fetch_content(art_code: str, pages: int = 7) -> str:
    full = ""
    for p in range(1, pages + 1):
        try:
            d = json.loads(urlrequest.urlopen(urlrequest.Request(
                OFFER_API.format(code=art_code, page=p),
                headers={"User-Agent": "Mozilla/5.0"}), timeout=15).read())
        except Exception as exc:
            log.warning("公告正文页失败(%s p%s): %r", art_code, p, exc)
            break
        full += d.get("data", {}).get("notice_content") or ""
    return full


def fetch_offer_price(art_code: str) -> float | None:
    return parse_price_from_text(_fetch_content(art_code))


def fetch_recent_announcements(code: str, size: int = 8) -> list[dict]:
    d = json.loads(urlrequest.urlopen(urlrequest.Request(
        ANN_LIST_API.format(code=code, size=size), headers={"User-Agent": "Mozilla/5.0"}), timeout=15).read())
    return [{"art_code": str(it.get("art_code", "")), "title": str(it.get("title", "")),
             "date": str(it.get("notice_date", ""))[:10]}
            for it in ((d.get("data", {}) or {}).get("list") or [])]


def event_round(led: dict, dry: bool) -> list[dict]:
    """事件扫描 + 价差核验 + X 槽建仓/否决。"""
    try:
        import akshare as ak
        import datetime as _dt
        today = EX_DATE
        keywords = ("要约收购", "现金选择权", "私有化", "主动退市", "终止上市", "吸收合并")
        noise = ("风险提示", "可能因", "配售", "全资子公司", "完成吸收合并")  # 内部合并/提示类无套利意义
        new_events = []
        seen_titles = {str(v.get("title") or v.get("event") or "")
                       for v in (led["strategies"].get("X_事件套利", {}).get("veto_log", []))}
        seen_titles |= {str(v.get("title") or "") for v in (led["strategies"].get("X_事件套利", {}).get("watchlist", []))}
        for d in (today, today - _dt.timedelta(days=1)):
            try:
                df = ak.stock_notice_report(symbol="全部", date=d.strftime("%Y%m%d"))
            except Exception:
                continue
            if df is None:
                continue
            for _, row in df.iterrows():
                title = str(row.get("公告标题", ""))
                if any(k in title for k in keywords) and not any(n in title for n in noise) and title not in seen_titles:
                    new_events.append({"date": d.isoformat(), "code": str(row.get("代码", "")),
                                       "name": str(row.get("名称", "")), "title": title,
                                       "url": str(row.get("网址", ""))})
        msgs = []
        slot = led["strategies"]["X_事件套利"]
        px = realtime_quotes([e["code"] for e in new_events]) if new_events else {}
        for ev in new_events:
            art = (re.search(r"/([A-Z0-9]+)\.html", ev["url"]) or [None, ""])[1]
            offer = fetch_offer_price(art) if ("要约收购" in ev["title"] or "吸收合并" in ev["title"]) else None
            spot = px.get(ev["code"], {}).get("price") or 0.0
            offer_type = classify_tender_offer(_fetch_content(art, pages=4)) if art else "unknown"
            record = {**ev, "offer_price": offer, "spot": spot, "offer_type": offer_type}
            if offer_type != "full":
                record["decision"] = f"HOLD({offer_type}要约需人工评估收购比例)"
                if not dry:
                    msgs.append(f"X事件 HOLD {ev['name']}: {offer_type}要约非确定性价差, 转人工")
            elif offer and spot and spot < offer * (0.98 if "ST" not in ev["name"] else 0.95):
                qty = int(min(1241.0, slot.get("cash", 0)) // (spot * 100)) * 100
                if qty and not dry:
                    apply_orders(slot, [], [{"symbol": ev["code"], "name": ev["name"], "qty": qty,
                                             "price": spot, "is_stock": True}], px)
                    msgs.append(f"X事件套利 买入 {ev['name']}({ev['code']}) {qty}股@{spot} 要约价{offer}")
                record["decision"] = "BUY"
            else:
                record["decision"] = "VETO" + ("" if not offer else f"(溢价{spot/offer-1:+.1%})")
                msgs.append(f"X事件否决 {ev['name']}: 要约价{offer} 现价{spot} {record['decision']}")
            slot.setdefault("veto_log", []).append(record)
        return [] if dry else msgs
    except Exception as exc:
        log.error("事件轮失败: %r", exc)
        return []


def watchlist_round(led: dict, dry: bool) -> list[dict]:
    """X 观察名单跟踪：每交易日核查候选事件公司的新公告 → 提取对价 → 按 rule 核价差。"""
    slot = led["strategies"].get("X_事件套利", {})
    msgs: list[dict] = []
    try:
        for item in slot.get("watchlist", []):
            code = str(item.get("code", ""))
            if not code or item.get("last_checked") == EX_DATE.isoformat():
                continue
            try:
                anns = fetch_recent_announcements(code)
            except Exception as exc:
                log.warning("观察名单公告获取失败 %s: %r", code, exc)
                continue
            item["last_checked"] = EX_DATE.isoformat()
            seen = set(item.get("seen_titles", []))
            for a in anns:
                title = a["title"]
                if a["art_code"] in seen or title in seen or not title:
                    continue
                if not any(k in title for k in WATCH_TRIGGER):
                    continue
                seen.add(a["art_code"]); seen.add(title)
                spot = (realtime_quotes([code]).get(code, {}) or {}).get("price") or 0.0
                full_text = _fetch_content(a["art_code"], pages=4)
                price = parse_price_from_text(full_text)
                name = str(item.get("name", code))
                is_st = "ST" in name
                otype = classify_tender_offer(full_text) if full_text else "unknown"
                record = {"date": EX_DATE.isoformat(), "code": code, "name": name,
                          "title": title[:60], "offer_price": price, "spot": spot, "offer_type": otype}
                if otype != "full":
                    record["decision"] = f"HOLD({otype}要约需人工评估)"
                elif price and spot and spot < price * (0.95 if is_st else 0.98):
                    qty = int(min(1241.0, slot.get("cash", 0)) // (spot * 100)) * 100
                    if qty and not dry:
                        apply_orders(slot, [], [{"symbol": code, "name": name, "qty": qty,
                                                 "price": spot, "is_stock": True}], {})
                        msgs.append(f"X事件套利 买入 {name}({code}) {qty}股@{spot} 对价{price} ({title[:24]})")
                    record["decision"] = "BUY"
                else:
                    record["decision"] = "HOLD" + (f"(对价{price}/现价{spot})" if price else "(对价未披露)")
                    if not dry:
                        msgs.append(f"X观察 {name}: {title[:36]} → {record['decision']}")
                slot.setdefault("veto_log", []).append(record)
            item["seen_titles"] = sorted(seen)[-20:]
    except Exception as exc:
        log.error("观察名单轮失败: %r", exc)
    return [] if dry else msgs


# ---------------------------------------------------------------- 风控
CREDIT_WORDS = ("下调", "回售", "违约", "兑付", "本息", "评级", "质押冻结", "逾期")


def tencent_quotes(codes6: list[str]) -> dict[str, dict]:
    """转债/指数等六源链覆盖不到的品种：腾讯直连（6位裸码）。返回 price/pct/name。"""
    out: dict[str, dict] = {}
    try:
        raw = urlrequest.urlopen(urlrequest.Request(
            "http://qt.gtimg.cn/q=" + ",".join(codes6), headers={"User-Agent": "Mozilla/5.0"}),
            timeout=10).read().decode("gbk")
        for line in raw.strip().split("\n"):
            if "=" not in line:
                continue
            f = line.split('="', 1)[1].rstrip('";').split("~")
            if f[3] and float(f[3]) > 0:
                out[f[2]] = {"price": float(f[3]), "pct": float(f[32]) if f[32] else 0.0,
                             "name": f[1]}
    except Exception as exc:
        log.warning("腾讯直连行情失败: %r", exc)
    return out


def risk_round(led: dict, px: dict, dry: bool) -> list[dict]:
    """三层风控 + 分级熔断（三年数据校准 2026-09-30，见台账 crash_study）。

    触发信号(任一): 微盘中位数≤-4 / 国证2000日跌≤-3 / 国证2000峰值回撤≤-5。
    分级(normal→watch→defensive): 首触减1/3+停新买入; 30日历日内再触清仓;
    恢复: 月度窗F重建持仓时自动回normal。数据教训: 单次触发后20日平均+3.77%
    (16次里13次反弹)——清仓清在反弹前, 连续触发才是系统性危机特征。"""
    msgs = []
    ctrl = led.setdefault("_control", {})
    crash = ctrl.get("crash_state", "normal")
    micro = sorted(px[h["symbol"]].get("pct", 0) for s in ("F_小市值20", "F2_小市值5只神帖对照")
                   for h in led["strategies"][s].get("holds", [])
                   if px.get(h["symbol"], {}).get("pct") is not None)
    med = micro[len(micro)//2] if micro else 0.0
    idx = tencent_quotes(["sz399303"]).get("399303", {})
    idx_pct = idx.get("pct", 0.0)
    triggered = (micro and med <= -4.0) or idx_pct <= -3.0
    if triggered:
        cause = f"微盘中位数 {med}%" if med <= -4.0 else f"国证2000 {idx_pct}%"
        if crash == "normal":
            crash = "watch"
            ctrl["crash_state"] = "watch"
            ctrl["crash_watch_until"] = (EX_DATE + timedelta(days=30)).isoformat()
            ctrl["crash_cause"] = cause
            if not dry:
                for s in ("F_小市值20", "F2_小市值5只神帖对照"):
                    slot = led["strategies"][s]
                    sells = [{"symbol": h["symbol"], "qty": h["qty"] // 3,
                              "price": px[h["symbol"]]["price"], "is_stock": True}
                             for h in slot.get("holds", []) if h["qty"] // 3 > 0]
                    apply_orders(slot, sells, [], px)
            msgs.append(f"【熔断一级】{cause} → F/F2 减1/3, 进入观察期(30日历日内再触即清仓)")
        elif crash == "watch":
            until = ctrl.get("crash_watch_until", "")
            if EX_DATE.isoformat() <= until:
                crash = "defensive"
                ctrl["crash_state"] = "defensive"
                if not dry:
                    for s in ("F_小市值20", "F2_小市值5只神帖对照"):
                        slot = led["strategies"][s]
                        sells = [{"symbol": h["symbol"], "qty": h["qty"], "price": px[h["symbol"]]["price"], "is_stock": True}
                                 for h in slot.get("holds", [])]
                        apply_orders(slot, sells, [], px)
                msgs.append(f"【熔断二级】观察期内再次触发({cause}) → F/F2 全部清仓")
            else:
                ctrl["crash_watch_until"] = (EX_DATE + timedelta(days=30)).isoformat()
        # defensive 状态: 持仓已空, 无需动作
    # E 信用监测
    prices = tencent_quotes(["sh113062", "sh113631", "sh113691", "sh118031"])
    lowest = min((v["price"] for v in prices.values() if v), default=0)
    ew = led["strategies"]["E_可转债双低"].setdefault("credit_watch", {})
    if lowest > 0:
        ew.update({"min_price": lowest, "bonds": {k: v["price"] for k, v in prices.items()},
                   "checked": EX_DATE.isoformat()})
    ew.setdefault("seen_alerts", [])
    if 0 < lowest < 100 and not dry:
        slot = led["strategies"]["E_可转债双低"]
        sells = [{"symbol": h["symbol"], "qty": h["qty"] // 2,
                  "price": prices.get(h["symbol"].split(".")[0], {}).get("price") or h["price"]}
                 for h in slot.get("holds", [])]
        apply_orders(slot, sells, [], px)
        msgs.append(f"【信用告警】转债最低 {lowest} → E 减半仓")
    for code6 in ("113062", "113631", "113691", "118031"):
        try:
            anns = fetch_recent_announcements(code6, size=3)
        except Exception as exc:
            log.warning("转债信用公告获取失败 %s: %r", code6, exc)
            continue
        for a in anns:
            title = a["title"]
            if a["art_code"] in ew["seen_alerts"] or not title:
                continue
            if any(w in title for w in CREDIT_WORDS):
                ew["seen_alerts"].append(a["art_code"])
                msgs.append(f"【信用事件】{code6} {title[:44]}")
                if not dry:
                    ew["credit_flag"] = title[:60]
    ew["seen_alerts"] = ew["seen_alerts"][-30:]
    for s, th in (("F_小市值20", 5), ("F2_小市值5只神帖对照", 5), ("E_可转债双低", 3)):
        for h in led["strategies"][s].get("holds", []):
            pct = px.get(h["symbol"], {}).get("pct")
            if pct is not None and abs(pct) >= th:
                msgs.append(f"异动 {h.get('name', h['symbol'])} {pct:+.1f}%")
    return [] if dry else msgs

def slot_drawdown(marks: list, slot_name: str, current_eq: float) -> float:
    """槽位当前净值相对历史峰值的回撤(%), 无历史返回 0。"""
    peak = current_eq
    for m in marks:
        v = (m.get("equity") or {}).get(slot_name)
        if v and v > peak:
            peak = v
    return round((1 - current_eq / peak) * 100, 2) if peak > 0 else 0.0


def _is_month_last_trading_day() -> bool:
    """EX_DATE 是否本月最后一个交易日（tushare trade_cal 升序取月末；失败回退月底3日窗）。"""
    if not is_exchange_trading_day(EX_DATE):
        return False
    if getattr(_is_month_last_trading_day, "_cache", None) == f"{EX_DATE:%Y%m}":
        return _is_month_last_trading_day._result
    result = False
    try:
        import tushare as ts
        token = ""
        for line in (PROJECT_DIR / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("TUSHARE_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
        cal = ts.pro_api(token).trade_cal(exchange="SSE", start_date=f"{EX_DATE:%Y%m}01",
                                          end_date=f"{EX_DATE:%Y%m%d}", fields="cal_date,is_open")
        open_days = sorted(str(x) for x in cal[cal["is_open"] == 1]["cal_date"])
        result = bool(open_days) and open_days[-1] == f"{EX_DATE:%Y%m%d}"
    except Exception as exc:
        log.warning("月末交易日判定失败, 回退月底3日窗: %r", exc)
        result = EX_DATE.day >= 28
    _is_month_last_trading_day._cache = f"{EX_DATE:%Y%m}"
    _is_month_last_trading_day._result = result
    return result


def monthly_report(led: dict, dry: bool) -> str:
    """月度报告: 各槽本月收益/最大回撤/累计费用 + 预注册假设状态。"""
    marks = led.get("marks", [])
    month_key = f"{EX_DATE:%Y-%m}"
    month_marks = [m for m in marks if (m.get("date") or "").startswith(month_key)]
    if not month_marks:
        return ""
    lines = [f"【月度报告 {month_key}】"]
    for name, s in led.get("strategies", {}).items():
        start_eq = month_marks[0].get("equity", {}).get(name)
        end_eq = s.get("equity", 0)
        if not start_eq:
            lines.append(f"{name}: 本月无起点净值(月中建仓?) 现值{end_eq:,.0f}")
            continue
        r = (end_eq / start_eq - 1) * 100
        dd = slot_drawdown(marks, name, end_eq)
        fees = s.get("fees_paid", 0.0)
        pr = (led.get("prereg", {}).get(name) or {})
        pred = str(pr.get("prediction", pr.get("hypothesis", "")))[:36]
        lines.append(f"{name}: 本月{r:+.2f}% 回撤{dd:.2f}% 费用{fees:,.0f} | {pred}")
    meta = led.get("prereg", {}).get("_meta", {})
    if meta.get("review"):
        lines.append(f"预注册复核: {meta['review']}")
    return chr(10).join(lines)

_CAL_CACHE: dict = {}


def is_exchange_trading_day(d: date) -> bool:
    """tushare trade_cal 交易日校验；接口失败时保守视为交易日（close_done 幂等兜底）。"""
    if _CAL_CACHE.get(d.isoformat()) is not None:
        return _CAL_CACHE[d.isoformat()]
    result = True
    try:
        import tushare as ts
        token = ""
        for line in (PROJECT_DIR / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("TUSHARE_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
        cal = ts.pro_api(token).trade_cal(exchange="SSE", start_date=d.strftime("%Y%m%d"),
                                          end_date=d.strftime("%Y%m%d"), fields="is_open")
        result = str(cal["is_open"].iloc[0]) == "1"
    except Exception as exc:
        log.warning("交易日历获取失败(%s), 保守视为交易日: %r", d, exc)
    _CAL_CACHE[d.isoformat()] = result
    return result


def run_intraday(dry: bool) -> str:
    led = load_ledger()
    if not is_exchange_trading_day(EX_DATE):
        return f"休市(交易所日历 {EX_DATE})，跳过"
    px = slot_prices(led)
    # 事件全量扫描节流：每半天最多一次（公告流一次 ~800 行，逐轮扫既慢又浪费）
    ctrl = led.setdefault("_control", {})
    halfday = f"{EX_DATE:%Y%m%d}_{ 'AM' if datetime.now().hour < 12 else 'PM'}"
    scan = ctrl.get("event_scan_last") != halfday
    msgs = ((event_round(led, dry) + watchlist_round(led, dry)) if scan else []) + risk_round(led, px, dry)
    if scan and not dry:
        ctrl["event_scan_last"] = halfday
    if (msgs or scan) and not dry:
        today = EX_DATE.isoformat()
        acts = led.setdefault("_actions", {}).setdefault(today, [])
        led["_actions"][today] = sorted(set(acts) | {m for m in msgs if "买入" in m or "卖出" in m or "清仓" in m})
        save_ledger(led)
        for i, m in enumerate(set(msgs)):
            notify(m, dedup_key=f"deck_intraday_{EX_DATE:%Y%m%d}_{i}")
    out = "盘中轮: " + ("; ".join(sorted(set(msgs))) if msgs else "无触发")
    # 收盘自动触发：交易所时间 ≥15:00 且当日收盘轮未跑 → 补跑（幂等）
    ex_now = exchange_now()
    if ex_now and ex_now.hour * 100 + ex_now.minute >= 1500:
        if ctrl.get("close_done") == ex_now.date().isoformat():
            out += " | 收盘轮当日已执行, 跳过"
        else:
            out += " | " + run_close(dry)
            if not dry:
                led2 = load_ledger()
                led2.setdefault("_control", {})["close_done"] = ex_now.date().isoformat()
                save_ledger(led2)
    return out


def e2_gate(led: dict, dry: bool) -> list[str]:
    """E2 转债估值择时（研究验证回撤改善31%）：全市场转债中位数锚点分位 → E 槽减半/恢复。

    数据: tushare cb_daily 单次全市场当日; 中位数序列逐日积累进 credit_watch.median_history。"""
    ctrl = led.setdefault("_control", {})
    try:
        import tushare as ts
        token = ""
        for line in (PROJECT_DIR / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("TUSHARE_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
        df = ts.pro_api(token).cb_daily(trade_date=EX_DATE.strftime("%Y%m%d"), fields="close")
        prices = [float(x) for x in (df["close"].dropna().tolist() if df is not None else []) if x and float(x) > 0]
    except Exception as exc:
        log.warning("E2 数据失败: %r", exc)
        prices = []
    median = round(pd_median(prices), 2) if prices else 0.0
    ew = led["strategies"]["E_可转债双低"].setdefault("credit_watch", {})
    hist = ew.setdefault("median_history", [])
    if median:
        hist.append({"date": EX_DATE.isoformat(), "median": median})
        ew["median_history"] = hist[-400:]
    state = ctrl.get("e2_state", "normal")
    action = e2_gate_decision(median, state)
    ew["e2"] = {"median": median, "state": state, "action": action}
    msgs = []
    if action == "enter_defensive" and not dry:
        slot = led["strategies"]["E_可转债双低"]
        sells = [{"symbol": h["symbol"], "qty": h["qty"] // 2,
                  "price": (px or {}).get(h["symbol"], {}).get("price") or h.get("price", 0)}
                 for h in slot.get("holds", []) if h["qty"] // 2 > 0]
        apply_orders(slot, sells, [], px)
        ctrl["e2_state"] = "defensive"
        ew["e2"]["state"] = "defensive"
        msgs.append(f"【E2择时】转债中位数 {median} ≥127 → E 减半")
    elif action == "exit_defensive" and not dry:
        ctrl["e2_state"] = "normal"
        ew["e2"]["state"] = "normal"
        msgs.append(f"【E2择时】转债中位数 {median} <118 → E 恢复满仓")
    return msgs


def erp_gate(led: dict, px: dict, dry: bool) -> list[str]:
    """ERP 总闸（每周一查，滞回 80/60）：沪深300 PE 10年分位 >80% → 主动槽减半；<60% 解除。

    研究依据：估值分位只在极值区有效（国信实证 R²=0.13/0.24），中性区不动。"""
    ctrl = led.setdefault("_control", {})
    last = ctrl.get("erp_last_check")
    if last and (EX_DATE - datetime.strptime(last, "%Y-%m-%d").date()).days < 5:
        return []
    try:
        import tushare as ts
        token = ""
        for line in (PROJECT_DIR / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("TUSHARE_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
        df = ts.pro_api(token).index_dailybasic(ts_code="000300.SH",
                                                start_date=(EX_DATE - datetime.timedelta(days=3652)).strftime("%Y%m%d"),
                                                end_date=EX_DATE.strftime("%Y%m%d"), fields="trade_date,pe_ttm")
        pe = df.dropna(subset=["pe_ttm"]).sort_values("trade_date")
        hist = [float(x) for x in pe["pe_ttm"] if x and float(x) > 0]
    except Exception as exc:
        log.warning("ERP 总闸数据失败(乐咕兜底): %r", exc)
        hist = []
        try:
            import akshare as ak
            df2 = ak.stock_index_pe_lg(symbol="沪深300")
            col = "滚动市盈率" if "滚动市盈率" in df2.columns else "pe_ttm"
            hist = [float(x) for x in df2[col].tolist() if x and float(x) > 0]
        except Exception as exc2:
            log.warning("ERP 乐咕兜底也失败: %r", exc2)
    cur = hist[-1] if hist else 0.0
    pct = pe_percentile(hist, cur)
    state = ctrl.get("erp_state", "normal")
    action = erp_decision(pct, state)
    ctrl["erp_last_check"] = EX_DATE.isoformat()
    ctrl["erp"] = {"pe": round(cur, 2), "percentile": pct, "state": state, "action": action}
    msgs = []
    active = ("A_动量", "E_可转债双低", "F_小市值20", "F2_小市值5只神帖对照", "G_MWU融合", "H_MWU_CB融合")
    if action == "enter_defensive" and not dry:
        for s in active:
            slot = led["strategies"][s]
            is_stock = s in ("F_小市值20", "F2_小市值5只神帖对照")
            sells = [{"symbol": h["symbol"], "qty": h["qty"] // 2,
                      "price": px.get(h["symbol"], {}).get("price") or h.get("price", 0),
                      "is_stock": is_stock}
                     for h in slot.get("holds", []) if h["qty"] // 2 > 0]
            apply_orders(slot, sells, [], px)
            slot["erp_note"] = f"ERP防御 {EX_DATE} 分位{pct}%"
        ctrl["erp_state"] = "defensive"
        msgs.append(f"【ERP总闸】沪深300 PE分位 {pct}% >80 → 主动槽减半")
    elif action == "exit_defensive" and not dry:
        ctrl["erp_state"] = "normal"
        for s in active:
            led["strategies"][s].pop("erp_note", None)
        msgs.append(f"【ERP总闸】PE分位 {pct}% <60 → 解除防御")
    return msgs


def _tushare_pro():
    import tushare as ts
    token = ""
    for line in (PROJECT_DIR / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("TUSHARE_TOKEN="):
            token = line.split("=", 1)[1].strip().strip('"').strip("'")
    return ts.pro_api(token)


def _ok_symbol(name: str, ts_code: str) -> bool:
    return ("ST" not in name) and (not ts_code.startswith(("8", "4", "9", "688")))


def pead_universe(days_back: int = 40, top: int = 8) -> list[dict]:
    """I_PEAD 候选: 最近 N 天公告的预增(预告净利同比≥50%) → 按公告日新鲜度排序。

    数据: tushare forecast 逐日 ann_date(接口限制); 三季报/年报预告季密集。"""
    pro = _tushare_pro()
    seen, out = set(), []
    for d in range(days_back, 0, -1):
        day = (EX_DATE - timedelta(days=d)).strftime("%Y%m%d")
        try:
            df = pro.forecast(ann_date=day, fields="ts_code,ann_date,type,p_change")
        except Exception:
            continue
        if df is None or df.empty:
            continue
        for _, r in df.iterrows():
            code = str(r.get("ts_code", ""))
            typ = str(r.get("type", ""))
            chg = r.get("p_change")
            chg = float(chg) if chg not in (None, "", "nan") else None
            if typ != "预增" or (chg is not None and chg < 50):
                continue
            if code in seen or not _ok_symbol("", code):
                continue
            seen.add(code)
            out.append({"ts_code": code, "ann_date": day, "p_change": chg})
    return out[:top]


def reversal_universe(days: int = 21, top: int = 8) -> list[dict]:
    """J_短期反转候选: 全市场 21 日跌幅最深 + 流动性过滤(金额≥3000万) + 剔ST/北交所/科创板。

    数据: tushare daily 逐日(daily 单次上限6000行)。机制: A股短期反转(LSY2019/STREV实证)。"""
    pro = _tushare_pro()
    series: dict[str, dict[str, float]] = {}
    for d in range(days, -1, -1):
        day = (EX_DATE - timedelta(days=d)).strftime("%Y%m%d")
        try:
            df = pro.daily(trade_date=day, fields="ts_code,close,amount")
        except Exception:
            continue
        if df is None or df.empty:
            continue
        for _, r in df.iterrows():
            series.setdefault(str(r["ts_code"]), {})[day] = (float(r["close"]), float(r.get("amount") or 0))
    out = []
    for code, pxmap in series.items():
        days_sorted = sorted(pxmap)
        if len(days_sorted) < days:
            continue
        c0, c1 = pxmap[days_sorted[0]][0], pxmap[days_sorted[-1]][0]
        amt = pxmap[days_sorted[-1]][1]
        if c0 <= 0 or amt < 3e7:
            continue
        out.append({"ts_code": code, "ret21": round((c1 / c0 - 1) * 100, 2)})
    sb = _tushare_pro().stock_basic(list_status="L", fields="ts_code,name")
    names = dict(sb.values.tolist())
    out = [r for r in out if _ok_symbol(names.get(r["ts_code"], ""), r["ts_code"])]
    out.sort(key=lambda r: r["ret21"])
    return out[:top]


def build_close_summary(led: dict, px: dict) -> str:
    """收盘消息：排名 + 今日操作 + 每槽持仓明细（数量/成本/现价/盈亏）+ 风险状态。"""
    equity = {name: s.get("equity", 0) for name, s in led["strategies"].items()}
    ret = {name: round((eq / CAPITAL - 1) * 100, 2) for name, eq in equity.items()}
    rank = sorted(equity, key=lambda k: equity[k], reverse=True)
    today = EX_DATE.isoformat()
    lines = [f"【对比盘收盘 {today}】", "▎排名"]
    for i, k in enumerate(rank, 1):
        dd = slot_drawdown(led.get("marks", []), k, equity[k])
        lines.append(f"{i}. {k} ¥{equity[k]:,.0f} ({ret[k]:+.2f}%) 回撤{dd:.1f}%")
    lines.append("▎今日操作")
    actions = (led.get("_actions", {}).get(today) or [])
    lines += [f"· {a}" for a in actions] if actions else ["· 无调仓"]
    lines.append("▎持仓明细")
    for name in ("A_动量", "B_轮动", "E_可转债双低", "F_小市值20", "F2_小市值5只神帖对照", "X_事件套利", "G_MWU融合", "H_MWU_CB融合", "I_PEAD业绩预告", "J_短期反转", "K_项目选股", "C_沪深300持有", "D_红利低波持有"):
        holds = led["strategies"][name].get("holds", [])
        if not holds:
            lines.append(f"{name}: 空仓")
            continue
        parts = []
        for h in holds:
            cost = h.get("price", 0)
            now = (px.get(h["symbol"], {}).get("price")) or cost
            unit = "张" if ("转债" in h.get("name", "") or name.startswith("E_")
                            or h["symbol"] == "511010.SH") else "股"
            pnl = (now / cost - 1) * 100 if cost else 0
            parts.append(f"{h.get('name', h['symbol'])} {h['qty']}{unit}@{cost}→{now}({pnl:+.1f}%)")
        lines.append(f"{name}: " + " ".join(parts))
    ew = led["strategies"]["E_可转债双低"].get("credit_watch", {})
    lines.append(f"▎风险 信用最低{ew.get('min_price', '—')} | 本金¥{CAPITAL:,.0f}/槽")
    if _is_month_last_trading_day():
        lines.append("▎Y槽窗口: 今日为月末/节前最后交易日, 14:30后可操作逆回购GC001(¥12,000)")
    if EX_DATE.weekday() == 4 and led.get("prereg"):  # 周五: 预注册假设状态
        lines.append("▎假设状态(预注册)")
        for name, pr in led["prereg"].items():
            if name.startswith("_"):
                continue
            pred = str(pr.get("prediction", pr.get("hypothesis", "")))[:46]
            lines.append(f"{name}: {pred} | 复核 {(pr.get('review') or '2026-12-31')[:10]}")
    return "\n".join(lines)


def daily_dividend_pass(led: dict, px: dict, dry: bool) -> list[str]:
    """每日一次：股票/ETF 持仓的除息分红计入现金（红利策略语义=分红到账）。

    节流：EX_DATE 一天一次；结果缓存 (ts_code, since)。"""
    ctrl = led.setdefault("_control", {})
    if ctrl.get("dividend_check_last") == EX_DATE.isoformat():
        return []
    msgs = []
    since = (led.get("start_date") or "2026-09-28").replace("-", "")
    for name, s in led["strategies"].items():
        for h in s.get("holds", []):
            sym = h["symbol"]
            if not sym.endswith((".SH", ".SZ")) or sym.startswith(("SH204001",)):
                continue
            key = (sym, since)
            if key not in _DIV_CACHE:
                _DIV_CACHE[key] = dividend_accrued(sym, since, h.get("price", 0), h["qty"])
            cash, desc = _DIV_CACHE[key]
            if cash > 0 and not dry:
                s["cash"] = round(s.get("cash", 0) + cash, 2)
                s.setdefault("tuning", []).append(
                    {"date": EX_DATE.isoformat(), "change": f"分红到账 ¥{cash}", "why": desc})
                msgs.append(f"分红 {name} {h.get('name', sym)} +¥{cash} ({desc})")
    ctrl["dividend_check_last"] = EX_DATE.isoformat()
    return msgs


_DIV_CACHE: dict = {}


def run_close(dry: bool) -> str:
    led = load_ledger()
    px = slot_prices(led)
    today = EX_DATE.isoformat()
    snap_msg = maybe_refresh_snapshot(dry)
    div_msgs = [] if dry else daily_dividend_pass(led, px, dry)
    marks = led.setdefault("marks", [])
    equity, ret = {}, {}
    for name, s in led["strategies"].items():
        eq = revalue(s, px)
        equity[name], ret[name] = eq, round((eq / CAPITAL - 1) * 100, 2)
    existing = next((m for m in marks if m["date"] == today), None)
    if existing:
        existing.update({"equity": equity, "return_pct": ret})
    else:
        marks.append({"date": today, "equity": equity, "return_pct": ret})
    msgs: list[str] = []
    if snap_msg:
        msgs.append(snap_msg)
    msgs.extend(div_msgs)
    msgs.extend(erp_gate(led, px, dry))
    msgs.extend(e2_gate(led, dry))
    if _is_month_last_trading_day() and not dry:
        rep = monthly_report(led, dry)
        if rep:
            notify(rep, dedup_key=f"deck_monthly_{EX_DATE:%Y-%m}")
    ctrl = led.setdefault("_control", {})
    if EX_DATE.weekday() == 0:
        for m in momentum_signal(led, px, dry) + rotation_signal(led, px, dry) + mwu_rebalance(led, px, dry):
            msgs.append(m)
    month_key = today[:7]
    done = ctrl.setdefault("rebalance_done", {})
    if _is_month_first_trading_day() and done.get(month_key) != "all" and not dry:
        try:
            for m in (smallcap_rebalance(led, "F_小市值20", 20, 2500, True, px, dry)
                      + smallcap_rebalance(led, "F2_小市值5只神帖对照", 5, 4000, False, px, dry)
                      + cb_rebalance(led, px, dry)
                      + mwu_rebalance(led, px, dry, slot_name="H_MWU_CB融合",
                                      strategy="etf_mwu_cb",
                                      strategy_config="config/strategy/etf_mwu_cb_monthly.yaml")
                      + pead_rebalance(led, px, dry)
                      + reversal_rebalance(led, px, dry)
                      + buywatch_rebalance(led, px, dry)):
                msgs.append(m)
            done[month_key] = "all"
        except Exception as exc:
            log.error("月度调仓失败(不影响净值落账): %r", exc)
            msgs.append(f"【异常】月度调仓失败: {exc!r:.120}")
    summary = build_close_summary(led, px)
    if not dry:
        acts = led.setdefault("_actions", {}).setdefault(today, [])
        led["_actions"][today] = sorted(set(acts) | set(msgs))
        save_ledger(led)
        for m in msgs:
            notify(m, dedup_key=f"deck_close_{today}_{m[:12]}")
        notify(summary, dedup_key=f"deck_close_summary_{today}")
    return summary + ("\n调仓: " + "; ".join(msgs) if msgs else "\n无调仓")


def main() -> int:
    ap = argparse.ArgumentParser(description="多策略对比盘确定性执行器")
    ap.add_argument("--mode", choices=["intraday", "close"], required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="忽略交易时段判断(测试用)")
    args = ap.parse_args()
    global EX_DATE
    if not acquire_lock():
        print("另一实例运行中, 跳过本轮")
        return 0
    try:
        return _main_inner(args)
    finally:
        release_lock()


def _main_inner(args: argparse.Namespace) -> int:
    now = exchange_now() or datetime.now()
    global EX_DATE
    EX_DATE = now.date()
    if not args.force and not is_trading_session(now):
        print(f"非交易时段(交易所时间 {now:%m-%d %H:%M})，跳过 {args.mode} 轮")
        return 0
    out = run_intraday(args.dry_run) if args.mode == "intraday" else run_close(args.dry_run)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
