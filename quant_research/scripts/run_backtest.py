#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""回测脚本

用法：
    # 单标的 SMA 回测
    python scripts/run_backtest.py --strategy sma --symbol 600519.SH --start 2020-01-01 --end 2024-12-31

    # ETF 动量轮动（universe 与参数从 config/strategy/etf_momentum.yaml 读取）
    python scripts/run_backtest.py --strategy etf_momentum --start 2015-01-01

    # ETF 轮动 + 自定义策略配置
    python scripts/run_backtest.py --strategy etf_momentum --strategy-config config/strategy/etf_momentum.yaml
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from quant_research.config import load_config, load_strategy_config, AppConfig, StrategyConfig
from quant_research.data import DataLoader
from quant_research.backtest import (
    BacktestEngine,
    SMACross,
    ETFMomentum,
    ETFMWU,
    build_experts,
    ConvertibleDoubleLow,
    compute_double_low_scores,
    load_pit_snapshot,
)


logger = logging.getLogger("backtest_run")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="quant_research 回测脚本")
    p.add_argument("--config", default=None, help="主配置文件路径")
    p.add_argument("--symbol", default="600519.SH", help="单标的代码（sma 策略）")
    p.add_argument("--symbols", default=None, help="多标的，逗号分隔（覆盖策略配置的 universe）")
    p.add_argument("--start", default="2020-01-01", help="起始日期")
    p.add_argument("--end", default=None, help="结束日期，默认今天")
    p.add_argument(
        "--strategy",
        default="sma",
        choices=["sma", "etf_momentum", "etf_mwu", "etf_mwu_cb", "convertible_double_low"],
        help="策略：sma 双均线 / etf_momentum ETF 动量轮动 / etf_mwu MWU 多策略融合 / "
             "etf_mwu_cb ETF+可转债 MWU / convertible_double_low 可转债双低",
    )
    p.add_argument("--fast", type=int, default=5, help="SMA 快线周期")
    p.add_argument("--slow", type=int, default=20, help="SMA 慢线周期")
    p.add_argument(
        "--strategy-config",
        default=None,
        help="策略配置 yaml 路径；etf_momentum/etf_mwu 默认 config/strategy/<name>.yaml",
    )
    p.add_argument("--etf", action="store_true", help="sma 策略下使用 ETF 专用数据接口")
    p.add_argument(
        "--pit-snapshots",
        default=None,
        help="可转债 PIT 历史快照文件（csv/parquet/pkl，需含 as_of/symbol/price/premium_rate/rating/list_date）",
    )
    p.add_argument(
        "--cb-symbols-file",
        default=None,
        help="可转债日线 feed 限制清单（每行一个代码）：PIT 模式下默认为快照全部"
        " symbol 建_feed，全量池（数百只）会拖垮引擎；传入预计算的候选并集"
        "（如 data/cb_candidate_union.txt）可显著提速，选债结果不受影响"
        "（专家只从有 feed 的券里选，并集 ⊇ 全部过滤后候选）",
    )
    return p


def _resolve_etf_symbols(
    args: argparse.Namespace,
    strat_cfg: StrategyConfig,
) -> list[str]:
    """etf_momentum：universe 解析优先级 CLI --symbols > 策略 yaml"""
    if args.symbols:
        return [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    universe = strat_cfg.universe or []
    if not universe:
        raise SystemExit("etf_momentum 需要 universe：请在策略 yaml 配置或用 --symbols 指定")
    return [s.strip().upper() for s in universe]


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    cfg = load_config(args.config)
    end = args.end or time.strftime("%Y-%m-%d")

    strategy_cls = SMACross
    strategy_params: dict = {}
    use_etf_api = args.etf
    commission_overrides: Optional[dict] = None

    if args.strategy == "etf_momentum":
        strategy_cls = ETFMomentum
        use_etf_api = True
        # ETF 现货免印花税与过户费（仅佣金，部分券商免最低 5 元限制）
        commission_overrides = {"stamp_duty": 0.0, "transfer_fee": 0.0}
        try:
            strat_cfg = load_strategy_config(args.strategy_config, name="etf_momentum")
        except FileNotFoundError as e:
            print(f"策略配置不存在: {e}", file=sys.stderr)
            return 2
        symbols = _resolve_etf_symbols(args, strat_cfg)
        strategy_params = dict(strat_cfg.params)
        strategy_params["rebalance_freq"] = strat_cfg.rebalance_freq
        logger.info(
            f"ETF 动量轮动: universe={len(symbols)} 只, params={strategy_params}"
        )
    elif args.strategy in ("etf_mwu", "etf_mwu_cb"):
        strategy_cls = ETFMWU
        use_etf_api = True
        commission_overrides = {"stamp_duty": 0.0, "transfer_fee": 0.0}
        strategy_name = args.strategy
        try:
            strat_cfg = load_strategy_config(args.strategy_config, name=strategy_name)
        except FileNotFoundError as e:
            print(f"策略配置不存在: {e}", file=sys.stderr)
            return 2
        symbols = _resolve_etf_symbols(args, strat_cfg)
        if not strat_cfg.experts:
            raise SystemExit(f"{strategy_name} 需要在策略 yaml 配置 experts")
        strategy_params = dict(strat_cfg.params)
        strategy_params["rebalance_freq"] = strat_cfg.rebalance_freq
        logger.info(
            f"ETF MWU 融合: universe={len(symbols)} 只, "
            f"experts={list(strat_cfg.experts)}, "
            f"params={ {k: v for k, v in strategy_params.items() if k != 'experts'} }"
        )
    elif args.strategy == "convertible_double_low":
        strategy_cls = ConvertibleDoubleLow
        use_etf_api = False
        # 可转债免印花税与过户费
        commission_overrides = {"stamp_duty": 0.0, "transfer_fee": 0.0}
        try:
            strat_cfg = load_strategy_config(args.strategy_config, name="convertible_double_low")
        except FileNotFoundError as e:
            print(f"策略配置不存在: {e}", file=sys.stderr)
            return 2
        strategy_params = dict(strat_cfg.params)
        strategy_params["rebalance_freq"] = strat_cfg.rebalance_freq
        # yaml 里有策略类不认识的键（candidate_pool/exclude_force_redeem/
        # min_maturity_years/min_volume 等是选债过滤用），Backtrader 只接受
        # ConvertibleDoubleLow 定义的 params，过滤掉避免 TypeError
        cb_params = {
            k: v for k, v in strategy_params.items()
            if k in ConvertibleDoubleLow.params._getkeys()
        }
        logger.info(
            f"可转债双低: params={strategy_params}, "
            f"传入策略={cb_params}, universe 由当日快照动态生成"
        )
    else:
        if args.symbols:
            symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        else:
            symbols = [args.symbol.upper()]
        strategy_params = {"fast_period": args.fast, "slow_period": args.slow}

    logger.info(f"回测标的: {len(symbols) if args.strategy != 'convertible_double_low' else '动态'}, 日期: {args.start} ~ {end}")

    # 数据预热：均线/动量窗口需要历史数据，若数据起点=回测起点，
    # 第一个窗口期（如 ma_filter=250 日）将完全空转。按最长窗口的
    # 1.7 倍换算日历日提前加载（250 交易日 ≈ 365 日历日）。
    data_start = args.start
    if args.strategy in ("etf_momentum", "etf_mwu", "etf_mwu_cb"):
        warmup_bars = max(
            strategy_params.get("ma_filter", 250),
            strategy_params.get("lookback", 60),
        )
        # etf_mwu：专家窗口可能大于顶层参数（如 mean_reversion ma_filter=200）
        for exp_kw in (strat_cfg.experts or {}).values():
            for key in ("ma_filter", "lookback"):
                if isinstance(exp_kw, dict) and key in exp_kw:
                    warmup_bars = max(warmup_bars, int(exp_kw[key]))
        data_start = (
            pd.Timestamp(args.start) - pd.Timedelta(days=int(warmup_bars * 1.7))
        ).strftime("%Y-%m-%d")

    with DataLoader(cfg) as loader:
        data_feeds = {}
        bond_table = None
        expert_spec = None

        # 可转债双低：先从当日快照算双低候选，再下载这些转债的日线
        if args.strategy == "convertible_double_low":
            bond_all = loader.get_convertible_bond_list()
            bond_table = compute_double_low_scores(
                bond_all,
                top_n=strategy_params.get("candidate_pool", 15),
                min_rating=strategy_params.get("min_rating", "AA-"),
                max_premium=strategy_params.get("max_premium", 20.0),
                as_of=args.start,
            )
            symbols = list(bond_table["symbol"])
            logger.info(f"双低候选 {len(symbols)} 只: {symbols}")
            if not symbols:
                logger.error("双低候选为空，无法回测")
                return 1
            for sym in symbols:
                try:
                    df = loader.get_convertible_bond_daily(sym, data_start, end)
                    if len(df) > 0:
                        data_feeds[sym] = df
                    else:
                        logger.warning(f"{sym}: 空数据，跳过")
                except Exception as e:
                    logger.error(f"{sym} 数据获取失败: {e}")
        elif args.strategy in ("etf_mwu", "etf_mwu_cb"):
            expert_spec = {key: dict(value) for key, value in strat_cfg.experts.items()}
            convertible_symbols: list[str] = []
            if args.strategy == "etf_mwu_cb":
                cb_spec = expert_spec.get("convertible_double_low")
                if cb_spec is None:
                    raise SystemExit("etf_mwu_cb 需要 convertible_double_low 专家")
                if args.pit_snapshots:
                    pit_table = load_pit_snapshot(args.pit_snapshots)
                    convertible_symbols = [
                        str(symbol).upper() for symbol in pit_table["symbol"].dropna().unique()
                    ]
                    if args.cb_symbols_file:
                        from pathlib import Path as _P

                        allowed = {
                            line.strip().upper()
                            for line in _P(args.cb_symbols_file)
                            .read_text(encoding="utf-8")
                            .splitlines()
                            if line.strip() and not line.startswith("#")
                        }
                        convertible_symbols = [
                            s for s in convertible_symbols if s in allowed
                        ]
                        logger.info(
                            f"CB feed 限制清单: {len(convertible_symbols)} 只（全量池过滤后）"
                        )
                    expert_spec.pop("convertible_double_low")
                    expert_spec["convertible_double_low_pit"] = {
                        key: value for key, value in cb_spec.items()
                        if key != "candidate_pool"
                    }
                    expert_spec["convertible_double_low_pit"]["pit_table"] = pit_table
                    logger.info(
                        f"PIT 可转债快照: {args.pit_snapshots}, "
                        f"{len(pit_table)} 条, {len(convertible_symbols)} 只"
                    )
                else:
                    bond_all = loader.get_convertible_bond_list()
                    bond_table = compute_double_low_scores(
                        bond_all,
                        top_n=int(cb_spec.get("candidate_pool", max(int(cb_spec.get("top_n", 5)) * 3, 15))),
                        min_rating=cb_spec.get("min_rating", "AA-"),
                        max_premium=float(cb_spec.get("max_premium", 20.0)),
                        exclude_new_days=int(cb_spec.get("exclude_new_days", 30)),
                        as_of=args.start,
                    )
                    convertible_symbols = [str(symbol).upper() for symbol in bond_table["symbol"]]
                    cb_expert = {
                        key: value for key, value in cb_spec.items()
                        if key != "candidate_pool"
                    }
                    cb_expert["bond_table"] = bond_table
                    expert_spec["convertible_double_low"] = cb_expert
                strategy_params["convertible_symbols"] = tuple(convertible_symbols)
                logger.info(f"MWU 可转债标的 {len(convertible_symbols)} 只")

            strategy_params["experts"] = build_experts(expert_spec)
            for sym in symbols:
                try:
                    df = loader.get_etf_daily(sym, data_start, end)
                    if len(df) > 0:
                        data_feeds[sym] = df
                    else:
                        logger.warning(f"{sym}: 空数据，跳过")
                except Exception as e:
                    logger.error(f"{sym} 数据获取失败: {e}")
            for sym in convertible_symbols:
                try:
                    df = loader.get_convertible_bond_daily(sym, data_start, end)
                    if len(df) > 0:
                        data_feeds[sym] = df
                    else:
                        logger.warning(f"{sym}: 空数据，跳过")
                except Exception as e:
                    logger.error(f"{sym} 数据获取失败: {e}")
        else:
            for sym in symbols:
                try:
                    if use_etf_api:
                        df = loader.get_etf_daily(sym, data_start, end)
                    else:
                        df = loader.get_daily_bars(sym, args.start, end)
                    if len(df) > 0:
                        data_feeds[sym] = df
                        logger.info(f"{sym}: {len(df)} 行")
                    else:
                        logger.warning(f"{sym}: 空数据，跳过")
                except Exception as e:
                    logger.error(f"{sym} 数据获取失败: {e}")

        if not data_feeds:
            logger.error("无可用数据，无法回测")
            return 1

        # 可转债双低：把候选快照注入策略参数（用过滤后的 cb_params）
        if args.strategy == "convertible_double_low":
            cb_params["bond_table"] = bond_table
            engine_params = cb_params
        else:
            engine_params = strategy_params

        engine = BacktestEngine(cfg, commission_overrides=commission_overrides)
        result = engine.run(strategy_cls, engine_params, data_feeds)

        print()
        print(result.summary())
        print()
        return 0


if __name__ == "__main__":
    sys.exit(main())
