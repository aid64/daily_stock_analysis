# -*- coding: utf-8 -*-
"""纸面交易服务：多策略对比盘唯一账本入口。

事务边界（每条都成立）：
    BEGIN IMMEDIATE → 事务内重放当前状态 → 重算订单键并过滤已执行 →
    调用纯执行核心（src/core/paper_execution）→ 成交事件与通知 outbox
    同事务插入 → COMMIT。行情获取、信号计算、网络发送都在事务外。

语义：
- 订单幂等：事件 UNIQUE(slot, order_key) + 事务内已执行键过滤，跨进程重试
  同一命令只会成交一次，第二次返回 DUPLICATE_ORDER 拒绝。
- 分红/公司行为：按 event_id 恰好入账一次（不按"每天一次"）。
- outbox 与成交同事务：outbox 写入失败则整笔回滚，之后重放整条命令即可
  （幂等键保证不会重复成交）。outbox 状态只到 queued/acknowledged，
  不冒充渠道"已送达"。
- 状态是事件的投影：现金取最近一条 cash_after，持仓由成交事件经
  core.consume_lots 重放；不维护第二份可漂移的余额表。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from src.core.paper_execution import (
    AssetClass,
    ExecutionContext,
    ExecutionResult,
    Intent,
    Lot,
    Quote,
    ValuationResult,
    consume_lots,
    execute_intents,
    order_key,
    value_positions,
)
from src.repositories.paper_repo import PaperStore, PaperStoreError

DEFAULT_DB_PATH = (Path(__file__).resolve().parents[2] / "output" / "simulation_ledger"
                   / "paper_deck.db")


@dataclass(frozen=True)
class PaperState:
    slot: str
    cash: float
    lots: tuple
    last_seq: int


def build_quote(symbol: str, info: Mapping) -> Quote:
    """runner 风格行情 dict → Quote。price 缺失/0 视为无效价，由核心拒绝。"""
    ts = info.get("ts") or info.get("timestamp")
    if isinstance(ts, str) and ts:
        try:
            from datetime import datetime
            ts = datetime.fromisoformat(ts)
        except ValueError:
            ts = None
    return Quote(symbol=symbol,
                 price=float(info.get("price") or 0.0),
                 timestamp=ts,
                 is_stale=bool(info.get("is_stale") or info.get("stale", False)))


class PaperTradingService:
    def __init__(self, db_path: str | Path | None = None):
        self.store = PaperStore(db_path or DEFAULT_DB_PATH)

    # ------------------------------------------------------------ 账户与状态
    def ensure_account(self, slot: str, starting_cash: float,
                       config_hash: str = "unset", created_day: date | str | None = None) -> None:
        day = created_day if created_day is None else (
            created_day if isinstance(created_day, str) else created_day.isoformat())
        self.store.ensure_account(slot, float(starting_cash), config_hash,
                                  day or date.today().isoformat())

    def load_state(self, slot: str) -> PaperState:
        return self._replay(self.store._conn(), slot)

    def _replay(self, conn, slot: str) -> PaperState:
        rows = conn.execute(
            "SELECT seq, event_type, payload, cash_after FROM events WHERE slot=? ORDER BY seq",
            (slot,)).fetchall()
        cash: Optional[float] = None
        lots_by: dict[str, list[Lot]] = {}
        last_seq = 0
        for seq, event_type, payload_s, cash_after in rows:
            last_seq = seq
            payload = json.loads(payload_s)
            if event_type == "opening":
                cash = float(payload.get("starting_cash", 0.0))
            elif event_type == "fill":
                symbol = str(payload["symbol"])
                qty = int(payload["qty"])
                buy_day = date.fromisoformat(str(payload["executed_day"]))
                if payload["side"] == "BUY":
                    lots_by.setdefault(symbol, []).append(
                        Lot(symbol, qty, float(payload["price"]), buy_day))
                else:
                    lots_by[symbol] = list(consume_lots(lots_by.get(symbol, []), qty))
            # dividend/adjustment 等只影响 cash_after，不动持仓
            if cash_after is not None:
                cash = float(cash_after)
        return PaperState(slot=slot, cash=float(cash or 0.0),
                          lots=tuple(l for ls in lots_by.values() for l in ls),
                          last_seq=last_seq)

    # ------------------------------------------------------------ 执行
    def execute(self, slot: str, trading_day: date, intents: Sequence[Intent],
                quotes: Mapping[str, Quote], ctx: ExecutionContext,
                extra_notifications: Iterable[tuple[str, str, str]] = ()) -> ExecutionResult:
        """撮合并入账。事件与 outbox 原子提交；冲突/约束失败整笔回滚可重放。"""
        key_list = [order_key(ctx, i) for i in intents]
        with self.store.transaction() as conn:
            state = self._replay(conn, slot)
            done = self.store.executed_keys(conn, slot, key_list)
            if done:
                from dataclasses import replace
                ctx = replace(ctx, executed_order_keys=ctx.executed_order_keys | frozenset(done))
            result = execute_intents(state.cash, state.lots, intents, quotes, ctx)

            running = state.cash
            for fill in result.fills:
                delta = (fill.notional - fill.fee) if fill.side == "SELL" else -(fill.notional + fill.fee)
                running += delta
                self.store.insert_event(
                    conn, slot, trading_day.isoformat(), "fill", fill.order_key,
                    {"symbol": fill.symbol, "side": fill.side, "qty": fill.qty,
                     "price": fill.price, "notional": fill.notional, "fee": fill.fee,
                     "asset_class": fill.asset_class.value,
                     "executed_day": fill.executed_day.isoformat()},
                    cash_after=round(running, 2))
                self.store.insert_outbox(
                    conn, "paper_fill", f"fill:{fill.order_key}",
                    f"{slot} {fill.side} {fill.symbol} x{fill.qty} @ {fill.price} "
                    f"(fee {fill.fee:.2f})",
                    slot, trading_day.isoformat())
            if result.fills and abs(round(running, 2) - result.cash) > 1e-6:
                raise PaperStoreError(
                    f"核心现金 {result.cash} 与事件流水 {round(running, 2)} 不一致——拒绝入账")
            for topic, dedup_key, content in extra_notifications:
                self.store.insert_outbox(conn, topic, dedup_key, content, slot,
                                         trading_day.isoformat())
        return result

    # ------------------------------------------------------------ 公司行为
    def record_dividend(self, slot: str, trading_day: date, event_id: str, symbol: str,
                        amount: float, detail: str = "") -> bool:
        """按事件恰好入账一次。重复 event_id 返回 False 且不改状态。"""
        order_key_div = f"div:{event_id}"
        with self.store.transaction() as conn:
            if self.store.executed_keys(conn, slot, [order_key_div]):
                return False
            state = self._replay(conn, slot)
            cash_after = round(state.cash + float(amount), 2)
            self.store.insert_event(
                conn, slot, trading_day.isoformat(), "dividend", order_key_div,
                {"symbol": symbol, "amount": round(float(amount), 2),
                 "event_id": event_id, "detail": detail},
                cash_after=cash_after)
            self.store.insert_outbox(
                conn, "paper_dividend", f"div:{event_id}",
                f"{slot} 分红入账 {symbol} +{float(amount):.2f} {detail}".strip(),
                slot, trading_day.isoformat())
        return True

    # ------------------------------------------------------------ 估值与 outbox
    def settle_valuation(self, slot: str, trading_day: date, quotes: Mapping[str, Quote],
                         symbol_asset_class: Optional[Mapping[str, AssetClass]] = None) -> ValuationResult:
        state = self.load_state(slot)
        valuation = value_positions(state.cash, state.lots, quotes, symbol_asset_class=symbol_asset_class)
        self.store.upsert_valuation(slot, trading_day.isoformat(), valuation.cash,
                                    valuation.market_value, valuation.equity,
                                    dict(valuation.unvalued), valuation.fully_valued)
        return valuation

    def pending_notifications(self, limit: int = 50) -> list:
        return self.store.outbox_rows(("pending",), limit=limit)

    def mark_notification(self, outbox_id: int, status: str, error: Optional[str] = None) -> None:
        self.store.mark_outbox(outbox_id, status, error)

    # ------------------------------------------------------------ 运维
    def backup(self, target: str | Path) -> Path:
        return self.store.backup(target)
