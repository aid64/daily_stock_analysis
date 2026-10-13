"""Forward-only ETF virtual account CLI; no broker credentials or DSA imports."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quant_research.backtest.strategies import momentum_scores
from quant_research.core.market import ArrayMarketView
from quant_research.trade.paper_validation import PaperAccount


def _period(day: date) -> tuple:
    return tuple(day.isocalendar()[:2])


def advance_snapshot(account: PaperAccount, feeds: dict, *, through: date) -> dict:
    """Consume only completed input sessions since inception, never reset the account."""
    import exchange_calendars as xcals

    state = account.snapshot()
    config = state['strategy_config']
    if config.get('name') != 'etf_momentum' or config.get('rebalance_freq', 'W') != 'W':
        raise ValueError('Only the existing weekly etf_momentum strategy is supported')
    params = config.get('params', {})
    supported = {'lookback', 'top_k', 'ma_filter', 'min_volume', 'defensive', 'mom_windows', 'vol_adjusted'}
    if set(params) - supported:
        raise ValueError('Unsupported strategy parameters; do not silently change the strategy')
    universe = state['universe']
    if not set(universe).issubset(feeds):
        raise ValueError('Missing configured ETF data')
    frames = {}
    for symbol in universe:
        frame = feeds[symbol].copy()
        frame['date'] = pd.to_datetime(frame['date'])
        frame = frame.sort_values('date').set_index('date')
        if frame.index.has_duplicates or frame.empty:
            raise ValueError('Empty or duplicate ETF sessions')
        frames[symbol] = frame
    ends = {frame.index[-1].date() for frame in frames.values()}
    if len(ends) != 1:
        raise ValueError('ETF data ends are not synchronized')
    end = min(through, next(iter(ends)))
    start = date.fromisoformat(state['start_date'])
    if end < start:
        return state
    calendar = xcals.get_calendar('XSHG')
    sessions = calendar.sessions_in_range(start, end)
    for symbol, frame in frames.items():
        if any(pd.Timestamp(session).tz_localize(None) not in frame.index for session in sessions):
            raise ValueError(f'Missing forward session for {symbol}; account unchanged')
    windows = tuple(params.get('mom_windows') or [params.get('lookback', 60)])
    ma = int(params.get('ma_filter', 250))
    top_k = int(params.get('top_k', 3))
    if not windows or min(windows) <= 0 or ma <= 0 or top_k <= 0:
        raise ValueError('Invalid strategy lookback, MA, or position count')
    defensive = params.get('defensive')
    for session in sessions:
        ts = pd.Timestamp(session).tz_localize(None)
        day = ts.date()
        if state['last_day'] and day.isoformat() <= state['last_day']:
            continue
        bars = {s: {k: float(f.loc[ts, k]) for k in ('open', 'high', 'low', 'close', 'volume')}
                for s, f in frames.items()}
        previous_session = calendar.previous_session(session).date()
        rebalance = _period(previous_session) != _period(day)
        targets = None
        signal_id = None
        if rebalance:
            arrays = {s: (f.loc[:ts, 'close'].to_numpy(), f.loc[:ts, 'amount'].to_numpy())
                      for s, f in frames.items()}
            if any(len(values[0]) < max(max(windows) + 1, ma) for values in arrays.values()):
                raise ValueError('Insufficient warmup history; no paper signal generated')
            view = ArrayMarketView(arrays, current_date=day)
            scores = momentum_scores(view, windows, ma, float(params.get('min_volume', 1000000)),
                                     bool(params.get('vol_adjusted', False)), defensive)
            selected = [s for s, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True)[:top_k]]
            if not selected and defensive in bars and bars[defensive]['volume'] > 0:
                selected = [defensive]
            equity = state['cash'] + sum(p['quantity'] * bars[s]['close'] for s, p in state['positions'].items())
            per_value = equity / len(selected) if selected else 0
            # Match the strategy's retained-position policy; no forced trimming of retained ETFs.
            targets = {}
            for symbol in selected:
                current = state['positions'].get(symbol, {}).get('quantity', 0)
                price = bars[symbol]['close']
                if not np.isfinite(price) or price <= 0:
                    raise ValueError('Invalid target price')
                add = max(0, int((per_value - current * price) * 0.98 / price / 100)) * 100
                targets[symbol] = current + add
            signal_id = f"{state['strategy_id']}:{state['config_hash']}:{day.isoformat()}"
        state = account.advance_day(day, bars, targets=targets, signal_id=signal_id)
    return state


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Independent ETF virtual account (no real orders)')
    parser.add_argument('--account', type=Path, required=True)
    parser.add_argument('--init', action='store_true')
    parser.add_argument('--capital', type=float, default=10000)
    parser.add_argument('--strategy-config', type=Path, default=ROOT / 'config/strategy/etf_momentum.yaml')
    parser.add_argument('--commission', type=float, default=0.0001)
    parser.add_argument('--min-commission', type=float, default=5)
    parser.add_argument('--slippage', type=float, default=0.001)
    parser.add_argument('--snapshot', type=Path)
    args = parser.parse_args(argv)
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    account = PaperAccount(args.account)
    if args.init:
        config = yaml.safe_load(args.strategy_config.read_text(encoding='utf-8'))
        if config.get('name') != 'etf_momentum':
            parser.error('This runner does not implement four-asset rotation or other catalog strategies')
        account = PaperAccount.create(
            args.account, strategy_id=config['name'], strategy_config=config,
            initial_cash=args.capital, start_date=now.date(), universe=config['universe'],
            commission=args.commission, min_commission=args.min_commission, slippage=args.slippage)
    if args.snapshot:
        if now.time() < time(15, 30):
            parser.error('Daily bars may be incomplete; advance only after 15:30 Asia/Shanghai')
        state = advance_snapshot(account, pd.read_pickle(args.snapshot), through=now.date())
    else:
        state = account.snapshot()
    print(json.dumps({k: state[k] for k in ('mode', 'strategy_id', 'start_date', 'last_day',
                                          'cash', 'positions', 'pending', 'equity_curve')},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
