# -*- coding: utf-8 -*-
"""转债信号桥（蓝图 ①-C）：读取 quant_research 的 current_plan.json 契约

跨项目边界（docs/agents/domain.md）：两项目独立运行、禁止互相 import——
集成只走 `quant_research/output/paper_tracking/current_plan.json` 文件
（schema=cb_current_plan_v1，由 quant_research/scripts/run_paper_tracking.py
的 --export-plan / 例行运行导出）。

本模块职责：读契约 → 校验 → 权重×资金基数 → 整手目标数量。
失败一律返回 None / 空并记日志——信号源缺失时操作单降级为
"信号源不可用"，绝不拖垮命令。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PLAN_PATH = (
    PROJECT_ROOT.parent / "quant_research" / "output" / "paper_tracking"
    / "current_plan.json"
)
SUPPORTED_SCHEMA = "cb_current_plan_v1"


def canonical_symbol(symbol: str) -> str:
    """统一到 PortfolioService 存储形态（SH113062 / SZ123089）。

    契约侧是 PIT 后缀形态（113062.SH），快照侧是前缀形态——不归一的话
    同一只债会被 diff 当成两个标的（先买入再"缺价持有"）。
    """
    digits = "".join(ch for ch in symbol if ch.isdigit())
    if len(digits) != 6:
        return symbol.strip().upper()
    if digits.startswith("1"):
        # 转债：11x/118 沪、12x 深
        return ("SH" if digits.startswith("11") else "SZ") + digits
    parts = symbol.split(".")
    if len(parts) == 2 and parts[1].upper() in ("SH", "SZ"):
        return f"{parts[1].upper()}{digits}"
    return symbol.strip().upper()


def lot_size_for(symbol: str) -> int:
    """与 order_sheet_service 同口径：6 位数字且 11/12 开头 = 转债 10 张。"""
    digits = "".join(ch for ch in symbol if ch.isdigit())
    if len(digits) == 6 and digits.startswith(("11", "12")):
        return 10
    return 100


def load_cb_plan(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """读取并校验信号契约；缺失/损坏/schema 不符 → None。"""
    plan_path = Path(path) if path else DEFAULT_PLAN_PATH
    if not plan_path.exists():
        logger.info("CB 信号契约不存在: %s", plan_path)
        return None
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("CB 信号契约读取失败: %r", exc)
        return None
    if plan.get("schema") != SUPPORTED_SCHEMA:
        logger.warning("CB 信号契约 schema 不支持: %r", plan.get("schema"))
        return None
    targets = plan.get("targets")
    if not isinstance(targets, dict) or not targets:
        logger.info("CB 信号契约为空计划（无目标）")
        return None
    return plan


def cb_plan_to_targets(
    plan: Dict[str, Any],
    *,
    capital_override: Optional[float] = None,
) -> Dict[str, int]:
    """权重 → 整手目标数量：target_qty = floor(weight×资金 / t1_close / lot)×lot

    权重之和不必为 1（信号源可能含 buffer 零权重标的——零权重产出 0，
    即"清出"指令）。
    """
    capital = float(capital_override or plan.get("capital_base") or 0)
    targets: Dict[str, int] = {}
    for symbol, payload in (plan.get("targets") or {}).items():
        try:
            weight = float(payload.get("weight") or 0)
            t1_close = float(payload.get("t1_close") or 0)
        except (TypeError, ValueError, AttributeError):
            logger.warning("跳过非法信号条目 %s", symbol)
            continue
        if t1_close <= 0:
            logger.warning("信号条目 %s 无有效 T-1 收盘价，跳过", symbol)
            continue
        lot = lot_size_for(symbol)
        target_value = weight * capital
        qty = int(target_value / t1_close // lot) * lot
        targets[canonical_symbol(symbol)] = max(qty, 0)
    return targets
