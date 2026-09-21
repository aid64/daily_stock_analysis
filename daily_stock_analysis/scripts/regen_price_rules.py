# -*- coding: utf-8 -*-
"""按市场结构重新生成 price_cross 价位（审视第 1 条的 B 方案落地）

背景：原四档价位为"成本 ± 固定百分比"机械模板（000899 −3%/−5%/+5%/+15%，
002475 同构），未经用户确认、不反映个股波动率与市场结构。

生成方法（结构锚 + 收盘波幅，数据与破位诊断同源——本地日线）：
  止损一档（警戒）= 近 20 根收盘最低点          （跌破 = 20 日结构破坏）
  止损二档（硬）  = 最低点 − 1.5 × 近 20 日平均日波幅（结构破坏后确认离场）
  止盈一档        = 近 20 根收盘最高点          （上穿 = 动量突破 20 日高）
  止盈二档        = 最高点 + 1.5 × 平均日波幅

用法（在 daily_stock_analysis/ 下）：
  py scripts/regen_price_rules.py             # 干跑：只打印新旧对比
  py scripts/regen_price_rules.py --apply    # 写回 .env（只替换 price_cross，
                                             # 涨跌幅等其他规则原样保留）

纪律：价位随行情变化，建议每 1-2 周或显著波动后重跑；每次覆盖在
.env 中不留历史（决策快照/告警审计天然留痕）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]   # daily_stock_analysis/
sys.path.insert(0, str(_PROJECT_ROOT))

WINDOW = 20            # 结构窗口（20 根收盘）
BUFFER_ATR = 1.5       # 波幅缓冲倍数（近似 1.5×ATR，收盘价口径）
MIN_SPACING = 0.02     # 两档最小价差（A 股 0.01 tick × 2）


def _load_bars(symbol: str, days: int = 120):
    from datetime import date, timedelta

    from src.repositories.stock_repo import StockRepository

    end = date.today()
    rows = StockRepository().get_range(
        symbol, end - timedelta(days=days), end) or []
    return [(str(r.date), float(r.close)) for r in rows
            if float(getattr(r, "close", 0) or 0) > 0]


def compute_levels(bars):
    """结构锚价位：{stop1, stop2, take1, take2}（含波幅），None=数据不足。"""
    if len(bars) < WINDOW + 1:
        return None
    closes = [c for _, c in bars]
    window = closes[-WINDOW:]
    low, high = min(window), max(window)
    moves = [abs(closes[i] - closes[i - 1])
             for i in range(len(closes) - WINDOW, len(closes))]
    avg_move = sum(moves) / len(moves)
    stop1 = round(low, 2)
    stop2 = round(low - BUFFER_ATR * avg_move, 2)
    take1 = round(high, 2)
    take2 = round(high + BUFFER_ATR * avg_move, 2)
    # 防四舍五入把两档压平
    if stop1 - stop2 < MIN_SPACING:
        stop2 = round(stop1 - MIN_SPACING, 2)
    if take2 - take1 < MIN_SPACING:
        take2 = round(take1 + MIN_SPACING, 2)
    return {"stop1": stop1, "stop2": stop2,
            "take1": take1, "take2": take2,
            "low": low, "high": high, "avg_move": round(avg_move, 3),
            "as_of": bars[-1][0], "bars": len(bars)}


def _portfolio_symbols():
    try:
        from src.services.portfolio_service import PortfolioService

        snap = PortfolioService().get_portfolio_snapshot(
            cost_method="fifo", include_realtime=False)
        symbols = []
        for account in snap.get("accounts") or []:
            for p in account.get("positions") or []:
                if float(p.get("quantity") or 0) > 0:
                    digits = "".join(ch for ch in str(p.get("symbol"))
                                     if ch.isdigit())
                    symbols.append(digits if len(digits) == 6
                                  else str(p.get("symbol")))
        return symbols
    except Exception as exc:  # noqa: BLE001
        print(f"[regen] 持仓读取失败，改用手动指定: {exc!r}")
        return []


def _rewrite_env(env_path: Path, new_rules_json: str) -> bool:
    """只替换 AGENT_EVENT_ALERT_RULES_JSON 行，其余内容逐字节保留。"""
    text = env_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"^(AGENT_EVENT_ALERT_RULES_JSON\s*=\s*).*$", re.MULTILINE)
    if not pattern.search(text):
        print("[regen] .env 中未找到 AGENT_EVENT_ALERT_RULES_JSON，中止")
        return False
    new_text = pattern.sub(lambda m: m.group(1) + new_rules_json, text, count=1)
    env_path.write_text(new_text, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("symbols", nargs="*",
                        help="标的（缺省=当前持仓快照）")
    parser.add_argument("--apply", action="store_true",
                        help="写入 .env（默认干跑只打印）")
    args = parser.parse_args()

    symbols = args.symbols or _portfolio_symbols()
    if not symbols:
        print("无标的（持仓为空且未手动指定）——无可生成")
        return 1
    print(f"[regen] 标的: {symbols}")

    levels = {}
    for sym in symbols:
        bars = _load_bars(sym)
        lv = compute_levels(bars)
        if lv is None:
            print(f"[regen] {sym} 本地日线不足 {WINDOW + 1} 根，跳过")
            continue
        levels[sym] = lv
        print(f"[regen] {sym}（{lv['bars']} 根，截至 {lv['as_of']}）"
              f" 20日低 {lv['low']:.2f} / 高 {lv['high']:.2f}"
              f" / 日均波幅 {lv['avg_move']}")
        print(f"         止损 {lv['stop2']} / {lv['stop1']}"
              f" · 止盈 {lv['take1']} / {lv['take2']}")

    if not levels:
        return 1

    env_path = _PROJECT_ROOT / ".env"
    current = ""
    match = re.search(
        r"^AGENT_EVENT_ALERT_RULES_JSON\s*=\s*(.*)$",
        env_path.read_text(encoding="utf-8"), re.MULTILINE)
    if match:
        current = match.group(1).strip()

    try:
        entries = json.loads(current) if current else []
    except json.JSONDecodeError:
        print("[regen] 现有规则 JSON 无效，将整体重建 price_cross 部分")
        entries = []

    # 非目标标的的 price_cross + 所有其他类型规则原样保留
    kept = [e for e in entries
            if not (str(e.get("alert_type", "")).lower() == "price_cross"
                    and "".join(ch for ch in str(e.get("stock_code", ""))
                                if ch.isdigit()) in levels)]
    regen = []
    for sym, lv in levels.items():
        regen += [
            {"alert_type": "price_cross", "stock_code": sym,
             "direction": "below", "price": lv["stop1"]},
            {"alert_type": "price_cross", "stock_code": sym,
             "direction": "below", "price": lv["stop2"]},
            {"alert_type": "price_cross", "stock_code": sym,
             "direction": "above", "price": lv["take1"]},
            {"alert_type": "price_cross", "stock_code": sym,
             "direction": "above", "price": lv["take2"]},
        ]
    new_entries = kept + regen
    new_json = json.dumps(new_entries, ensure_ascii=False)

    print(f"[regen] 规则条数: {len(entries)} → {len(new_entries)}"
          f"（保留非 price_cross {len(kept) - sum(1 for e in kept if str(e.get('alert_type','')).lower() != 'price_cross')} 条 + 其他）")

    if not args.apply:
        print("[regen] 干跑完成（未写入）。确认后加 --apply")
        print(new_json)
        return 0

    if _rewrite_env(env_path, new_json):
        print("[regen] .env 已更新。注意：server 需重启加载新规则"
              "（决策引擎与告警 worker 都读单例配置）。")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
