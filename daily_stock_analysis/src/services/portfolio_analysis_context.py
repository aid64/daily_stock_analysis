# -*- coding: utf-8 -*-
"""持仓分析上下文构造（P0-A：打通 PortfolioService → 分析主链路）

此前 portfolio_context 只有 Web API 的孤儿路径会传入（手动持仓分析），
CLI/定时主链路实例化 StockAnalysisPipeline 时不传——LLM 的"持仓者建议"
永远基于空上下文。本模块把快照 → 分析上下文的构造收敛到服务层，
供 main.py 主链路与 bot 命令共用。

诚实边界：这里不做实时行情解析（API 版同样不解析），price_available
留给下游按缺失处理；构造失败一律静默降级为空——持仓上下文缺失不应
拖垮分析主流程。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _service():
    from src.services.portfolio_service import PortfolioService

    return PortfolioService()

DEFAULT_ACCOUNT_NAME = "默认账户"

# position dict → 分析上下文的透传键（与 api/v1/endpoints/portfolio.py
# 的 _resolve_position_analysis_context 返回键保持一致）
_POSITION_CONTEXT_KEYS = (
    "symbol",
    "market",
    "currency",
    "quantity",
    "avg_cost",
    "total_cost",
    "unrealized_pnl_base",
    "unrealized_pnl_pct",
    "price_source",
    "price_provider",
)


def get_or_create_default_account(service) -> Optional[Dict[str, Any]]:
    """取第一个活跃账户；不存在时创建默认账户（CN 股票账户）。

    返回 None 表示账户操作失败（DB 异常等），调用方按无持仓降级。
    """
    try:
        accounts = service.list_accounts(include_inactive=False)
        if accounts:
            first = dict(accounts[0])
            first.setdefault("account_id", _account_id(first))
            return first
        created = service.create_account(
            name=DEFAULT_ACCOUNT_NAME,
            broker=None,
            market="cn",
            base_currency="CNY",
        )
        created.setdefault("account_id", _account_id(created))
        return created
    except Exception as exc:  # noqa: BLE001 - 持仓上下文缺失不拖垮主流程
        logger.warning("portfolio account bootstrap failed: %r", exc)
        return None


def _account_id(account: Dict[str, Any]) -> Optional[int]:
    """create_account/list_accounts 返回 'id'，snapshot 侧用 'account_id'。"""
    value = account.get("account_id", account.get("id"))
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _position_to_context(account: Dict[str, Any], position: Dict[str, Any]) -> Dict[str, Any]:
    context: Dict[str, Any] = {
        "account_id": _account_id(account),
        "account_name": account.get("account_name"),
    }
    for key in _POSITION_CONTEXT_KEYS:
        if key in position:
            context[key] = position.get(key)
    return context


def build_position_context(
    service,
    *,
    symbol: str,
    account_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """单标的持仓上下文（与 API 孤儿路径同 shape）；无持仓返回 None。"""
    try:
        target = service._normalize_symbol_for_position(symbol)
        if not target:
            return None
        snapshot = service.get_portfolio_snapshot(account_id=account_id, cost_method="fifo")
        for account in snapshot.get("accounts") or []:
            for position in account.get("positions") or []:
                position_symbol = service._normalize_symbol_for_position(
                    str(position.get("symbol") or "")
                )
                if position_symbol != target:
                    continue
                try:
                    quantity = float(position.get("quantity") or 0)
                except (TypeError, ValueError):
                    quantity = 0.0
                if quantity > 0:
                    return _position_to_context(account, position)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("build_position_context(%s) failed: %r", symbol, exc)
        return None


def build_all_position_contexts() -> Dict[str, Dict[str, Any]]:
    """全量持仓上下文映射 {symbol: context}，供主链路 per-stock 注入。

    键为 normalize 后的代码；任何失败静默降级为空映射。
    """
    contexts: Dict[str, Dict[str, Any]] = {}
    try:
        service = _service()
        snapshot = service.get_portfolio_snapshot(cost_method="fifo")
        for account in snapshot.get("accounts") or []:
            for position in account.get("positions") or []:
                try:
                    quantity = float(position.get("quantity") or 0)
                except (TypeError, ValueError):
                    quantity = 0.0
                if quantity <= 0:
                    continue
                symbol = service._normalize_symbol_for_position(
                    str(position.get("symbol") or "")
                )
                if not symbol:
                    continue
                contexts[symbol] = _position_to_context(account, position)
    except Exception as exc:  # noqa: BLE001
        logger.warning("build_all_position_contexts failed: %r", exc)
    return contexts


def list_positions_text(limit: int = 20) -> str:
    """bot 用：当前持仓的纯文本摘要（无持仓给引导文案）。"""
    try:
        service = _service()
        snapshot = service.get_portfolio_snapshot(cost_method="fifo")
    except Exception as exc:  # noqa: BLE001
        return f"持仓读取失败：{exc!r}"

    rows: List[Dict[str, Any]] = []
    for account in snapshot.get("accounts") or []:
        for position in account.get("positions") or []:
            try:
                quantity = float(position.get("quantity") or 0)
            except (TypeError, ValueError):
                quantity = 0.0
            if quantity <= 0:
                continue
            rows.append({**position, "_account": account.get("account_name")})
    if not rows:
        return (
            "当前无持仓记录。\n"
            "登记方式：/持仓登记 600519 买入 100 1820.00\n"
            "（代码 方向 数量 价格，卖出同格式）"
        )
    lines = [f"当前持仓（{len(rows)} 只）："]
    for row in rows[:limit]:
        pnl_pct = row.get("unrealized_pnl_pct")
        # unrealized_pnl_pct 已是百分数形式（-26.96 表示 -26.96%）
        pnl_text = f"{float(pnl_pct):+.2f}%" if isinstance(pnl_pct, (int, float)) else "—"
        if float(pnl_pct) < -100:  # 价格源异常时的不可能值 → 不展示
            pnl_text = "（现价不可信）"
        lines.append(
            f"  {row.get('symbol')} {row.get('quantity')}股 "
            f"成本 {row.get('avg_cost')} 浮动 {pnl_text}"
        )
    if len(rows) > limit:
        lines.append(f"  …（其余 {len(rows) - limit} 只省略）")
    return "\n".join(lines)


def build_target_position_caps(
    *,
    account_id: Optional[int] = None,
    opportunity_tiers: Optional[Dict[str, str]] = None,
    stop_losses: Optional[Dict[str, float]] = None,
    current_drawdown_pct: Optional[float] = None,
    include_realtime: bool = True,
    app_config=None,
) -> Dict[str, Dict[str, Any]]:
    """①-A：三层仓位管理（position_sizer）接到持仓快照上。

    输入：PortfolioService 快照（每账户 total_equity + positions）。
    输出：{symbol: target_value/target_quantity/target_pct/capped_by/...}。

    与裸 PositionSizer 的差异（接线层的增量价值）：
    - compute_batch 的 max_total_exposure 是逐标的独立 cap，各标的都触顶时
      sum 仍可超限——此处做**全局二次裁剪**（等比例缩量，手数精度留给
      操作单生成阶段的整数手算法）。
    - entry_price 缺失（离线无实时价）回退 avg_cost；回退发生的标的在
      输出中标记 price_fallback=True。
    - 构造失败静默降级为空映射（持仓上下文缺失不拖垮主流程）。
    """
    try:
        from src.services.position_sizer import build_position_sizer_from_config

        service = _service()
        snapshot = service.get_portfolio_snapshot(
            account_id=account_id, cost_method="fifo",
            include_realtime=include_realtime,
        )
        sizer = build_position_sizer_from_config(app_config)
        tiers = opportunity_tiers or {}
        stops = stop_losses or {}

        outputs: Dict[str, Dict[str, Any]] = {}
        for account in snapshot.get("accounts") or []:
            # 净值语义：现金 + Σ(参考价×数量)。实时价可用时 = 真实净值；
            # 离线（last_price=0 → 回退 avg_cost）= 成本估值净值，保证
            # 盘前无实时价也能计算仓位上限。
            cash = float(account.get("total_cash") or 0)
            equity = cash
            for position in account.get("positions") or []:
                try:
                    qty = float(position.get("quantity") or 0)
                except (TypeError, ValueError):
                    qty = 0.0
                if qty <= 0:
                    continue
                ref = position.get("last_price")
                try:
                    ref = float(ref) if ref is not None else 0.0
                except (TypeError, ValueError):
                    ref = 0.0
                if ref <= 0:
                    try:
                        ref = float(position.get("avg_cost") or 0)
                    except (TypeError, ValueError):
                        ref = 0.0
                equity += ref * qty
            if equity <= 0:
                continue
            candidates = []
            metas = []
            for position in account.get("positions") or []:
                try:
                    quantity = float(position.get("quantity") or 0)
                except (TypeError, ValueError):
                    quantity = 0.0
                if quantity <= 0:
                    continue
                symbol = service._normalize_symbol_for_position(
                    str(position.get("symbol") or "")
                )
                if not symbol:
                    continue
                entry = position.get("last_price")
                try:
                    entry = float(entry) if entry is not None else None
                except (TypeError, ValueError):
                    entry = None
                price_fallback = False
                if not entry or entry <= 0:
                    try:
                        entry = float(position.get("avg_cost") or 0)
                    except (TypeError, ValueError):
                        entry = 0.0
                    price_fallback = entry > 0
                if entry <= 0:
                    continue
                stop = stops.get(symbol)
                candidates.append({
                    "entry_price": entry,
                    "stop_loss_price": float(stop) if stop is not None else None,
                    "opportunity_tier": tiers.get(symbol),
                })
                metas.append((symbol, entry, price_fallback))

            if not candidates:
                continue

            results = sizer.compute_batch(
                account_equity=equity,
                candidates=candidates,
                current_drawdown_pct=current_drawdown_pct,
            )
            for (symbol, entry, price_fallback), result in zip(metas, results):
                outputs[symbol] = {
                    "account_id": account.get("account_id"),
                    "equity": round(equity, 2),
                    "target_value": round(result.final_value, 2),
                    "target_quantity": result.final_quantity,
                    "target_pct": round(result.final_pct_of_equity, 2),
                    "capped_by": result.layer1_base.capped_by,
                    "layer2_multiplier": result.layer2_net_worth.multiplier,
                    "layer3_weight": result.layer3_opportunity.weight,
                    "entry_price": entry,
                    "price_fallback": price_fallback,
                }

        # 全局二次裁剪：总敞口 ≤ max_total_exposure_pct
        max_total_pct = float(
            getattr(app_config, "position_sizer_max_total_exposure_pct", 95.0)
        ) if app_config is not None else 95.0
        total_value = sum(o["target_value"] for o in outputs.values())
        equity_ref = max((o["equity"] for o in outputs.values()), default=0.0)
        cap_value = equity_ref * max_total_pct / 100.0
        if equity_ref > 0 and total_value > cap_value:
            scale = cap_value / total_value
            for symbol, cap in outputs.items():
                scaled_value = cap["target_value"] * scale
                entry = cap["entry_price"]
                cap["target_value"] = round(scaled_value, 2)
                cap["target_quantity"] = (
                    int(scaled_value / entry) if entry > 0 else 0
                )
                cap["target_pct"] = round(
                    scaled_value / equity_ref * 100, 2
                ) if equity_ref > 0 else 0.0
                cap["capped_by"] = "global_total_exposure"
        return outputs
    except Exception as exc:  # noqa: BLE001 - 仓位上限缺失不拖垮主流程
        logger.warning("build_target_position_caps failed: %r", exc)
        return {}
