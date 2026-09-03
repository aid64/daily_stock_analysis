#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""构建可转债 PIT 历史快照

从 iFinD 拉取逐日收盘价 / 转股溢价率（全量 CSV 下载）与历史评级 / 上市日期，
按"评级生效日期前向映射"组装成严格 PIT 表，输出 parquet + 覆盖率报告。

用法：
    # 默认 universe = data/cache/bars/*/none/*.parquet（现有可转债缓存）
    py scripts/build_pit_snapshot.py --start 2021-01-01 --end 2026-08-08

    # 指定标的
    py scripts/build_pit_snapshot.py --symbols 110076.SH,113605.SH \
        --start 2021-01-01 --end 2026-08-08

    # 指定 universe 文件（每行一个代码，或含 symbol 列的 csv）
    py scripts/build_pit_snapshot.py --universe-file data/universe_cb.txt \
        --start 2021-01-01 --end 2026-08-08

    # 只校验已有快照，不发网络请求
    py scripts/build_pit_snapshot.py --validate-only --pit data/pit/convertible_pit.parquet

输出：
    --out             默认 data/pit/convertible_pit.parquet
    --coverage-out    默认 data/pit/coverage.json
    --raw-dir         默认 data/pit/raw（按 标的/年.csv 缓存，断点续跑）

字段契约：as_of / symbol / price / premium_rate / rating / list_date
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from quant_research.backtest import (
    normalize_cb_symbol,
    validate_pit_snapshot,
)
from quant_research.data.clients.ifind_client import IFinDClient

logger = logging.getLogger("build_pit_snapshot")


def _default_universe() -> list[str]:
    """推导 assemble universe：优先 raw 缓存目录树（PIT 数据真源），
    回退 bars/{exchange}/{adj}/code.parquet 日线缓存。

    2026-09-03 修复：bars 缓存只有 307 只（早期子集），曾使 assemble-only
    静默丢掉 550+ 只历史债券——universe 必须与实际读取数据的 raw 缓存
    一致，否则重建会无声缩水。
    """
    raw_root = _PROJECT_ROOT / "data" / "pit" / "raw"
    if raw_root.exists():
        symbols = sorted(
            normalize_cb_symbol(d.name) for d in raw_root.iterdir()
            if d.is_dir() and (d / "meta.json").exists()
        )
        if symbols:
            return symbols
    cache_root = _PROJECT_ROOT / "data" / "cache" / "bars"
    symbols: list[str] = []
    for p in cache_root.glob("*/none/*.parquet"):
        sym = normalize_cb_symbol(p.stem)
        if sym not in symbols:
            symbols.append(sym)
    symbols.sort()
    return symbols


def _load_universe(args) -> list[str]:
    if args.symbols:
        return [normalize_cb_symbol(s) for s in args.symbols.split(",") if s.strip()]
    if args.universe_file:
        path = Path(args.universe_file)
        if path.suffix.lower() == ".csv":
            df = pd.read_csv(path, dtype=str)
            col = next((c for c in df.columns if "symbol" in c.lower()), df.columns[0])
            syms = [normalize_cb_symbol(s) for s in df[col].dropna().tolist()]
        else:
            syms = [
                normalize_cb_symbol(s.strip())
                for s in path.read_text(encoding="utf-8").splitlines()
                if s.strip()
            ]
        return list(dict.fromkeys(syms))
    return _default_universe()


def _year_chunks(start: str, end: str, list_date: str = "") -> list[tuple[str, str]]:
    """按年份切块；若已知上市日期，则起始年不早于上市年（减少空请求）"""
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    if list_date:
        ld = pd.Timestamp(list_date)
        if not pd.isna(ld) and ld > s:
            s = max(s, ld.replace(day=1))
    chunks: list[tuple[str, str]] = []
    year = s.year
    while year <= e.year:
        lo = max(s, pd.Timestamp(year, 1, 1))
        hi = min(e, pd.Timestamp(year, 12, 31))
        if lo <= hi:
            chunks.append((lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d")))
        year += 1
    return chunks


def _fetch_meta(client: IFinDClient, raw_dir: Path, symbol: str) -> tuple[list, str]:
    """拉取并缓存 meta（评级事件 + 上市日期），返回 (ratings, list_date)"""
    meta_path = raw_dir / symbol / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return meta.get("ratings", []), meta.get("list_date", "")
    list_date, events = client.fetch_convertible_meta(symbol)
    ratings = [{"eff_date": d, "rating": r} for d, r in events]
    # 空结果不落盘缓存，下次运行会重试（避免把"解析失败"固化为"无数据"）
    if not list_date and not ratings:
        logger.warning(f"{symbol}: meta 解析为空（未缓存），list_date/评级缺失")
        return ratings, ""
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        json.dumps({"list_date": list_date or "", "ratings": ratings}, ensure_ascii=False),
        encoding="utf-8",
    )
    return ratings, list_date or ""


def _rating_for_date(ratings: list[dict], as_of: pd.Timestamp) -> str:
    """按生效日期前向映射：取生效日 <= as_of 的最新评级；无则空"""
    best = ""
    for ev in ratings:
        if pd.Timestamp(ev["eff_date"]).normalize() <= as_of:
            best = ev["rating"]
        else:
            break
    return best


def _collection_audit(
    symbols: list[str], raw_dir: Path, pit: pd.DataFrame
) -> dict:
    actual = {
        normalize_cb_symbol(str(symbol))
        for symbol in pit["symbol"].dropna()
        if str(symbol).strip()
    }
    missing_meta: list[str] = []
    missing_daily: list[str] = []
    no_usable_pit: list[str] = []
    lifecycle: dict[str, dict[str, str]] = {}

    for symbol in symbols:
        symbol_dir = raw_dir / symbol
        meta_path = symbol_dir / "meta.json"
        daily_paths = list(symbol_dir.glob("*.csv")) if symbol_dir.exists() else []
        if not meta_path.exists():
            missing_meta.append(symbol)
        elif not daily_paths:
            missing_daily.append(symbol)
        elif symbol not in actual:
            no_usable_pit.append(symbol)

    lifecycle_rows = pit.copy()
    lifecycle_rows["symbol"] = lifecycle_rows["symbol"].map(normalize_cb_symbol)
    for symbol, rows in lifecycle_rows.groupby("symbol"):
        normalized = normalize_cb_symbol(str(symbol))
        dates = pd.to_datetime(rows["as_of"], errors="coerce").dropna()
        if dates.empty:
            continue
        lifecycle[normalized] = {
            "first_observed": dates.min().strftime("%Y-%m-%d"),
            "last_observed": dates.max().strftime("%Y-%m-%d"),
        }

    return {
        "missing_meta_symbols": sorted(missing_meta),
        "missing_daily_symbols": sorted(missing_daily),
        "no_usable_pit_symbols": sorted(no_usable_pit),
        "observed_lifecycle": lifecycle,
    }


def _write_coverage(
    report: dict, symbols: list[str], raw_dir: Path, pit: pd.DataFrame, path: Path
) -> None:
    report.update(_collection_audit(symbols, raw_dir, pit))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    p = argparse.ArgumentParser(description="构建可转债 PIT 历史快照")
    p.add_argument("--symbols", default=None, help="逗号分隔的转债代码")
    p.add_argument("--universe-file", default=None, help="universe 文件（行=代码或 csv）")
    p.add_argument("--start", default="2021-01-01", help="PIT 起始日期")
    p.add_argument("--end", default=None, help="PIT 结束日期，默认今天")
    p.add_argument("--out", default=None, help="输出 parquet 路径")
    p.add_argument("--coverage-out", default=None, help="覆盖率报告 json 路径")
    p.add_argument("--raw-dir", default=None, help="原始 CSV/meta 缓存目录")
    p.add_argument("--rate-delay", type=float, default=1.2, help="iFinD 请求间隔（秒）")
    p.add_argument("--limit", type=int, default=0, help="最多处理 N 只（0=全部，调试用）")
    p.add_argument("--validate-only", action="store_true",
                   help="只校验已有快照，不发网络请求")
    p.add_argument("--assemble-only", action="store_true",
                   help="只从 raw 缓存重建 parquet，不发网络请求")
    p.add_argument("--pit", default=None, help="--validate-only 时的快照路径")
    args = p.parse_args(argv)

    end = args.end or pd.Timestamp.today().strftime("%Y-%m-%d")
    out_path = Path(args.out or (_PROJECT_ROOT / "data" / "pit" / "convertible_pit.parquet"))
    coverage_path = Path(
        args.coverage_out or (_PROJECT_ROOT / "data" / "pit" / "coverage.json")
    )
    raw_dir = Path(args.raw_dir or (_PROJECT_ROOT / "data" / "pit" / "raw"))

    # --validate-only：不发网络
    if args.validate_only:
        pit_path = Path(args.pit or out_path)
        table = pd.read_parquet(pit_path)
        report = validate_pit_snapshot(
            table,
            start=args.start,
            end=end,
            requested_symbols=_load_universe(args),
        )
        _write_coverage(
            report, _load_universe(args), raw_dir, table, coverage_path
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    symbols = _load_universe(args)
    if args.limit > 0:
        symbols = symbols[: args.limit]
    if not symbols:
        logger.error("universe 为空")
        return 1
    logger.info(f"universe {len(symbols)} 只: {symbols}")

    client = None if args.assemble_only else IFinDClient(rate_delay=args.rate_delay)
    frames: list[pd.DataFrame] = []

    for idx, symbol in enumerate(symbols, 1):
        logger.info(f"[{idx}/{len(symbols)}] {symbol}")
        if args.assemble_only:
            meta_path = raw_dir / symbol / "meta.json"
            if not meta_path.exists():
                logger.info(f"{symbol}: 无 meta 缓存，跳过")
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            ratings = meta.get("ratings", [])
            list_date = meta.get("list_date", "")
            maturity_date = meta.get("maturity_date", "")
            issue_size = meta.get("issue_size")
        else:
            try:
                ratings, list_date = _fetch_meta(client, raw_dir, symbol)
                # 在线路径同样消费 enrich 后的静态字段（若已 enrich）
                m_extra = {}
                mp = raw_dir / symbol / "meta.json"
                if mp.exists():
                    m_extra = json.loads(mp.read_text(encoding="utf-8"))
                maturity_date = m_extra.get("maturity_date", "")
                issue_size = m_extra.get("issue_size")
            except Exception as e:
                logger.warning(f"{symbol}: meta 获取失败，跳过: {e}")
                continue
        # 按上市日期裁剪起始年份，晚上市的券不空跑早年份
        chunks = _year_chunks(args.start, end, list_date)
        per_bond: list[pd.DataFrame] = []
        for lo, hi in chunks:
            csv_path = raw_dir / symbol / f"{lo[:4]}.csv"
            if csv_path.exists():
                try:
                    df = pd.read_csv(csv_path, dtype=str, on_bad_lines="skip")
                    df["date"] = pd.to_datetime(df["date"], errors="coerce")
                    df["close"] = pd.to_numeric(df["close"], errors="coerce")
                    df["premium_rate"] = pd.to_numeric(df["premium_rate"], errors="coerce")
                except Exception as e:
                    logger.warning(f"{symbol} {lo[:4]}: 缓存读取失败: {e}")
                    df = pd.DataFrame()
            else:
                df = pd.DataFrame()
            if df is not None and len(df) == 0 and not args.assemble_only:
                try:
                    df = client.fetch_convertible_daily(symbol, lo, hi)
                    csv_path.parent.mkdir(parents=True, exist_ok=True)
                    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
                except Exception as e:
                    logger.warning(f"{symbol} [{lo}~{hi}] 拉取失败: {e}")
                    df = pd.DataFrame()
            if df is not None and len(df):
                per_bond.append(df)
        if not per_bond:
            logger.warning(f"{symbol}: 无任何日线数据，跳过")
            continue

        daily = pd.concat(per_bond, ignore_index=True)
        daily = daily.dropna(subset=["date", "close"])
        daily = daily.drop_duplicates(subset=["date"], keep="last")
        daily = daily.sort_values("date")
        daily["symbol"] = symbol
        daily["list_date"] = pd.to_datetime(list_date, errors="coerce") if list_date else pd.NaT
        # 静态生命周期字段广播（B1 规则消费：临期排除用 maturity_date，规模因子用 issue_size）
        daily["maturity_date"] = (
            pd.to_datetime(maturity_date, errors="coerce") if maturity_date else pd.NaT
        )
        daily["issue_size"] = (
            float(issue_size) if issue_size is not None else float("nan")
        )
        # 上市前剔除（iFinD 可能返回上市前无价格，这里再保险一次）
        if daily["list_date"].notna().any():
            daily = daily[daily["date"] >= daily["list_date"]]
        if daily.empty:
            continue
        daily["rating"] = daily["date"].map(lambda d: _rating_for_date(ratings, d))
        daily["rating"] = daily["rating"].replace("", pd.NA)
        frames.append(daily)

    if not frames:
        logger.error("没有任何标的的 PIT 数据，未输出文件")
        return 1

    pit = pd.concat(frames, ignore_index=True)
    pit = pit.rename(columns={"date": "as_of", "close": "price"})
    # 只保留回测窗口内、且有价格 + 溢价率的有效行（周末/停牌无溢价率行剔除）
    pit = pit[
        (pit["as_of"] >= pd.Timestamp(args.start))
        & (pit["as_of"] <= pd.Timestamp(end))
    ]
    pit = pit.dropna(subset=["price", "premium_rate"])
    pit = pit[
        [
            "as_of", "symbol", "price", "premium_rate", "rating", "list_date",
            "maturity_date", "issue_size",
        ]
    ].sort_values(["as_of", "symbol"]).reset_index(drop=True)

    report = validate_pit_snapshot(
        pit,
        start=args.start,
        end=end,
        requested_symbols=symbols,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pit.to_parquet(out_path, index=False)
    _write_coverage(report, symbols, raw_dir, pit, coverage_path)
    logger.info(f"已输出 {out_path}（{len(pit)} 行）")
    logger.info(f"覆盖率报告: {coverage_path}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
