# -*- coding: utf-8 -*-
"""买入观察池服务（买入侧最小闭环）

解决"光卖不买"：卖出侧有用户规则授权，买入侧此前没有候选来源。
本服务从 market_scan 候选池（全市场规则漏斗，零 LLM）中按确定性
规则打分，输出每日买入观察列表。

诚实边界（铁律 4）：
  - 输出是【观点非信号】——打分规则未回测验证，不产生买卖指令、
    不进 order_sheet 状态机，只进决策包的"买入观察"附注区；
  - E2 择时防守区（转债估值分位 >80%）时照常展示，但明确标注
    择时风险——是否买入由用户裁决（人是执行引擎）；
  - 参数全部为结构值（不网格调参，预注册纪律），来源 mandate.yaml
    的漏斗参数 + 本文件声明的打分结构值。

数据容错：新浪降级快照缺 volume_ratio/turnover_rate/pe 时，
对应维度按"缺失不加分不扣分"处理，打分退化为涨幅+成交额排序。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ── 打分结构值（预注册：不调参，改值须升级说明）─────────────────
# 硬性过滤（不满足即出局，缺列容错放行）
CHANGE_PCT_BAND = (0.0, 7.0)        # 温和上行；上界挡掉涨停/近涨停（不追板）
VOLUME_RATIO_BAND = (0.8, 3.0)      # 量能温和；上界挡掉天量（防出货）
TURNOVER_BAND = (0.5, 10.0)         # 换手健康带
# 加分区间（满足 +2/+1，缺失 0 分）
CHANGE_PCT_SWEET = (1.0, 5.0)       # 温和上行 +2
VOLUME_RATIO_SWEET = (1.0, 2.5)     # 温和放量 +2
PE_SWEET_MAX = 30.0                  # 低估值 +2
TURNOVER_SWEET = (1.0, 5.0)          # 换手活跃 +1
DEFAULT_TOP_N = 5

# 北交所在 _normalize_scan_code 中排除：30% 涨跌幅/申报规则与沪深不同，
# 观察池只覆盖沪深。


@dataclass
class BuyCandidate:
    """单条买入观察候选"""
    symbol: str            # 6 位裸码（与持仓快照同形态）
    name: str
    close: float
    change_pct: float
    amount: float          # 当日成交额（元）
    volume_ratio: Optional[float]
    turnover_rate: Optional[float]
    pe: Optional[float]
    score: int
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol, "name": self.name,
            "close": round(self.close, 2),
            "change_pct": round(self.change_pct, 2),
            "amount": round(self.amount, 0),
            "volume_ratio": (round(self.volume_ratio, 2)
                             if self.volume_ratio is not None else None),
            "turnover_rate": (round(self.turnover_rate, 2)
                              if self.turnover_rate is not None else None),
            "pe": round(self.pe, 2) if self.pe is not None else None,
            "score": self.score, "reasons": list(self.reasons),
        }


def _to_float(value: Any) -> Optional[float]:
    try:
        f = float(value)
        return f if f == f else None   # NaN 检查（NaN != NaN）
    except (TypeError, ValueError):
        return None


def _normalize_scan_code(code: Any) -> tuple:
    """快照代码 → (6 位裸码, 是否北交所)。

    兼容两种形态：真实快照前缀形（sh600000 / bj920000）、
    测试/备用源后缀形（600000.SH / 920000.BJ）。
    """
    code = str(code or "").strip().lower()
    if code.startswith(("sh", "sz", "bj")) and len(code) == 8:
        return code[2:], code[:2] == "bj"
    if len(code) >= 8 and code[6] == ".":
        return code[:6], code[-2:] == "bj"
    return code, False


def score_candidates(
    candidates: pd.DataFrame,
    *,
    exclude_symbols: Optional[List[str]] = None,
    top_n: int = DEFAULT_TOP_N,
) -> List[BuyCandidate]:
    """确定性打分：硬过滤 → 结构值加分 → 得分/成交额排序取前 N。

    Args:
        candidates: 漏斗输出的候选池（market_scan.run_funnel().candidates）
        exclude_symbols: 已持仓代码（裸码 6 位），从观察池剔除
        top_n: 输出条数
    """
    exclude = {s.strip() for s in (exclude_symbols or []) if s}
    results: List[BuyCandidate] = []

    for _, row in candidates.iterrows():
        symbol, is_bj = _normalize_scan_code(row.get("code"))
        if is_bj or not symbol:
            continue
        if symbol in exclude:
            continue

        close = _to_float(row.get("close"))
        prev = _to_float(row.get("prev_close"))
        if close is None or close <= 0 or prev is None or prev <= 0:
            continue
        change_pct = (close / prev - 1) * 100

        # 硬过滤（缺列放行——新浪降级快照无量比/换手/PE）
        if not (CHANGE_PCT_BAND[0] <= change_pct <= CHANGE_PCT_BAND[1]):
            continue
        vr = _to_float(row.get("volume_ratio"))
        if vr is not None and not (VOLUME_RATIO_BAND[0] <= vr <= VOLUME_RATIO_BAND[1]):
            continue
        tr = _to_float(row.get("turnover_rate"))
        if tr is not None and not (TURNOVER_BAND[0] <= tr <= TURNOVER_BAND[1]):
            continue
        pe = _to_float(row.get("pe"))
        amount = _to_float(row.get("amount")) or 0.0

        # 加分（缺失维度 0 分）
        score = 0
        reasons: List[str] = []
        if CHANGE_PCT_SWEET[0] <= change_pct <= CHANGE_PCT_SWEET[1]:
            score += 2
            reasons.append(f"温和上行 {change_pct:+.1f}%")
        if vr is not None and VOLUME_RATIO_SWEET[0] <= vr <= VOLUME_RATIO_SWEET[1]:
            score += 2
            reasons.append(f"量能温和 量比{vr:.1f}")
        if pe is not None and 0 < pe <= PE_SWEET_MAX:
            score += 2
            reasons.append(f"估值不贵 PE{pe:.0f}")
        if tr is not None and TURNOVER_SWEET[0] <= tr <= TURNOVER_SWEET[1]:
            score += 1
            reasons.append(f"换手健康 {tr:.1f}%")
        if not reasons:
            reasons.append("涨幅/成交额达标（其余维度数据缺失）")

        results.append(BuyCandidate(
            symbol=symbol, name=str(row.get("name") or symbol),
            close=close, change_pct=change_pct, amount=amount,
            volume_ratio=vr, turnover_rate=tr, pe=pe,
            score=score, reasons=reasons))

    results.sort(key=lambda c: (-c.score, -c.amount))
    return results[:top_n]


def build_buy_watchlist(
    *,
    exclude_symbols: Optional[List[str]] = None,
    top_n: int = DEFAULT_TOP_N,
    snapshot_date: Optional[date] = None,
    snapshot: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """组装买入观察池（含数据新鲜度审计）。

    Args:
        snapshot: 已就绪的候选池快照（调用方预处理后注入，如补列后的行情表）。
            提供时不再读盘；None 时按 snapshot_date 从 market_scan 存储加载。

    Returns:
        {candidates: [...], snapshot_date, stale_days, source_note}
        数据不可用时 candidates 为空列表（决策包降级显示，不阻塞）。
    """
    from src.services.market_scan_service import load_snapshot, run_funnel

    out: Dict[str, Any] = {
        "candidates": [], "snapshot_date": "",
        "stale_days": None, "source_note": "",
    }
    if snapshot is not None:
        if snapshot.empty:
            out["source_note"] = "注入的候选池快照为空"
            return out
        out["snapshot_date"] = (snapshot_date or date.today()).isoformat()
    else:
        snapshot = load_snapshot(snapshot_date)
        if snapshot is None or snapshot.empty:
            # 快照缺失时回溯最近 7 天（管线未跑/节假日容错）
            for back in range(1, 8):
                d = date.fromordinal(date.today().toordinal() - back)
                snapshot = load_snapshot(d)
                if snapshot is not None and not snapshot.empty:
                    out["snapshot_date"] = d.isoformat()
                    break
            if snapshot is None or snapshot.empty:
                out["source_note"] = "候选池快照不可用（market_scan 未运行）"
                return out
        else:
            out["snapshot_date"] = (snapshot_date or date.today()).isoformat()

    try:
        funnel = run_funnel(snapshot)
    except Exception as exc:  # noqa: BLE001 - 漏斗失败降级为空池
        out["source_note"] = f"规则漏斗执行失败：{exc}"
        return out

    try:
        picks = score_candidates(funnel.candidates,
                                 exclude_symbols=exclude_symbols,
                                 top_n=top_n)
    except Exception as exc:  # noqa: BLE001
        out["source_note"] = f"买入打分失败：{exc}"
        return out

    out["candidates"] = [p.to_dict() for p in picks]
    if out["snapshot_date"]:
        try:
            stale = (date.today() - date.fromisoformat(out["snapshot_date"])).days
            out["stale_days"] = stale
            if stale > 3:
                out["source_note"] = (f"快照已 {stale} 天未更新"
                                      f"（{out['snapshot_date']}）——"
                                      "请检查 market_scan 管线步骤")
        except ValueError:
            pass
    return out
