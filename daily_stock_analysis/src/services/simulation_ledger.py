# -*- coding: utf-8 -*-
"""模拟账本（蓝图 ③-A）：操作单的假设成交 vs 真实回填逐笔对比

两本账的语义（盘点 D4 判定）：
- 模拟账本（本模块）：操作单生成时按**限价假设全量成交**，费用模型与
  quant_research PaperBroker 数字对齐（佣金 max(率,5) + 卖出印花税 +
  过户费双边；滑点按 F6 解析口径单列不叠加进价格）。
- 真实账本（PortfolioService，唯一真源）：用户回填的真实成交。

对比价值：模拟与真实的偏差 = "要不要信它"的量化依据。
账本选型：JSON 文件（与操作单一致，单人轻方案）。
费率参数：mandate.yaml 声明为基线，与 QR 回测保持同源数字（测试锁定）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]      # 仓库根（mandate.yaml）
_LEDGER_PATH = (
    Path(__file__).resolve().parents[2] / "output" / "simulation_ledger"
    / "ledger.json"
)

# 与 quant_research trade/broker.py PaperBroker 默认/真实费率对齐的基线
# （ mandate 未声明 fees 时使用；真实费率 = 华泰万0.845 + 最低5，不免五）
BASELINE_FEES = {
    "commission": 0.0000845,
    "min_commission": 5.0,
    "stamp_duty": 0.0005,        # 仅卖出
    "transfer_fee": 0.00002,     # 双边
    "slippage_rate": 0.001,      # F6 解析口径：单列，不叠加价格
}


def load_fees() -> Dict[str, float]:
    """费率参数：mandate.yaml fees 段 > BASELINE_FEES。"""
    fees = dict(BASELINE_FEES)
    try:
        import yaml

        mandate_path = _ROOT / "mandate.yaml"
        if mandate_path.exists():
            mandate = yaml.safe_load(mandate_path.read_text(encoding="utf-8")) or {}
            fees.update(mandate.get("fees") or {})
    except Exception as exc:  # noqa: BLE001 - 费率回退基线
        logger.warning("mandate fees 读取失败，用基线: %r", exc)
    return fees


def calculate_costs(
    quantity: int, price: float, side: str, fees: Optional[Dict[str, float]] = None
) -> Dict[str, float]:
    """单笔交易费用（与 QR PaperBroker._calc_fee 同公式）。

    Returns:
        {commission, stamp_duty, transfer_fee, total, notional, slippage}
    """
    fees = {**BASELINE_FEES, **(fees or {})}
    notional = quantity * price
    commission = max(notional * fees["commission"], fees["min_commission"])
    transfer = notional * fees["transfer_fee"]
    stamp = notional * fees["stamp_duty"] if side == "sell" else 0.0
    return {
        "notional": round(notional, 2),
        "commission": round(commission, 4),
        "stamp_duty": round(stamp, 4),
        "transfer_fee": round(transfer, 4),
        "total": round(commission + transfer + stamp, 4),
        "slippage": round(notional * fees["slippage_rate"], 4),
    }


@dataclass
class SimEntry:
    """一笔模拟成交"""

    sheet_id: str
    trade_date: str
    symbol: str
    direction: str
    quantity: int
    limit_price: float          # 假设成交价 = 操作单限价
    costs: Dict[str, float]
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


class SimulationLedger:
    """模拟账本：JSON 持久化 + 按操作单记录 + 真实账本对比"""

    def __init__(self, ledger_path: Optional[Path] = None):
        self.path = Path(ledger_path) if ledger_path else _LEDGER_PATH
        self._data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"entries": []}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("模拟账本读取失败，重建: %r", exc)
            return {"entries": []}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=1),
            encoding="utf-8")

    # ── 写入 ──

    def record_sheet(self, sheet) -> List[SimEntry]:
        """把一张操作单的全部订单记为模拟成交（假设限价全量成交）。"""
        fees = load_fees()
        existing = {e["sheet_id"] + ":" + e["symbol"] + ":" + e["direction"]
                    for e in self._data["entries"]}
        recorded: List[SimEntry] = []
        for order in sheet.orders:
            key = f"{sheet.sheet_id}:{order.symbol}:{order.direction}"
            if key in existing:
                continue  # 幂等：同单同标的同方向不重复记
            costs = calculate_costs(order.quantity, order.limit_price,
                                    order.direction, fees)
            entry = SimEntry(
                sheet_id=sheet.sheet_id,
                trade_date=sheet.trade_date,
                symbol=order.symbol,
                direction=order.direction,
                quantity=order.quantity,
                limit_price=order.limit_price,
                costs=costs,
                created_at=datetime.now().isoformat(timespec="seconds"),
            )
            self._data["entries"].append(entry.to_dict())
            recorded.append(entry)
        if recorded:
            self._save()
        return recorded

    # ── 对比 ──

    def compare_with_real(
        self, service, *, account_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """模拟成交 vs 真实账本（PortfolioService 交易事件）逐笔对比。

        匹配键：(symbol 归一形态, direction, trade_date)——匹配不到真实侧
        = "未回填/未执行"，诚实列出。
        """
        rows: List[Dict[str, Any]] = []
        entries = self._data["entries"]
        real_index: Dict[tuple, Dict[str, Any]] = {}
        try:
            kwargs = {"page": 1, "page_size": 100}
            if account_id is not None:
                kwargs["account_id"] = account_id
            result = service.list_trade_events(**kwargs)
            for ev in result.get("events") or result.get("items") or []:
                key = (str(ev.get("symbol", "")).upper(),
                       str(ev.get("side", "")).lower(),
                       str(ev.get("trade_date", ""))[:10])
                real_index.setdefault(key, ev)
        except Exception as exc:  # noqa: BLE001 - 真实侧失败 → 全部 unmatched
            logger.warning("真实账本读取失败: %r", exc, exc_info=True)

        for e in entries:
            real = real_index.get(
                (str(e["symbol"]).upper(), e["direction"], e["trade_date"]))
            row = {
                "trade_date": e["trade_date"],
                "symbol": e["symbol"],
                "direction": e["direction"],
                "sim_quantity": e["quantity"],
                "sim_price": e["limit_price"],
                "sim_cost": e["costs"]["total"],
                "real_price": None,
                "real_cost": None,
                "matched": real is not None,
            }
            if real is not None:
                row["real_price"] = real.get("price")
                row["real_quantity"] = real.get("quantity")
            rows.append(row)
        matched = [r for r in rows if r["matched"]]
        price_diffs = [
            (r["real_price"] - r["sim_price"]) / r["sim_price"] * 100
            for r in matched
            if r["real_price"] and r["sim_price"]
        ]
        return {
            "rows": rows,
            "n_sim": len(rows),
            "n_matched": len(matched),
            "n_unmatched": len(rows) - len(matched),
            "avg_price_diff_pct": (
                round(sum(price_diffs) / len(price_diffs), 3)
                if price_diffs else None),
        }

    def render_comparison_text(self, comparison: Dict[str, Any]) -> str:
        lines = [
            f"【模拟 vs 真实】模拟 {comparison['n_sim']} 笔，"
            f"已回填 {comparison['n_matched']}，未回填 {comparison['n_unmatched']}"
        ]
        if comparison["avg_price_diff_pct"] is not None:
            lines.append(
                f"· 成交价平均偏差 {comparison['avg_price_diff_pct']:+.2f}%"
                "（正=真实买贵/卖便宜）")
        for r in comparison["rows"]:
            flag = "✓" if r["matched"] else "·"
            real_text = (f"真实 {r['real_price']} × {r['real_quantity']}"
                         if r["matched"] else "未回填")
            lines.append(
                f"{flag} {r['trade_date']} {r['direction']} {r['symbol']} "
                f"模拟 {r['sim_price']}×{r['sim_quantity']}"
                f" 费用 ¥{r['sim_cost']} | {real_text}")
        if comparison["n_sim"] == 0:
            lines.append("（模拟账本为空——/操作单 生成后自动记录）")
        return "\n".join(lines)
