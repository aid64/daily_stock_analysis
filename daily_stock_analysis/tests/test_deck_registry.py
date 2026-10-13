# -*- coding: utf-8 -*-
"""槽位注册表测试：18 槽全覆盖、风险作用域合法、资产类别前缀分类。"""

import pytest

from src.core.paper_execution import AssetClass
from src.services.deck_registry import RISK_SCOPES, SLOTS, classify_symbol, get_slot


def test_registry_covers_18_slots_with_unique_names():
    assert len(SLOTS) == 18
    names = [s.slot for s in SLOTS]
    assert len(set(names)) == 18
    for key in ("A_动量", "B_轮动", "C_沪深300持有", "D_红利低波持有", "E_可转债双低",
                "F_小市值20", "F2_小市值5只神帖对照", "G_MWU融合", "H_MWU_CB融合",
                "I_PEAD业绩预告", "J_短期反转", "K_项目选股", "L_北向择时",
                "M_筹码集中度", "N_技术形态对照", "O_行业轮动", "X_事件套利", "Y_确定性收益"):
        assert get_slot(key) is not None, f"槽位 {key} 未登记"


def test_risk_scopes_valid():
    for s in SLOTS:
        assert s.risk_scope in RISK_SCOPES, f"{s.slot} 风险作用域非法: {s.risk_scope}"


def test_slot_asset_classes():
    assert get_slot("E_可转债双低").asset_class is AssetClass.CB
    assert get_slot("A_动量").asset_class is AssetClass.ETF
    assert get_slot("F_小市值20").asset_class is AssetClass.STOCK
    assert get_slot("Y_确定性收益").asset_class is AssetClass.REPO
    # E2 转债择时不得再传染 ETF/股票槽（spec Phase 3）
    assert get_slot("A_动量").risk_scope != "cb"
    assert get_slot("E_可转债双低").risk_scope == "cb"


def test_classify_symbol_prefixes():
    for sym in ("510300.SH", "512890.SH", "511010.SH", "511880.SH", "159915.SZ"):
        assert classify_symbol(sym) is AssetClass.ETF, sym
    for sym in ("600000.SH", "000001.SZ", "300750.SZ", "600010"):
        assert classify_symbol(sym) is AssetClass.STOCK, sym
    for sym in ("113062.SH", "123089.SZ", "127012.SZ", "118031.SH"):
        assert classify_symbol(sym) is AssetClass.CB, sym
    for sym in ("204001.SH", "131810.SZ"):
        assert classify_symbol(sym) is AssetClass.REPO, sym


def test_classify_symbol_unknown_raises():
    with pytest.raises(ValueError):
        classify_symbol("MYSTERY")
    with pytest.raises(ValueError):
        classify_symbol("")


def test_erp_scale_slots_matches_registry():
    """runner 的 ERP 主动槽必须与注册表 risk_scope 语义一致（单一真源, 防三套槽位集合漂移）。"""
    import importlib.util
    from pathlib import Path

    runner = Path(__file__).resolve().parents[1] / "scripts" / "compare_deck_runner.py"
    spec = importlib.util.spec_from_file_location("compare_deck_runner_erp_scope", runner)
    runner_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner_mod)
    expected = tuple(s.slot for s in SLOTS
                     if s.risk_scope in ("etf_cycle", "stock_cycle") and s.slot != "L_北向择时")
    got = runner_mod._erp_scale_slots()
    assert got == expected
    assert len(got) == 11
    # 关键反例: E/H(cb) 与观察态 L 不受 PE 分位总闸; B(etf_cycle) 在
    assert "E_可转债双低" not in got and "H_MWU_CB融合" not in got
    assert "L_北向择时" not in got
    assert "B_轮动" in got
