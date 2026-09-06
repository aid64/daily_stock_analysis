# -*- coding: utf-8 -*-
"""D0：tushare 转债条款数据源落地（蓝图 CB-RESEARCH-PLAN 诊断线 D0）

拉取并落盘（data/pit/cb_terms.parquet + json 附表）：
- cb_basic 全量：ts_code/list_date/maturity_date/issue_size/coupon_rate/maturity_call_price
- cb_rate 逐券：逐年票息区间表（rate_start_date/rate_end_date/coupon_rate）
- cb_call：强赎/到赎事件（ann_date/is_call/call_price_tax）
- cb_share：转股价历史（publish_date/convert_price）——E3 下修事件源

频率纪律：15000 积分档约 500 次/分钟，节流 200 次/分钟留余量；
失败重试 3 次后进豁免清单走东财 fallback（D1 内处理）。
幂等：产物存在且 --force 未指定时跳过。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from datetime import date, datetime
from typing import Any, Dict, Optional, Sequence

import pandas as pd

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from quant_research.data.clients.tushare_client import TushareClient

logger = logging.getLogger("fetch_cb_terms")

OUT_DIR = _PROJECT_ROOT / "data" / "pit"
OUT_PARQUET = OUT_DIR / "cb_terms.parquet"
OUT_META = OUT_DIR / "cb_terms_meta.json"
OUT_CALLS = OUT_DIR / "cb_calls.parquet"
OUT_SHARE = OUT_DIR / "cb_share_history.parquet"
RATE_LIMIT_PER_MIN = 200
RETRY = 3


def _throttle(state: Dict[str, float]) -> None:
    """简单令牌桶：RATE_LIMIT_PER_MIN 次/分钟。"""
    import time as _time

    now = _time.time()
    state.setdefault("window_start", now)
    state.setdefault("count", 0)
    elapsed = now - state["window_start"]
    if elapsed < 60 and state["count"] >= RATE_LIMIT_PER_MIN:
        wait = 60 - elapsed
        logger.info("频率节流：等待 %.1fs", wait)
        _time.sleep(wait)
        state["window_start"] = _time.time()
        state["count"] = 0
    elif elapsed >= 60:
        state["window_start"] = now
        state["count"] = 0
    state["count"] += 1


def _with_retry(fn, what: str, throttle_state: Dict[str, float]):
    for attempt in range(1, RETRY + 1):
        _throttle(throttle_state)
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s 失败 (attempt %d/%d): %r",
                           what, attempt, RETRY, exc)
            time.sleep(min(2 ** attempt, 8))
    return None


def _load_token() -> str:
    # .env 在 quant_research/ 下；stdin 环境里 load_dotenv 的 find_dotenv 会断言失败
    env = _PROJECT_ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("TUSHARE_TOKEN="):
                os.environ["TUSHARE_TOKEN"] = (
                    line.split("=", 1)[1].strip().strip('"').strip("'"))
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN 未配置（quant_research/.env）")
    return token


def fetch_all(force: bool = False) -> Dict[str, Any]:
    """幂等语义：各产物独立判断——basic/calls/share 任一缺失都重拉该组。"""
    basic_exists = OUT_PARQUET.exists()
    calls_exist = OUT_CALLS.exists()
    share_exist = OUT_SHARE.exists()
    if basic_exists and calls_exist and share_exist and not force:
        logger.info("全部产物已存在，跳过（--force 重拉）")
        return json.loads(OUT_META.read_text(encoding="utf-8")) if OUT_META.exists() else {}

    client = TushareClient(token=_load_token())._pro
    throttle: Dict[str, float] = {}

    if basic_exists:
        basic = pd.read_parquet(OUT_PARQUET)
    else:
        basic = _with_retry(
            lambda: client.cb_basic(fields=(
                "ts_code,bond_full_name,list_date,maturity_date,issue_size,"
                "coupon_rate,maturity_call_price")),
            "cb_basic", throttle)
        if basic is None or basic.empty:
            raise RuntimeError("cb_basic 拉取失败")
    logger.info("cb_basic: %d 只", len(basic))
    symbols = basic["ts_code"].tolist()

    # 2) cb_rate 逐券
    rate_rows = []
    rate_ok, rate_exempt = 0, []
    for i, ts_code in enumerate(symbols, 1):
        df = _with_retry(
            (lambda c=ts_code: client.cb_rate(ts_code=c,
             fields="ts_code,rate_freq,rate_start_date,rate_end_date,coupon_rate")),
            f"cb_rate {ts_code}", throttle)
        if df is None or df.empty or "coupon_rate" not in df.columns:
            rate_exempt.append(ts_code)
            continue
        df["ts_code"] = ts_code
        rate_rows.append(df)
        rate_ok += 1
        if i % 100 == 0:
            logger.info("cb_rate 进度 %d/%d（豁免 %d）", i, len(symbols), len(rate_exempt))
    rate_df = pd.concat(rate_rows, ignore_index=True) if rate_rows else pd.DataFrame()
    logger.info("cb_rate: %d/%d 只成功，豁免 %d",
                rate_ok, len(symbols), len(rate_exempt))

    # 3) cb_call 强赎/到赎事件（按公告日循环拉全量）
    call_frames = []
    for year in (range(2018, date.today().year + 1) if not calls_exist else []):
        for half in (("0101", "0630"), ("0701", "1231")):
            df = _with_retry(
                (lambda a=f"{year}{half[0]}", b=f"{year}{half[1]}":
                 client.cb_call(start_date=a, end_date=b)),
                f"cb_call {year}H{half[0][:2]}", throttle)
            if df is not None and len(df):
                call_frames.append(df)
    call_df = pd.concat(call_frames, ignore_index=True) if call_frames else pd.DataFrame()
    logger.info("cb_call: %d 条赎回/强赎事件", len(call_df))

    # 4) cb_share 转股价历史（逐券；E3 事件源）
    share_rows = []
    if share_exist:
        share_df = pd.read_parquet(OUT_SHARE)
        share_ok, share_exempt = -1, []
        logger.info("cb_share: 已存在（%d 行），跳过逐券循环", len(share_df))
        share_rows = None
    share_ok, share_exempt = 0, []
    for i, ts_code in enumerate(symbols, 1):
        df = _with_retry(
            (lambda c=ts_code: client.cb_share(ts_code=c,
             fields="ts_code,publish_date,end_date,convert_price_initial,convert_price")),
            f"cb_share {ts_code}", throttle)
        if df is None or df.empty:
            share_exempt.append(ts_code)
            continue
        share_rows.append(df)
        share_ok += 1
        if i % 100 == 0:
            logger.info("cb_share 进度 %d/%d", i, len(symbols))
    if share_rows is not None:
        share_df = pd.concat(share_rows, ignore_index=True) if share_rows else pd.DataFrame()
        logger.info("cb_share: %d/%d 只成功", share_ok, len(symbols))

    # 5) 落盘
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    basic.to_parquet(OUT_PARQUET, index=False)
    rate_df.to_parquet(OUT_DIR / "cb_rates.parquet", index=False)
    if not calls_exist and len(call_df):
        call_df.to_parquet(OUT_CALLS, index=False)
    elif calls_exist and not len(call_frames):
        call_df = pd.read_parquet(OUT_CALLS)
    if len(share_df):
        share_df.to_parquet(OUT_SHARE, index=False)
    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_basic": int(len(basic)),
        "n_rate_ok": rate_ok,
        "n_rate_exempt": len(rate_exempt),
        "rate_exempt": rate_exempt[:50],
        "n_share_ok": share_ok,
        "n_call_events": int(len(call_df)),
        "n_share_rows": int(len(share_df)),
    }
    OUT_META.write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    logger.info("落盘完成: %s", OUT_PARQUET)
    return meta


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="D0：tushare 转债条款拉取")
    parser.add_argument("--force", action="store_true", help="强制重拉")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                        force=True)
    meta = fetch_all(force=args.force)
    print(json.dumps(meta, ensure_ascii=False, indent=1))
