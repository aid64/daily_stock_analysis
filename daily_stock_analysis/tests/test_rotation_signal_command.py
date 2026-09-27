# -*- coding: utf-8 -*-
"""/轮动信号 影子跟踪命令单测（跨项目文件契约：只读 signals.jsonl）"""

import json

import pytest

from bot.commands.rotation_signal import RotationSignalCommand, _latest_signal


@pytest.fixture()
def isolated_log(tmp_path, monkeypatch):
    log = tmp_path / "signals.jsonl"
    monkeypatch.setattr(
        "bot.commands.rotation_signal.SIGNAL_LOG", log)
    return log


_SIG = {
    "at": "2026-09-27T22:00:00", "as_of": "2026-09-24",
    "strategy": "etf_momentum",
    "params": {"windows": [60], "ma_filter": 250, "top_k": 3,
               "rebalance_freq": "W", "defensive": "511010.SH",
               "universe_size": 17},
    "scores": [{"symbol": "512890.SH", "score": 0.0902},
               {"symbol": "511880.SH", "score": 0.0024}],
    "selected": ["512890.SH", "511880.SH"],
    "defensive_mode": False,
    "next_rebalance_day": "2026-09-28",
    "caveats": ["影子跟踪", "口径备注测试", "近似"],
}


def _write(log, records):
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8")


def test_renders_latest_signal(isolated_log):
    _write(isolated_log, [_SIG])
    text = RotationSignalCommand._render()
    assert "影子跟踪" in text and "不落单" in text
    assert "2026-09-24" in text and "2026-09-28" in text
    assert "512890.SH 红利低波ETF（+0.0902）" in text
    assert "过闸候选 2 只" in out_check(text)
    assert "14:50" in text  # 手动跟单参考


def out_check(text):
    return text


def test_defensive_mode_rendered(isolated_log):
    sig = dict(_SIG)
    sig["selected"] = []
    sig["defensive_mode"] = True
    _write(isolated_log, [sig])
    text = RotationSignalCommand._render()
    assert "防御态" in text and "国债ETF" in text


def test_missing_log_friendly(isolated_log):
    text = RotationSignalCommand._render()
    assert "还没有影子信号记录" in text


def test_malformed_lines_skipped(isolated_log):
    isolated_log.parent.mkdir(parents=True, exist_ok=True)
    isolated_log.write_text(
        "{bad json}\n" + json.dumps(_SIG, ensure_ascii=False) + "\n",
        encoding="utf-8")
    sig = _latest_signal(isolated_log)
    assert sig is not None and sig["as_of"] == "2026-09-24"
