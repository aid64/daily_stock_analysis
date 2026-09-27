# -*- coding: utf-8 -*-
"""
===================================
轮动信号命令（v5.0 计划：影子跟踪展示）
===================================

/轮动信号 展示 etf_momentum（17 池）影子跟踪的最新信号。

数据来源：quant_research/output/etf_momentum_tracking/signals.jsonl
（quant 侧 scripts/track_etf_momentum_signals.py 收盘后追加，口径与回测
同源——直接调用 momentum_scores）。跨项目只走文件契约，无 import。

边界（横幅强制）：影子跟踪·非投资建议·不落单——策略状态 registered，
实盘禁令维持（HANDOFF 0.3），active 需 holdout 评估 + 买侧授权。
"""

import json
from pathlib import Path
from typing import List, Optional

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse

# 跨项目文件契约（与 strategy_intake 同模式：只读文件，不 import 代码）
SIGNAL_LOG = (
    Path(__file__).resolve().parents[3]
    / "quant_research" / "output" / "etf_momentum_tracking" / "signals.jsonl")

# 名称对照（来源 config/strategy/etf_momentum.yaml universe 注释，仅展示用）
ETF_NAMES = {
    "510300.SH": "沪深300ETF", "510500.SH": "中证500ETF",
    "159915.SZ": "创业板ETF", "512100.SH": "中证1000ETF",
    "588000.SH": "科创50ETF", "510050.SH": "上证50ETF",
    "512880.SH": "证券ETF", "515790.SH": "光伏ETF",
    "515030.SH": "新能源车ETF", "512480.SH": "半导体ETF",
    "515170.SH": "食品饮料ETF", "512010.SH": "医药ETF",
    "159531.SZ": "中证2000ETF", "512890.SH": "红利低波ETF",
    "518880.SH": "黄金ETF", "511880.SH": "银华日利(货币)",
    "511010.SH": "国债ETF",
}


def _latest_signal(path: Path) -> Optional[dict]:
    """读最新一条信号；坏行跳过；文件缺失/空返回 None。"""
    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    latest: Optional[dict] = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and rec.get("strategy") == "etf_momentum":
            latest = rec
    return latest


def _name(symbol: str) -> str:
    return ETF_NAMES.get(symbol, symbol)


class RotationSignalCommand(BotCommand):
    """轮动信号：ETF 动量影子跟踪展示（registered 态，不落单）"""

    @property
    def name(self) -> str:
        return "rotation_signal"

    @property
    def aliases(self) -> List[str]:
        return ["轮动信号", "影子信号", "信号"]

    @property
    def description(self) -> str:
        return "ETF 动量轮动影子信号（影子跟踪·非投资建议·不落单）"

    @property
    def usage(self) -> str:
        return "/轮动信号"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        return BotResponse.text_response(self._render())

    @staticmethod
    def _render(log_path: Optional[Path] = None) -> str:
        sig = _latest_signal(log_path or SIGNAL_LOG)
        if sig is None:
            return ("还没有影子信号记录——quant 侧跟踪脚本每个收盘后运行一次"
                    "（scripts/track_etf_momentum_signals.py，口径与回测同源）。")
        params = sig.get("params") or {}
        lines = [
            "【ETF 动量轮动 · 影子跟踪】",
            "⚠ 影子跟踪·非投资建议·不落单（策略 registered，实盘禁令维持）",
            f"数据截至：{sig.get('as_of')}（17 池 / {params.get('windows', ['?'])[0]}"
            f" 日动量 / MA{params.get('ma_filter')} / top{params.get('top_k')}）",
            f"下一调仓日：{sig.get('next_rebalance_day')}"
            f"（{params.get('rebalance_freq')}，调仓日收盘算信号→次日开盘成交）",
        ]
        if sig.get("defensive_mode"):
            lines.append("防御态：无候选通过过滤 → 全仓 "
                         f"{_name(str(sig.get('params', {}).get('defensive', '')))}")
        else:
            lines.append("入选（等权）：")
            for i, sym in enumerate(sig.get("selected") or [], 1):
                score = next((s["score"] for s in sig.get("scores") or []
                              if s["symbol"] == sym), None)
                score_text = f"{score:+.4f}" if score is not None else "?"
                lines.append(f"  {i}. {sym} {_name(sym)}（{score_text}）")
        scores = sig.get("scores") or []
        if scores:
            lines.append(f"过闸候选 {len(scores)} 只"
                         f"（其余被 MA250/绝对动量/流动性过滤）")
        caveats = sig.get("caveats") or []
        for c in caveats[1:2]:  # 口径局限一条即可，横幅已含第一条
            lines.append(f"口径备注：{c}")
        lines.append("手动跟单参考：调仓日 14:50 前按现价下单"
                     "（股票 ETF T+1 当日不可卖；本金小先按卫星规模 20-30% 思考）")
        return "\n".join(lines)
