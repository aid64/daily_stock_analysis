# -*- coding: utf-8 -*-
"""多策略对比盘槽位注册表：策略身份、资产类别与风控作用域的单一真源。

策略函数只负责产出交易意图；本注册表声明每个槽位的身份契约，
供执行适配器（compare_deck_runner.apply_orders）、风控层（Phase 3）
与审计消费。新增槽位必须先在此登记，否则执行适配器拒绝交易。

风险作用域（risk_scope）决定哪些全局风险闸适用于该槽位——
修复"E2 转债防御减半股票槽"这类跨资产传染的依据（spec Phase 3）：
    etf_cycle   ETF 轮动类（ERP/熔断适用，E2 不适用）
    stock_cycle 股票选股类（ERP/熔断适用，E2 不适用）
    cb          转债类（E2/信用事件适用，ERP 不适用）
    benchmark   被动基准（无主动风控减仓）
    cash_mgmt   现金管理（逆回购/打新，无股票风控）
    event       事件套利（逐事件人工规则，不适用总闸减半）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from src.core.paper_execution import AssetClass

RISK_SCOPES = ("etf_cycle", "stock_cycle", "cb", "benchmark", "cash_mgmt", "event")


@dataclass(frozen=True)
class SlotSpec:
    slot: str               # 台账 strategies 键名
    kind: str               # 展示/分组用
    asset_class: AssetClass # 槽内默认资产类别（订单可显式覆盖）
    risk_scope: str
    rebalance: str          # 声明节奏（实际调度由 runner 决定）
    note: str = ""


SLOTS: tuple[SlotSpec, ...] = (
    SlotSpec("A_动量", "etf_momentum", AssetClass.ETF, "etf_cycle", "W", "17池60日动量+MA250 top3"),
    SlotSpec("B_轮动", "rotation", AssetClass.ETF, "etf_cycle", "W", "3资产25日动量+绝对过滤"),
    SlotSpec("C_沪深300持有", "benchmark", AssetClass.ETF, "benchmark", "-", "510300 买入持有"),
    SlotSpec("D_红利低波持有", "benchmark", AssetClass.ETF, "benchmark", "-", "512890 买入持有"),
    SlotSpec("E_可转债双低", "cb_double_low", AssetClass.CB, "cb", "M窗(季频选债)", "quant 引擎导出目标"),
    SlotSpec("F_小市值20", "smallcap", AssetClass.STOCK, "stock_cycle", "M", "流通市值升序,单票≤¥2500"),
    SlotSpec("F2_小市值5只神帖对照", "smallcap", AssetClass.STOCK, "stock_cycle", "M", "F 对照变体"),
    SlotSpec("G_MWU融合", "mwu", AssetClass.ETF, "etf_cycle", "W", "三专家 MWU 探针目标"),
    SlotSpec("H_MWU_CB融合", "mwu_cb", AssetClass.CB, "cb", "M", "四专家含转债 PIT（CB feed 修复前如实为三专家）"),
    SlotSpec("I_PEAD业绩预告", "pead", AssetClass.STOCK, "stock_cycle", "M", "预增动量（非 SUE）"),
    SlotSpec("J_短期反转", "reversal", AssetClass.STOCK, "stock_cycle", "M", "21日超跌+流动性过滤"),
    SlotSpec("K_项目选股", "buy_watchlist", AssetClass.STOCK, "stock_cycle", "M", "项目观察池前向测试（观点非信号）"),
    SlotSpec("L_北向择时", "north_timing", AssetClass.ETF, "etf_cycle", "D", "⚠ 观察态：北向披露语义未证实前不产生新交易"),
    SlotSpec("M_筹码集中度", "holder_number", AssetClass.STOCK, "stock_cycle", "M", "股东户数降幅筛选"),
    SlotSpec("N_技术形态对照", "sequoia", AssetClass.STOCK, "stock_cycle", "M", "Sequoia-X 七形态汇总"),
    SlotSpec("O_行业轮动", "industry_momentum", AssetClass.STOCK, "stock_cycle", "M", "申万L1 20日动量+龙头"),
    SlotSpec("X_事件套利", "event_arbitrage", AssetClass.STOCK, "event", "事件驱动", "要约/现金选择权, 单笔≤¥1241"),
    SlotSpec("Y_确定性收益", "cash_mgmt", AssetClass.REPO, "cash_mgmt", "节前/月末", "国债逆回购 GC001"),
)

SLOT_BY_NAME: Mapping[str, SlotSpec] = {s.slot: s for s in SLOTS}


def get_slot(slot_name: str) -> Optional[SlotSpec]:
    return SLOT_BY_NAME.get(slot_name)


def classify_symbol(symbol: str) -> AssetClass:
    """symbol 前缀 → 资产类别（注册表缺省之外的兜底，显式声明优先）。

    覆盖本研究涉及的全部品种形态；未知前缀返回 STOCK 之前必须先加测试，
    禁止把未知品种静默当股票交易——这里返回值供适配器兜底，因此
    对无法识别的形态抛错而非猜测。"""
    s = str(symbol or "").strip().upper()
    code = s.split(".")[0]
    body = code[-6:] if len(code) >= 6 else code
    if len(body) == 6 and body[:3] in ("110", "111", "113", "118", "123", "127", "128"):
        return AssetClass.CB                          # 沪 110/111/113/118 / 深 123/127/128 转债
    if body.startswith(("204", "1318", "1319")):
        return AssetClass.REPO                        # 沪 204xxx / 深 1318xx-1319xx 回购
    if body.startswith(("51", "56", "58", "588")):    # 沪 ETF
        return AssetClass.ETF
    if body.startswith("15") and len(body) == 6:      # 深 ETF/LOF
        return AssetClass.ETF
    if len(body) == 6 and body[:1] in ("6", "0", "3"):
        return AssetClass.STOCK                       # 沪 6 / 深 0、3
    raise ValueError(f"无法识别的证券代码形态: {symbol!r}——先在 deck_registry 登记规则")
