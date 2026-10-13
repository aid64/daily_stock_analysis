"""Isolated, forward-only paper account. Never opens the DSA portfolio database."""
from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from contextlib import contextmanager
from datetime import date
from pathlib import Path


def _fingerprint(value: dict) -> str:
    body = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


@contextmanager
def _exclusive(path: Path):
    lock = path.with_suffix(path.suffix + '.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.close(fd)
        yield
    finally:
        lock.unlink()


def _save(path: Path, state: dict) -> None:
    state = dict(state)
    state['content_hash'] = _fingerprint({k: v for k, v in state.items() if k != 'content_hash'})
    tmp = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        with tmp.open('w', encoding='utf-8') as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class PaperAccount:
    def __init__(self, path: Path):
        self.path = Path(path)

    @classmethod
    def create(cls, path: Path, *, strategy_id: str, strategy_config: dict,
               initial_cash: float, start_date: date, universe: list[str],
               commission: float, min_commission: float, slippage: float) -> PaperAccount:
        values = (initial_cash, commission, min_commission, slippage)
        if not all(math.isfinite(float(v)) for v in values):
            raise ValueError('Account inputs must be finite')
        if initial_cash <= 0 or min(commission, min_commission, slippage) < 0 or slippage >= 1:
            raise ValueError('Invalid paper capital or fees')
        if not universe or len(set(universe)) != len(universe):
            raise ValueError('Universe must be nonempty and unique')
        account = cls(path)
        with _exclusive(account.path):
            if account.path.exists():
                raise FileExistsError(account.path)
            if not all(s.endswith(('.SH', '.SZ')) for s in universe):
                raise ValueError('ETF universe requires exchange-qualified symbols')
            _save(account.path, {
                'schema': 'etf_paper_account_v1', 'mode': 'paper_only',
                'strategy_id': strategy_id, 'strategy_config': strategy_config,
                'config_hash': _fingerprint(strategy_config), 'universe': universe,
                'initial_cash': float(initial_cash), 'cash': float(initial_cash),
                'start_date': start_date.isoformat(), 'last_day': None,
                'fees': {'commission': commission, 'min_commission': min_commission,
                         'slippage': slippage, 'stamp_duty': 0.0, 'transfer_fee': 0.0},
                'positions': {}, 'fills': [], 'pending': None, 'events': [],
                'equity_curve': [],
            })
        return account

    def snapshot(self) -> dict:
        state = json.loads(self.path.read_text(encoding='utf-8'))
        if state.get('schema') != 'etf_paper_account_v1' or state.get('mode') != 'paper_only':
            raise ValueError('Not a supported paper account')
        if state.get('content_hash') != _fingerprint({k: v for k, v in state.items() if k != 'content_hash'}):
            raise ValueError('Paper account integrity check failed')
        return state

    @staticmethod
    def _broker(state: dict):
        from .broker import Order, PaperBroker, Side

        broker = PaperBroker(initial_cash=state['initial_cash'], **state['fees'])
        # Replaying recorded fills restores account state without mutating broker internals.
        for fill in state['fills']:
            broker.send_order(Order(fill['symbol'], Side(fill['side']),
                                    fill['quantity'], fill['reference_price']))
        if not math.isclose(broker.get_account().cash, state['cash'], abs_tol=1e-7):
            raise ValueError('Paper fills and cash do not reconcile')
        return broker

    def advance_day(self, day: date, bars: dict, *, targets: dict | None = None,
                    signal_id: str | None = None) -> dict:
        """Settle the previous signal at this bar's open, mark at close, then queue a signal.

        Caller supplies complete, consecutive exchange sessions. A same-day retry must
        contain identical inputs. No price interpolation and no same-day signal fills.
        """
        from .broker import BrokerError, Order, Side
        from ..core.rules import asset_type, get_limit_pct

        day_text = day.isoformat()
        with _exclusive(self.path):
            state = self.snapshot()
            if day.weekday() >= 5 or day_text < state['start_date']:
                raise ValueError('Session precedes paper inception or is a weekend')
            if set(bars) != set(state['universe']):
                raise ValueError('A complete configured universe is required')
            clean = {}
            for symbol, bar in bars.items():
                if asset_type(symbol) != 'etf':
                    raise ValueError('This paper account supports ETFs only')
                values = {k: float(bar[k]) for k in ('open', 'high', 'low', 'close', 'volume')}
                if not all(math.isfinite(v) for v in values.values()):
                    raise ValueError('Bar values must be finite')
                if min(values[k] for k in ('open', 'high', 'low', 'close')) <= 0 or values['volume'] < 0:
                    raise ValueError('Invalid bar prices or volume')
                if not (values['low'] <= min(values['open'], values['close'])
                        <= max(values['open'], values['close']) <= values['high']):
                    raise ValueError('Inconsistent OHLC bar')
                clean[symbol] = values
            if targets is not None:
                if not signal_id or not set(targets).issubset(state['universe']):
                    raise ValueError('Targets require a signal id and configured symbols')
                if any(isinstance(q, bool) or not isinstance(q, int) or q < 0 or q % 100 for q in targets.values()):
                    raise ValueError('ETF targets must be nonnegative multiples of 100 units')
            payload_hash = _fingerprint({'day': day_text, 'bars': clean,
                                         'targets': targets, 'signal_id': signal_id})
            if state['last_day']:
                if day_text < state['last_day']:
                    raise ValueError('Cannot move paper account backwards')
                if day_text == state['last_day']:
                    if state['events'][-1]['input_hash'] != payload_hash:
                        raise ValueError('Changed data for a settled session; create a separate run')
                    return state
            if signal_id and any(e.get('signal_id') == signal_id for e in state['events']):
                raise ValueError('Signal id already recorded')

            broker = self._broker(state)
            rejected = []
            pending = state['pending']
            if pending:
                current = {p.symbol: p.quantity for p in broker.get_account().positions}
                goals = pending['targets']
                orders = []
                for symbol in sorted(set(current) | set(goals)):
                    diff = goals.get(symbol, 0) - current.get(symbol, 0)
                    if diff:
                        orders.append(Order(symbol, Side.BUY if diff > 0 else Side.SELL,
                                            abs(diff), clean[symbol]['open']))
                orders.sort(key=lambda order: (order.side is Side.BUY, order.symbol))
                for order in orders:
                    bar = clean[order.symbol]
                    previous = state['marks'][order.symbol]
                    limit = get_limit_pct(order.symbol, day)
                    shift = bar['open'] / previous - 1
                    reason = None
                    if bar['volume'] == 0:
                        reason = 'no_volume'
                    elif limit is not None and (
                        (order.side is Side.BUY and shift >= limit - 0.0005)
                        or (order.side is Side.SELL and shift <= -limit + 0.0005)
                    ):
                        reason = 'price_limit_or_corporate_action_requires_review'
                    if reason:
                        rejected.append({'symbol': order.symbol, 'reason': reason})
                        continue
                    cash_before = broker.get_account().cash
                    try:
                        broker.send_order(order)
                    except BrokerError as exc:
                        rejected.append({'symbol': order.symbol, 'reason': str(exc)})
                        continue
                    cash_after = broker.get_account().cash
                    signed_notional = order.notional if order.side is Side.SELL else -order.notional
                    state['fills'].append({
                        'signal_id': pending['signal_id'], 'signal_date': pending['signal_date'],
                        'execution_date': day_text, 'symbol': order.symbol, 'side': order.side.value,
                        'quantity': order.filled_qty, 'price': order.filled_price,
                        'reference_price': order.price,
                        'fee': round(cash_before + signed_notional - cash_after, 8),
                    })
            acc = broker.get_account()
            state['cash'] = acc.cash
            state['positions'] = {p.symbol: {'quantity': p.quantity, 'cost_price': p.cost_price}
                                  for p in acc.positions}
            state['marks'] = {s: bar['close'] for s, bar in clean.items()}
            equity = acc.cash + sum(p.quantity * clean[p.symbol]['close'] for p in acc.positions)
            peak = max([state['initial_cash'], equity]
                       + [point['equity'] for point in state['equity_curve']])
            state['equity_curve'].append({
                'date': day_text, 'cash': acc.cash, 'equity': equity,
                'return_pct': (equity / state['initial_cash'] - 1) * 100,
                'drawdown_pct': (1 - equity / peak) * 100,
            })
            state['pending'] = ({'signal_id': signal_id, 'signal_date': day_text, 'targets': targets}
                                if targets is not None else None)
            state['last_day'] = day_text
            state['events'].append({'date': day_text, 'input_hash': payload_hash,
                                    'signal_id': signal_id, 'rejected': rejected})
            _save(self.path, state)
            return self.snapshot()
