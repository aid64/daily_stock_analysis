# -*- coding: utf-8 -*-
"""买入观察池测试：打分规则 / 北交所排除 / 已持仓排除 / 缺列容错 / 快照回溯"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.services import buy_watchlist_service as bws
from src.services.buy_watchlist_service import (
    build_buy_watchlist,
    score_candidates,
)


def _row(code, name="样本股", close=10.0, prev_close=9.8,
         amount=8e7, volume_ratio=1.2, turnover_rate=2.0, pe=15.0,
         market_cap=6e9):
    return {"code": code, "name": name, "close": close, "prev_close": prev_close,
            "volume": 1e6, "amount": amount, "turnover_rate": turnover_rate,
            "volume_ratio": volume_ratio, "pe": pe, "pb": 1.5,
            "market_cap": market_cap}


def _candidates(*rows):
    return pd.DataFrame([r for r in rows])


class TestScoreCandidates:
    def test_ideal_candidate_scores_seven(self):
        # 温和上行 + 量比 + PE + 换手 全中 → 2+2+2+1 = 7
        df = _candidates(_row("sh600001"))
        picks = score_candidates(df)
        assert len(picks) == 1
        assert picks[0].score == 7
        assert picks[0].symbol == "600001"
        assert any("温和上行" in r for r in picks[0].reasons)

    def test_limit_up_excluded(self):
        # 涨 9%（>7 上界）出局——不追板
        df = _candidates(_row("sh600002", close=10.9, prev_close=10.0))
        assert score_candidates(df) == []

    def test_down_day_excluded(self):
        df = _candidates(_row("sh600003", close=9.5, prev_close=10.0))
        assert score_candidates(df) == []

    def test_extreme_volume_ratio_excluded(self):
        df = _candidates(_row("sh600004", volume_ratio=5.0))
        assert score_candidates(df) == []

    def test_bj_prefix_excluded(self):
        df = _candidates(_row("bj920005"), _row("sh600006"))
        picks = score_candidates(df)
        assert [p.symbol for p in picks] == ["600006"]

    def test_bj_suffix_excluded(self):
        df = _candidates(_row("920007.BJ"), _row("600008.SH"))
        picks = score_candidates(df)
        assert [p.symbol for p in picks] == ["600008"]

    def test_held_symbols_excluded(self):
        df = _candidates(_row("sh000899"), _row("sh600010"))
        picks = score_candidates(df, exclude_symbols=["000899"])
        assert [p.symbol for p in picks] == ["600010"]

    def test_missing_columns_tolerated(self):
        # 新浪降级快照：量比/换手/PE 全缺 → 0% 涨幅入池，0 分
        row = _row("sh600011", close=10.0, prev_close=10.0,
                   volume_ratio=None, turnover_rate=None, pe=None)
        df = _candidates(row)
        picks = score_candidates(df)
        assert len(picks) == 1
        assert picks[0].score == 0
        assert picks[0].reasons == ["涨幅/成交额达标（其余维度数据缺失）"]

    def test_sort_by_score_then_amount(self):
        # 高分在前；同分按成交额降序
        df = _candidates(
            _row("sh600020", amount=1e8),   # score 7
            _row("sh600021", amount=5e8),   # score 7，成交额更高
            _row("sh600022", pe=50.0),      # score 5
        )
        picks = score_candidates(df)
        assert [p.symbol for p in picks] == ["600021", "600020", "600022"]

    def test_top_n_truncates(self):
        df = _candidates(*[_row(f"sh6000{i}") for i in range(30, 40)])
        picks = score_candidates(df, top_n=5)
        assert len(picks) == 5


class TestBuildBuyWatchlist:
    @staticmethod
    def _freeze_today(monkeypatch, day):
        class _FrozenDate(date):
            @classmethod
            def today(cls):  # noqa: N805 - 固定"今天"
                return day
        monkeypatch.setattr(bws, "date", _FrozenDate)

    def _seed_snapshot(self, tmp_path, monkeypatch, rows, day=None):
        from src.services import market_scan_service as mss
        monkeypatch.setattr(mss, "_PROJECT_SCAN_DIR", tmp_path)
        day = day or date(2026, 9, 15)
        path = tmp_path / f"snapshot_{day.isoformat()}.parquet"
        df = pd.DataFrame(rows)
        df.to_parquet(path, index=False)

    def test_full_flow_with_real_prefix_codes(self, tmp_path, monkeypatch):
        rows = [
            _row("sh600100", name="完美候选", close=10.4, prev_close=10.0),
            _row("sz000101", name="涨停候选", close=11.0, prev_close=10.0),  # +10% 出局
            _row("bj920102", name="北交候选"),                               # 北交出局
        ]
        self._seed_snapshot(tmp_path, monkeypatch, rows)
        out = build_buy_watchlist(exclude_symbols=[])
        assert out["candidates"], out
        assert out["candidates"][0]["symbol"] == "600100"
        assert out["snapshot_date"] == "2026-09-15"
        assert out["stale_days"] >= 0

    def test_stale_snapshot_flags_note(self, tmp_path, monkeypatch):
        # 快照 6 天前 → stale_days>3 → source_note 提示停更
        self._seed_snapshot(tmp_path, monkeypatch,
                            [_row("sh600200")], day=date(2026, 9, 8))
        self._freeze_today(monkeypatch, date(2026, 9, 15))
        out = build_buy_watchlist()
        assert out["candidates"]
        assert "未更新" in out["source_note"]

    def test_missing_snapshot_degrades_to_empty(self, tmp_path, monkeypatch):
        from src.services import market_scan_service as mss
        monkeypatch.setattr(mss, "_PROJECT_SCAN_DIR", tmp_path / "nothing")
        self._freeze_today(monkeypatch, date(2026, 9, 15))
        out = build_buy_watchlist()
        assert out["candidates"] == []
        assert "不可用" in out["source_note"]

    def test_zero_change_candidate_kept_with_zero_score(self):
        # 0% 涨幅在带内 [0,7]：过硬过滤但全不中加分区间 → 0 分入池
        df = _candidates(_row("sh600300", close=10.0, prev_close=10.0,
                              volume_ratio=0.9, turnover_rate=0.7, pe=40.0))
        picks = score_candidates(df)
        assert len(picks) == 1
        assert picks[0].score == 0
