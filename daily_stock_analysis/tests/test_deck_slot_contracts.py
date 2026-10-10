# -*- coding: utf-8 -*-
"""任务05 分槽数据契约回归（I/J/M/O/E/L + K 注入 + H unpriced 审计）。

每槽一条主断言，直接对应审查反例（.scratch/dsa-deployment-review-20261007/REVIEW.md §2）：
- I：官方字段为 p_change_min/max(%)；缺字段不得放行；同股取最新 ann_date。
- J：回看窗口按交易日（trade_cal），daily.amount 是千元；moneyflow 走统一客户端。
- M：同报告期多版本按 ann_date 去重；cyq 异常显式跳过不静默放行。
- O：sw_daily 先升序；窗口 20 交易日；index_classify 钉 SW2021；全量成员后取龙头。
- E：子进程非零退出显式失败；目标资金按当前 equity。
- L：观察态持仓冻结（上证发〔2024〕106号后字段语义未证）。
- K：build_buy_watchlist 支持快照参数注入（不再 monkeypatch 服务模块）。
"""
import importlib.util
import sys
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

RUNNER = __import__("pathlib").Path(__file__).resolve().parents[1] / "scripts" / "compare_deck_runner.py"
spec = importlib.util.spec_from_file_location("compare_deck_contract_runner", RUNNER)
mod = importlib.util.module_from_spec(spec)
sys.modules.setdefault("compare_deck_contract_runner", mod)
spec.loader.exec_module(mod)


@pytest.fixture(autouse=True)
def _fixed_day(monkeypatch):
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 10, 8))


# ---------------------------------------------------------------- I: forecast 字段契约
def test_i_forecast_uses_min_max_fields_and_rejects_missing(monkeypatch):
    """缺 p_change_min 的记录一律拒绝（旧实现把缺字段当放行）。"""
    def call(api, **kw):
        if api == "forecast":
            return pd.DataFrame({
                "ts_code": ["600010.SH", "600011.SH"],
                "ann_date": [kw["ann_date"]] * 2,
                "type": ["预增", "预增"],
                "p_change_min": [None, 60.0],       # 第一条缺下限 → 拒绝
                "p_change_max": [None, 90.0],
                "net_profit_min": [100.0, 500.0],
            })
        return None
    monkeypatch.setattr(mod, "ts_client", lambda: SimpleNamespace(call=call))
    out = mod.pead_universe(days_back=1)
    assert [r["ts_code"] for r in out] == ["600011.SH"]
    assert out[0]["p_change_min"] == 60.0


def test_i_forecast_rejects_true_growth_below_50(monkeypatch):
    """真实增长仅 10–20% 不得被判为大增（审查 fixture 反例）。"""
    def call(api, **kw):
        if api == "forecast":
            return pd.DataFrame({"ts_code": ["600010.SH"], "ann_date": [kw["ann_date"]],
                                 "type": ["预增"], "p_change_min": [10.0],
                                 "p_change_max": [20.0], "net_profit_min": [100.0]})
        return None
    monkeypatch.setattr(mod, "ts_client", lambda: SimpleNamespace(call=call))
    assert mod.pead_universe(days_back=1) == []


def test_i_forecast_same_stock_keeps_latest_disclosure(monkeypatch):
    """同股多公告: 新→旧遍历, 首见即最新, 不重复入候选。"""
    def call(api, **kw):
        if api == "forecast":
            day = kw["ann_date"]
            return pd.DataFrame({"ts_code": ["600010.SH"], "ann_date": [day],
                                 "type": ["预增"], "p_change_min": [55.0],
                                 "p_change_max": [80.0], "net_profit_min": [100.0]})
        return None
    monkeypatch.setattr(mod, "ts_client", lambda: SimpleNamespace(call=call))
    out = mod.pead_universe(days_back=5)
    assert len(out) == 1
    assert out[0]["ann_date"] == "20261007"  # 最近一天(EX_DATE 前一天)


# ---------------------------------------------------------------- J: 交易日窗口 + 单位
def test_j_window_comes_from_trade_cal(monkeypatch):
    """回看窗口必须按交易日请求, 不能拿自然日充当交易日。"""
    queried_days = []
    cal_calls = []

    def call(api, **kw):
        if api == "trade_cal":
            cal_calls.append(kw)
            opens = pd.bdate_range(end="2026-10-08", periods=40).strftime("%Y%m%d")
            return pd.DataFrame({"cal_date": opens, "is_open": [1] * len(opens)})
        if api == "daily":
            queried_days.append(kw["trade_date"])
            return pd.DataFrame({"ts_code": ["600010.SH"], "close": [10.0], "amount": [3e4]})
        if api == "moneyflow":
            return pd.DataFrame({"net_mf_amount": [0.0]})
        if api == "stock_basic":
            return pd.DataFrame({"ts_code": ["600010.SH"], "name": ["fixture"]})
        return None

    monkeypatch.setattr(mod, "ts_client", lambda: SimpleNamespace(call=call))
    out = mod.reversal_universe(days=21)
    assert cal_calls, "必须经 trade_cal 取交易日"
    assert len(queried_days) == 22           # 21 期收益 = 22 个收盘
    assert queried_days == sorted(queried_days)
    # amount=3e4 千元 = 3000 万元 → 恰好达到阈值(旧代码按元比较会误杀)
    assert out and out[0]["ts_code"] == "600010.SH"


def test_j_amount_unit_is_thousand_yuan(monkeypatch):
    """amount 千元换算: 2.9e4 千元 = 2900 万 < 3000 万 → 剔除。"""
    def call(api, **kw):
        if api == "trade_cal":
            opens = pd.bdate_range(end="2026-10-08", periods=40).strftime("%Y%m%d")
            return pd.DataFrame({"cal_date": opens, "is_open": [1] * len(opens)})
        if api == "daily":
            return pd.DataFrame({"ts_code": ["600010.SH"], "close": [10.0], "amount": [2.9e4]})
        if api == "stock_basic":
            return pd.DataFrame({"ts_code": ["600010.SH"], "name": ["fixture"]})
        return None

    monkeypatch.setattr(mod, "ts_client", lambda: SimpleNamespace(call=call))
    assert mod.reversal_universe(days=21) == []


def test_j_moneyflow_goes_through_client(monkeypatch):
    """moneyflow 不再直调不存在的 pro.moneyflow(旧实现异常被吞)。"""
    mf_calls = []

    def call(api, **kw):
        if api == "trade_cal":
            opens = pd.bdate_range(end="2026-10-08", periods=40).strftime("%Y%m%d")
            return pd.DataFrame({"cal_date": opens, "is_open": [1] * len(opens)})
        if api == "daily":
            return pd.DataFrame({"ts_code": ["600010.SH"], "close": [10.0], "amount": [3e4]})
        if api == "moneyflow":
            mf_calls.append(kw)
            return pd.DataFrame({"net_mf_amount": [-5000.0]})   # 主力净流出 5000 万 → 剔除
        if api == "stock_basic":
            return pd.DataFrame({"ts_code": ["600010.SH"], "name": ["fixture"]})
        return None

    monkeypatch.setattr(mod, "ts_client", lambda: SimpleNamespace(call=call))
    out = mod.reversal_universe(days=21)
    assert mf_calls, "moneyflow 必须经 TushareClient.call"
    assert out == []


# ---------------------------------------------------------------- M: 报告期版本去重
def test_m_same_period_versions_collapse(monkeypatch):
    """3×3 重复披露 → 1 个候选(旧实现 8 个候选仅 1 只证券)。"""
    def call(api, **kw):
        if api == "stk_holdernumber":
            end = kw["enddate"]
            num = 100 if end.endswith("0630") else 200
            return pd.DataFrame({"ts_code": ["600010.SH"] * 3,
                                 "ann_date": ["20260830", "20260815", "20260820"],
                                 "end_date": [end] * 3,
                                 "holder_num": [num] * 3})
        if api == "cyq_perf":
            return pd.DataFrame({"trade_date": ["20260930"], "winner_rate": [10.0]})
        if api == "stock_basic":
            return pd.DataFrame({"ts_code": ["600010.SH"], "name": ["fixture"]})
        return None

    monkeypatch.setattr(mod, "ts_client", lambda: SimpleNamespace(call=call))
    out = mod.holder_concentration_universe(top=8)
    codes = [r["ts_code"] for r in out]
    assert codes == ["600010.SH"], f"重复披露产生重复候选: {codes}"


def test_m_cyq_failure_skips_candidate_explicitly(monkeypatch):
    """cyq_perf 不可用(None) → 显式跳过, 不静默放行未校验候选。"""
    def call(api, **kw):
        if api == "stk_holdernumber":
            end = kw["enddate"]
            return pd.DataFrame({"ts_code": ["600010.SH"], "ann_date": ["20260830"],
                                 "end_date": [end],
                                 "holder_num": [100 if end.endswith("0630") else 200]})
        if api == "cyq_perf":
            return None
        if api == "stock_basic":
            return pd.DataFrame({"ts_code": ["600010.SH"], "name": ["fixture"]})
        return None

    monkeypatch.setattr(mod, "ts_client", lambda: SimpleNamespace(call=call))
    assert mod.holder_concentration_universe(top=8) == []


def test_m_missing_stock_basic_fails_closed(monkeypatch):
    """stock_basic 不可用 → 候选置空(不降级 ST/板块过滤)。"""
    def call(api, **kw):
        if api == "stk_holdernumber":
            end = kw["enddate"]
            return pd.DataFrame({"ts_code": ["600010.SH"], "ann_date": ["20260830"],
                                 "end_date": [end],
                                 "holder_num": [100 if end.endswith("0630") else 200]})
        return None

    monkeypatch.setattr(mod, "ts_client", lambda: SimpleNamespace(call=call))
    assert mod.holder_concentration_universe(top=8) == []


# ---------------------------------------------------------------- O: 排序/窗口/版本/成员
def test_o_ascending_sort_picks_real_up_industry(monkeypatch):
    """官方降序返回时不得反算动量方向(审查反例: 实际上涨行业被反选)。"""
    def call(api, **kw):
        if api == "trade_cal":
            opens = pd.bdate_range(end="2026-10-08", periods=30).strftime("%Y%m%d")
            return pd.DataFrame({"cal_date": opens, "is_open": [1] * len(opens)})
        if api == "index_classify":
            assert kw.get("src") == "SW2021", "分类版本必须钉 SW2021"
            return pd.DataFrame({"index_code": ["UP", "DOWN"]})
        if api == "sw_daily":
            series = list(range(100, 121)) if kw["ts_code"] == "UP" else list(range(120, 99, -1))
            return pd.DataFrame({"trade_date": pd.bdate_range(end="2026-10-08", periods=21).strftime("%Y%m%d"),
                                 "close": series}).iloc[::-1].reset_index(drop=True)  # 降序返回
        if api == "index_member_all":
            return pd.DataFrame({"ts_code": ["600010.SH" if kw["l1_code"] == "UP" else "600011.SH"]})
        if api == "daily_basic":
            return pd.DataFrame({"ts_code": ["600010.SH", "600011.SH"], "circ_mv": [1000.0, 1000.0]})
        return None

    monkeypatch.setattr(mod, "ts_client", lambda: SimpleNamespace(call=call))
    out = mod.industry_momentum_universe(top_industries=1)
    assert [r["industry"] for r in out] == ["UP"]


def test_o_uses_all_members_before_largest(monkeypatch):
    """成分股 200 条截断修复: 大市值龙头在第 200 条之后也必须可见。"""
    big_code = "600999.SH"
    members = [f"60{i:04d}.SH" for i in range(300)] + [big_code]
    # 让 big_code 不在前 200 条
    assert big_code not in members[:200]

    def call(api, **kw):
        if api == "trade_cal":
            opens = pd.bdate_range(end="2026-10-08", periods=30).strftime("%Y%m%d")
            return pd.DataFrame({"cal_date": opens, "is_open": [1] * len(opens)})
        if api == "index_classify":
            return pd.DataFrame({"index_code": ["UP"]})
        if api == "sw_daily":
            return pd.DataFrame({"trade_date": pd.bdate_range(end="2026-10-08", periods=21).strftime("%Y%m%d"),
                                 "close": list(range(100, 121))})
        if api == "index_member_all":
            return pd.DataFrame({"ts_code": members})
        if api == "daily_basic":
            rows = {"ts_code": members, "circ_mv": [100.0] * len(members)}
            rows["circ_mv"][-1] = 99999.0  # 大市值压轴
            return pd.DataFrame(rows)
        return None

    monkeypatch.setattr(mod, "ts_client", lambda: SimpleNamespace(call=call))
    out = mod.industry_momentum_universe(top_industries=1, per_industry=2, top=8)
    assert big_code in {r["ts_code"] for r in out}


# ---------------------------------------------------------------- E: 退出码 + equity 目标
def test_e_nonzero_export_exit_is_explicit_failure(monkeypatch, tmp_path):
    """quant 导出 exit≠0 → 返回异常消息, 不得继续读旧 plan 执行过期目标。"""
    monkeypatch.setattr(mod, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(mod, "subprocess", SimpleNamespace(
        run=lambda *a, **k: SimpleNamespace(stdout="", stderr="export failed", returncode=1)))
    led = {"strategies": {"E_可转债双低": {"cash": 12411.0, "holds": []}}, "_control": {}}
    msgs = mod.cb_rebalance(led, {}, False)
    assert msgs and "导出失败" in msgs[0]
    assert led["strategies"]["E_可转债双低"]["holds"] == []


def test_cb_target_lots_scales_with_equity_not_constant():
    """目标手数按传入资金缩放: 亏损后目标必须随之缩小。"""
    targets = {"113062.SH": {"weight": 0.5, "t1_close": 100.0}}
    px = {"113062.SH": {"price": 100.0}}
    full = mod.cb_target_lots(targets, px, e2_defensive=False, capital=12411.0)
    half = mod.cb_target_lots(targets, px, e2_defensive=False, capital=6205.0)
    assert full["113062.SH"] == 60      # 0.5*12411 // 1000 * 10 = 60 张
    assert half["113062.SH"] == 30
    assert half["113062.SH"] < full["113062.SH"]


# ---------------------------------------------------------------- L: 观察态
def test_l_observation_freeze_never_trades(monkeypatch):
    """低分位(清仓信号)也只能记录: 持仓冻结、现金不变、signal 标记 observation。"""
    dates = pd.bdate_range(end="2026-10-08", periods=101).strftime("%Y%m%d")
    frame = pd.DataFrame({"trade_date": dates, "north_money": list(range(100)) + [0.0]})
    monkeypatch.setattr(mod, "ts_client", lambda: SimpleNamespace(
        call=lambda *a, **k: frame))
    led = {"strategies": {"L_北向择时": {
        "cash": 10000.0, "equity": 10400.0,
        "holds": [{"symbol": "510300.SH", "name": "沪深300ETF", "qty": 100,
                   "price": 4.0, "notional": 400.0, "held_since": "2026-10-07"}],
    }}}
    msgs = mod.north_rebalance(led, {"510300.SH": {"price": 4.0}}, False)
    slot = led["strategies"]["L_北向择时"]
    assert slot["cash"] == 10000.0
    assert sum(h["qty"] for h in slot["holds"]) == 100
    assert slot["signal"]["mode"] == "observation"
    assert "不交易" in msgs[0]


def test_l_dry_run_mentions_observation(monkeypatch):
    msgs = mod.north_rebalance({"strategies": {"L_北向择时": {"cash": 0.0, "holds": []}}},
                               {}, True)
    assert msgs and "观察态" in msgs[0]


# ---------------------------------------------------------------- K: 参数注入
def test_buy_watchlist_accepts_injected_snapshot(monkeypatch):
    """build_buy_watchlist(snapshot=...) 注入生效, 且不触碰模块级 monkeypatch。"""
    from src.services import buy_watchlist_service as svc
    snapshot = pd.DataFrame({
        "code": ["sh600000"], "name": ["浦发银行"], "close": [10.0],
        "prev_close": [9.8], "change_pct": [2.0], "amount": [5e8], "market_cap": [5e11],
        "volume_ratio": [1.2], "turnover_rate": [2.0], "pe": [5.0],
    })
    out = svc.build_buy_watchlist(snapshot=snapshot, top_n=1)
    assert out["candidates"], f"注入快照应产出候选: {out}"
    assert out["candidates"][0]["symbol"] == "600000"


def test_buy_watchlist_empty_injected_snapshot_is_explicit():
    from src.services import buy_watchlist_service as svc
    out = svc.build_buy_watchlist(snapshot=pd.DataFrame())
    assert out["candidates"] == []
    assert "为空" in out["source_note"]

