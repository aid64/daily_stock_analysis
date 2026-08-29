# -*- coding: utf-8 -*-
"""IFinDClient 解析逻辑与 PIT 校验测试（离线，无网络依赖）"""
import io

import pandas as pd
import pytest

from quant_research.backtest import validate_pit_snapshot
from quant_research.data.clients.ifind_client import (
    extract_csv_url,
    parse_daily_csv,
    parse_daily_markdown,
    parse_rating_table,
)

SAMPLE_CSV = (
    "证券代码,证券简称,日期,收盘价,转股溢价率\n"
    "110076.SH,华海转债,20221231,110.531,\n"
    "110076.SH,华海转债,20221230,110.531,71.3584\n"
    "110076.SH,华海转债,20221229,111.367,70.6251\n"
).encode("utf-8-sig")

SAMPLE_DAILY_MD = """|证券代码|证券简称|日期|转股溢价率（单位：%）|收盘价（价格类型：市价；单位：元）|
|---|---|---|---|---|
|113605.SH|大参转债|20231229|108.7862|110.354|
|113605.SH|大参转债|20231228|108.1804|109.15|
|113605.SH|大参转债|20231224|\t|107.541|
"""

SAMPLE_RATING_MD = (
    "|证券代码|证券简称|债券历史信用评级|上市日期|\n"
    "|---|---|---|---|\n"
    "|113605.SH|大参转债|"
    "2020-05-22  中证鹏元资信评估股份有限公司  AA\n"
    "2021-06-15  中证鹏元资信评估股份有限公司  AA\n"
    "2022-06-21  中证鹏元资信评估股份有限公司  AA-|20201113|\n"
)


# ── CSV URL ───────────────────────────────────────────────
def test_extract_csv_url():
    url = "http://o.thsi.cn/ifind-python-aime-tools-service.result-cache/q.csv"
    answer = f"部分数据：\n# 截断提示\n下载URL为： {url}"
    assert extract_csv_url(answer) == url


def test_extract_csv_url_none_when_no_truncation():
    assert extract_csv_url("无截断的完整数据") is None


# ── 日线 CSV 解析 ─────────────────────────────────────────
def test_parse_daily_csv_columns_and_values():
    df = parse_daily_csv(SAMPLE_CSV)
    assert list(df.columns) == ["date", "close", "premium_rate"]
    assert len(df) == 3
    # 按日期升序排列
    assert list(df["date"]) == [
        pd.Timestamp("2022-12-29"),
        pd.Timestamp("2022-12-30"),
        pd.Timestamp("2022-12-31"),
    ]
    assert df.iloc[2]["close"] == pytest.approx(110.531)
    # 周末/停牌转股溢价率为空 -> NaN
    assert pd.isna(df.iloc[2]["premium_rate"])
    assert df.iloc[1]["premium_rate"] == pytest.approx(71.3584)


def test_parse_daily_csv_drops_empty_close():
    df = parse_daily_csv(
        "证券代码,证券简称,日期,收盘价,转股溢价率\n"
        "110076.SH,华海转债,20221231,,71.3\n".encode("utf-8-sig")
    )
    assert df.empty


def test_parse_daily_csv_missing_column_returns_empty():
    # 缺必要列（如已退市券返回异结构表）时返回空表，由调用方跳过该标的
    df = parse_daily_csv(
        "证券代码,证券简称,日期,收盘价\n"
        "110076.SH,华海转债,20221231,110.531\n".encode("utf-8-sig")
    )
    assert df.empty


def test_parse_daily_csv_skips_bad_lines():
    # 回归：已退市券返回列宽不一致的 CSV 曾导致 ParserError 整批崩溃
    bad = (
        "证券代码,证券简称,日期,收盘价,转股溢价率\n"
        "110076.SH,华海转债,20221231,110.531,71.0\n"
        "异常行,多余,字段,导致,列宽,不一致,出现\n"
        "110076.SH,华海转债,20221230,110.531,\n"
    ).encode("utf-8-sig")
    df = parse_daily_csv(bad)
    assert len(df) == 2  # 坏行被跳过，不崩溃
    assert df.iloc[0]["date"] == pd.Timestamp("2022-12-30")


# ── 日线 markdown 解析（未截断回退路径）──────────────────
def test_parse_daily_markdown():
    df = parse_daily_markdown(SAMPLE_DAILY_MD)
    assert len(df) == 3
    # 按日期升序：2023-12-24（无溢价率）、2023-12-28、2023-12-29
    assert df.iloc[0]["date"] == pd.Timestamp("2023-12-24")
    assert pd.isna(df.iloc[0]["premium_rate"])
    assert df.iloc[1]["date"] == pd.Timestamp("2023-12-28")
    assert df.iloc[1]["close"] == pytest.approx(109.15)
    assert df.iloc[1]["premium_rate"] == pytest.approx(108.1804)
    assert df.iloc[2]["date"] == pd.Timestamp("2023-12-29")
    assert df.iloc[2]["premium_rate"] == pytest.approx(108.7862)


# ── 评级 + 上市日期解析 ──────────────────────────────────
def test_parse_rating_table():
    list_date, events = parse_rating_table(SAMPLE_RATING_MD, "113605.SH")
    assert list_date == "2020-11-13"
    assert events == [
        ("2020-05-22", "AA"),
        ("2021-06-15", "AA"),
        ("2022-06-21", "AA-"),
    ]


def test_parse_rating_table_literal_backslash_n():
    # 评级单元格内行间为字面 "\\n"（两字符），整行无真实换行
    md = (
        "|证券代码|证券简称|债券历史信用评级|上市日期|\n"
        "|---|---|---|---|\n"
        "|113605.SH|大参转债|"
        "2020-05-22  中证鹏元资信评估股份有限公司  AA\\n"
        "2022-06-21  中证鹏元资信评估股份有限公司  AA-|20201113|\n"
    )
    list_date, events = parse_rating_table(md, "113605.SH")
    assert list_date == "2020-11-13"
    assert ("2020-05-22", "AA") in events
    assert ("2022-06-21", "AA-") in events


def test_parse_rating_table_reversed_columns():
    # 实测列序不固定：此处为 上市日期|评级（与 SAMPLE_RATING_MD 相反）
    md = (
        "|证券代码|证券简称|上市日期|债券历史信用评级|\n"
        "|---|---|---|---|\n"
        "|113605.SH|大参转债|20201113|"
        "2020-05-22  中证鹏元资信评估股份有限公司  AA\n"
        "2022-06-21  中证鹏元资信评估股份有限公司  AA-|\n"
    )
    list_date, events = parse_rating_table(md, "113605.SH")
    assert list_date == "2020-11-13"
    assert events == [
        ("2020-05-22", "AA"),
        ("2022-06-21", "AA-"),
    ]


def test_parse_rating_table_missing_returns_empty():
    list_date, events = parse_rating_table("无评级信息", "110076.SH")
    assert list_date is None
    assert events == []


# ── PIT 校验 ──────────────────────────────────────────────
def _pit_table():
    return pd.DataFrame(
        {
            "as_of": ["2022-06-01", "2022-06-01", "2022-06-02"],
            "symbol": ["110076.SH", "113605.SH", "110076.SH"],
            "price": [110.0, 108.0, 111.0],
            "premium_rate": [71.0, 68.0, 70.0],
            "rating": ["AA", "AA", "AA"],
            "list_date": ["2020-11-25", "2020-11-13", "2020-11-25"],
        }
    )


def test_validate_pit_snapshot_valid():
    report = validate_pit_snapshot(_pit_table(), start="2022-01-01", end="2022-12-31")
    assert report["total_rows"] == 3
    assert report["valid_rows"] == 3
    assert report["distinct_symbols"] == 2
    assert report["distinct_dates"] == 2
    assert report["future_rows"] == 0
    assert report["rating_coverage"] == pytest.approx(1.0)
    assert report["future_listing_rows"] == 0


def test_validate_pit_snapshot_future_leak_raises():
    with pytest.raises(ValueError, match="未来数据泄漏"):
        validate_pit_snapshot(_pit_table(), end="2022-06-01")


def test_validate_pit_snapshot_missing_column_raises():
    bad = _pit_table().drop(columns=["rating"])
    with pytest.raises(ValueError, match="缺少字段"):
        validate_pit_snapshot(bad)


def test_validate_pit_snapshot_empty_raises():
    with pytest.raises(ValueError, match="为空"):
        validate_pit_snapshot(pd.DataFrame())


def test_validate_pit_snapshot_future_listing_counted():
    t = _pit_table()
    t.loc[0, "list_date"] = "2023-01-01"  # 上市日晚于 as_of
    report = validate_pit_snapshot(t, start="2022-01-01", end="2022-12-31")
    assert report["future_listing_rows"] == 1
