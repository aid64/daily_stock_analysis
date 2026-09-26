# -*- coding: utf-8 -*-
"""
===================================
规则确认命令（v5.0 计划 U0：意图归化）
===================================

/规则确认 列出清单 | /规则确认 <编号> 确认|停用 [备注]

v2.0 审计#1 的关闭入口：系统每日执行的规则（价位锚/集中度/E2 门/
打新/逆回购）此前没有一条经过用户确认——"人不会执行自己没有同意
过的纪律"。本命令把每条规则的"做什么/依据/验证状态"列给用户，由
用户逐条签署：确认 / 停用（附备注可选）。

存储与消费在 `src/services/rule_confirmation_service.py`（台账旁路
JSONL，不写回 strategies.yaml；组合器与策略引擎经 is_rule_enabled
查询实现"确认/停用即时生效"）。本模块只保留规则清单与展示逻辑。
"""

from typing import Dict, List

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse
# 兼容 re-export：既有测试/调用方仍从本模块导入这些符号
from src.services.rule_confirmation_service import (  # noqa: F401
    load_rule_states,
    record_rule_action,
)

# ── 规则清单（U0 送审对象）─────────────────────────────────────
# 事实依据以 strategies.yaml 系统条目 + 各模块真实读取处为准；
# params_source 字段代码不解析，这里是"规则级"清单而非注册表投影。
RULE_INVENTORY: List[Dict[str, str]] = [
    {
        "id": "R1",
        "name": "用户价格规则（止损/止盈）",
        "does": "按结构锚价位（近20日高/低±1.5×日均波幅）对持仓产出卖出/减仓腿",
        "basis": ".env 价位规则段（scripts/regen_price_rules.py 按行情重生）",
        "validation": "未回测。P0.9 回放：价位锚 window 是真正决定卖出触发的参数",
    },
    {
        "id": "R2",
        "name": "组合集中度（35%）",
        "does": "单票市值占比超阈值时减仓；账户过小时保底一手分批降低（P1.5）",
        "basis": "mandate.yaml/config 阈值 35%（行业默认值）",
        "validation": "未回测。09-24 单的 002475\"卖100留100\"即此规则产出",
    },
    {
        "id": "R3",
        "name": "E2 估值择时门",
        "does": "转债估值分位 >80% 时组合进防守（现金 action 收紧买入）",
        "basis": "quant_research PIT 转债分位（expanding 口径）",
        "validation": "quant 侧实验产物：E2 分位择时 pass（E1 合成因子 fail 不涉及）",
    },
    {
        "id": "R4",
        "name": "转债打新（cb_new_issue）",
        "does": "新债申购/缴款/上市三段式提醒，决策包盘前汇总",
        "basis": "bond_new_issue_calendar（2026 年 45 只、32 只顶格实证）",
        "validation": "规则性正期望（信用申购零成本、债底封顶下行），免回测 active",
    },
    {
        "id": "R5",
        "name": "国债逆回购窗口（reverse_repo_window）",
        "does": "月末/季末/节假日前 1 天期 GC001 现金管理建议（金额=可用现金取整）",
        "basis": "strategies.yaml 参数（min_cash 1000 等）",
        "validation": "无风险利率类规则性正期望，免回测 active",
    },
]

_ACTION_MAP = {"确认": "confirmed", "停用": "disabled"}
_STATE_TEXT = {"confirmed": "已确认", "disabled": "已停用"}


def _state_text(rule_id: str, states: Dict[str, Dict[str, str]]) -> str:
    rec = states.get(rule_id)
    if not rec:
        return "⚪ 未确认"
    action = str(rec.get("action") or "")
    label = _STATE_TEXT.get(action, str(action))
    return f"{label}（{rec.get('at', '?')}）"


class RuleConfirmCommand(BotCommand):
    """规则确认：把系统默认规则变成用户签署的纪律（U0）"""

    @property
    def name(self) -> str:
        return "rule_confirm"

    @property
    def aliases(self) -> List[str]:
        return ["规则确认", "规则", "ruleconfirm"]

    @property
    def description(self) -> str:
        return "逐条确认/停用系统执行的交易规则（意图归化）"

    @property
    def usage(self) -> str:
        return "/规则确认 [编号 确认|停用 [备注]]"

    # ── 展示 ──
    @staticmethod
    def _render_list() -> str:
        states = load_rule_states()
        lines = ["【系统规则清单——请逐条签署】"]
        for rule in RULE_INVENTORY:
            lines.append(
                f"\n{rule['id']}｜{rule['name']}｜{_state_text(rule['id'], states)}\n"
                f"· 做什么：{rule['does']}\n"
                f"· 依据：{rule['basis']}\n"
                f"· 验证状态：{rule['validation']}")
        lines.append(
            "\n——\n回应方式：/规则确认 <编号> 确认 [备注] 或 /规则确认 <编号> 停用 [备注]\n"
            "停用即时生效（决策包/策略引擎不再产出该规则的动作）；确认恢复产出。")
        return "\n".join(lines)

    # ── 执行 ──
    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        if not args:
            return BotResponse.text_response(self._render_list())
        return BotResponse.text_response(self._execute_action(args))

    @staticmethod
    def _execute_action(args: List[str]) -> str:
        from datetime import datetime

        rule_id_raw = args[0].strip().upper()
        if not rule_id_raw.startswith("R"):
            rule_id_raw = f"R{rule_id_raw}"
        rule = next((r for r in RULE_INVENTORY if r["id"] == rule_id_raw), None)
        if rule is None:
            valid = "/".join(r["id"] for r in RULE_INVENTORY)
            return f"未知规则编号：{args[0]}。有效编号：{valid}\n先发 /规则确认 查看清单。"

        if len(args) < 2:
            return ("缺少动作。格式：/规则确认 <编号> 确认|停用 [备注]\n"
                    f"例：/规则确认 {rule_id_raw} 停用 阈值太激进")
        action_cn = args[1].strip()
        action = _ACTION_MAP.get(action_cn)
        if action is None:
            return f"动作必须是 确认/停用，收到：{action_cn}"

        note = " ".join(args[2:]).strip()
        record_rule_action(rule_id_raw, action, note=note)
        tail = f"，备注：{note}" if note else ""
        return (f"已记录：{rule['name']} → {_STATE_TEXT[action]}（"
                f"{datetime.now().isoformat(timespec='seconds')}）{tail}\n"
                f"发 /规则确认 可随时查看最新台账状态。")
