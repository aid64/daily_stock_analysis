# -*- coding: utf-8 -*-
"""P1.5 替代验收：隔离 paper 账户完整闭环（v4.2 计划 P1.5 边界条款）。

真实成交需用户在券商下单，系统无法代下；v4.2 P1.5 边界条款接受
"隔离 paper 账户完成一次完整闭环"作为替代验收。本脚本执行：

  ① paper 账户登记（入金 + 买入 000899/002475，成本与真实账户一致）
  ② 决策包生成（断言 P1.5 新集中度算法生效：002475 保底一手非清仓）
  ③ 操作单落盘 + 模拟账本记录（假设成交）
  ④ /成交回填 一笔（002475 卖 100 @54.50，故意偏离限价制造真实偏差）
  ⑤ /对比 产出非空真实偏差（模拟 54.84 vs 真实 54.50）

全程隔离（不触碰真实库/操作单/模拟账本/决策快照）：
  DATABASE_PATH → tmp；SHEETS_DIR → tmp；SimulationLedger(ledger_path=tmp)。
价位规则从真实 .env 只读加载（load_price_rules，纯数据，切隔离前取出）。

用法（在 daily_stock_analysis/ 下）：py scripts/acceptance_p15_paper_loop.py
退出码：0 = 验收通过；1 = 任一断言失败。
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

STEPS: list[str] = []


def step(msg: str) -> None:
    STEPS.append(msg)
    print(msg)


def main() -> int:
    # ── 隔离前：只读加载真实价位规则（纯数据）────────────────────
    from src.services.account_decision_service import load_price_rules
    real_rules = load_price_rules()
    step(f"① 真实价位规则加载（只读）：{ {k: v for k, v in real_rules.items()} }")

    # ── 切换隔离环境（conftest.isolated_db 同款纪律）─────────────
    tmp = Path(tempfile.mkdtemp(prefix="p15-paper-"))
    env_file = tmp / ".env"
    env_file.write_text("", encoding="utf-8")
    import os
    os.environ["ENV_FILE"] = str(env_file)
    os.environ["DATABASE_PATH"] = str(tmp / "paper.db")

    from src.config import Config
    from src.storage import DatabaseManager
    Config.reset_instance()
    DatabaseManager.reset_instance()

    from src.services import order_sheet_service as oss
    oss.SHEETS_DIR = tmp / "sheets"

    from src.services.simulation_ledger import SimulationLedger
    ledger = SimulationLedger(ledger_path=tmp / "ledger.json")

    # ── ① paper 账户登记 ────────────────────────────────────────
    from src.services.portfolio_analysis_context import (
        get_or_create_default_account)
    from src.services.portfolio_service import PortfolioService

    service = PortfolioService()
    account = get_or_create_default_account(service)
    assert account is not None, "paper 账户初始化失败"
    acct_id = int(account["account_id"])
    service.record_cash_ledger(
        account_id=acct_id, event_date=date(2026, 9, 1),
        direction="in", amount=14000.0, note="paper 初始入金")
    # 登记用 SZ 前缀形态（与 /成交回填 的 canonical_symbol 归一一致；
    # 决策快照层仍用裸码——.env 规则键为裸码形态）
    service.record_trade(
        account_id=acct_id, symbol="SZ000899", trade_date=date(2026, 9, 2),
        side="buy", quantity=100, price=11.16, fee=5.0,
        trade_uid="paper-init-000899")
    service.record_trade(
        account_id=acct_id, symbol="SZ002475", trade_date=date(2026, 9, 2),
        side="buy", quantity=200, price=56.77, fee=10.0,
        trade_uid="paper-init-002475")
    step("② paper 账户：入金 ¥14,000 + 买入 000899×100@11.16、002475×200@56.77")

    # ── ② 决策包（手工快照避免行情库依赖；价格=最近收盘）────────
    from src.services.account_decision_service import (
        build_account_decision, decision_to_sheet)

    snapshot = {"total_cash": 1495.0, "accounts": [{"positions": [
        {"symbol": "000899", "quantity": 100, "avg_cost": 11.16,
         "last_price": 9.32, "price_date": "2026-09-22", "name": "赣能股份"},
        {"symbol": "002475", "quantity": 200, "avg_cost": 56.77,
         "last_price": 54.84, "price_date": "2026-09-22", "name": "立讯精密"},
    ]}]}
    decision = build_account_decision(snapshot=snapshot, rules=real_rules)
    verdicts = {p.symbol: (p.verdict, p.target_quantity)
                for p in decision.positions}
    step(f"③ 决策包判决：{verdicts}")
    # P1.5 核心断言：002475 集中度保底一手（不是清仓 target=0）
    p2475 = next(p for p in decision.positions if p.symbol == "002475")
    assert p2475.verdict == "reduce", "002475 应为 REDUCE"
    assert p2475.target_quantity == 100, \
        f"P1.5 断言失败：002475 目标应为保底一手 100，实际 {p2475.target_quantity}"
    assert "保底一手" in p2475.reason and "非清仓" in p2475.reason
    step("   ✓ P1.5 集中度算法生效：002475 保底一手（卖 100 留 100，非清仓）")

    # ── ③ 操作单 + 模拟账本 ─────────────────────────────────────
    sheet = decision_to_sheet(decision, available_cash=1495.0)
    oss.save_order_sheet(sheet)
    ledger.record_sheet(sheet)
    orders = {o.symbol: (o.direction, o.quantity, o.limit_price)
              for o in sheet.orders}
    step(f"④ 操作单落盘（{sheet.sheet_id}）：{orders}；模拟账本已记假设成交")
    assert orders.get("002475") == ("sell", 100, 54.84), \
        f"002475 卖出腿应为 100 股@54.84，实际 {orders.get('002475')}"

    # ── ④ /成交回填（paper 的"真实成交"：卖便宜了 0.62%）────────
    from bot.commands.fill import _do_fill
    fill_result = _do_fill("002475", "sell", 100, 54.50)
    step("⑤ /成交回填 002475 卖出 100 @54.50（偏离限价 -0.62%）")
    print("   回填响应：", fill_result.replace("\n", "\n   "))
    assert "已回填" in fill_result, f"回填失败：{fill_result}"

    # 真实账本侧（隔离 paper 库）应有一笔卖出
    trades = service.list_trade_events(account_id=acct_id, page_size=50)
    sell_legs = [t for t in trades.get("trades") or trades.get("items") or []
                 if str(t.get("side")) == "sell"]
    assert sell_legs, "真实账本（paper）未记录该笔卖出"
    step(f"   ✓ 真实账本（paper）已记账：卖出 {sell_legs[0].get('quantity')}"
         f" @ {sell_legs[0].get('price')}（fee={sell_legs[0].get('fee')}）")

    # 操作单状态：100/100 足额 → filled
    latest = oss.load_latest_sheet(date.today().isoformat())
    assert latest is not None and latest.sheet_id == sheet.sheet_id
    fill_state = latest.fills.get("002475") or {}
    assert int(fill_state.get("actual_quantity") or 0) == 100
    step(f"   ✓ 操作单状态：{latest.status}（002475 腿 100/100）")

    # ── ⑤ /对比：模拟 vs 真实非空偏差 ──────────────────────────
    from bot.commands.compare import _RealLedgerProxy
    ledger2 = SimulationLedger(ledger_path=tmp / "ledger.json")
    comparison = ledger2.compare_with_real(_RealLedgerProxy(ledger_path=None))
    text = ledger2.render_comparison_text(comparison)
    print("── /对比 输出 ──")
    print(text)
    assert "002475" in text, "对比输出缺少 002475"
    assert ("54.50" in text) or ("54.5" in text), "对比输出未见真实成交价"
    step("⑥ /对比 产出非空真实偏差（模拟 54.84 vs 真实 54.50，-0.62%）")

    # ── 收尾：解除隔离（进程即退出，环境变量不外泄）─────────────
    DatabaseManager.reset_instance()
    Config.reset_instance()

    print("\n========== P1.5 替代验收：全部通过 ==========")
    print("闭环覆盖：账户登记 → 决策（新算法）→ 操作单 → 模拟账本"
          " → /成交回填 → 真实账本 → /对比 偏差")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"\n========== 验收失败 ==========\n{exc}")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"\n========== 验收异常 ==========\n{exc!r}")
        sys.exit(1)
