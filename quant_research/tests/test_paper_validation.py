from datetime import date

import json
import pytest

from quant_research.trade.paper_validation import PaperAccount


def test_new_account_is_virtual_cash_only_and_survives_reload(tmp_path):
    path = tmp_path / 'account.json'
    account = PaperAccount.create(
        path, strategy_id='test_etf', strategy_config={'version': 1},
        initial_cash=10000, start_date=date(2026, 9, 28),
        universe=['510300.SH'], commission=0.0001,
        min_commission=5, slippage=0,
    )
    state = account.snapshot()
    assert state['mode'] == 'paper_only'
    assert state['cash'] == 10000
    assert state['positions'] == {}
    assert state['fills'] == []
    assert state['equity_curve'] == []
    assert PaperAccount(path).snapshot() == state


def test_signal_waits_for_later_open_and_cash_is_conserved(tmp_path):
    account = PaperAccount.create(
        tmp_path / 'account.json', strategy_id='test_etf', strategy_config={},
        initial_cash=10000, start_date=date(2026, 9, 28),
        universe=['510300.SH'], commission=0.0001, min_commission=5, slippage=0,
    )
    bar = {'open': 10, 'high': 11, 'low': 9, 'close': 10, 'volume': 10000}
    first = account.advance_day(date(2026, 9, 28), {'510300.SH': bar},
                                targets={'510300.SH': 100}, signal_id='s1')
    assert first['fills'] == []
    assert first['pending']['signal_date'] == '2026-09-28'
    assert first['cash'] == 10000
    next_bar = dict(bar, open=10.5, high=12, close=12)
    second = PaperAccount(account.path).advance_day(
        date(2026, 9, 29), {'510300.SH': next_bar})
    assert second['cash'] == 8945
    assert second['positions']['510300.SH']['quantity'] == 100
    assert second['fills'][0]['price'] == 10.5
    assert second['fills'][0]['fee'] == 5
    assert second['equity_curve'][-1]['equity'] == 10145
    assert second['pending'] is None
    assert PaperAccount(account.path).advance_day(
        date(2026, 9, 29), {'510300.SH': next_bar}) == second


@pytest.fixture
def account(tmp_path):
    return PaperAccount.create(
        tmp_path / 'account.json', strategy_id='test_etf', strategy_config={},
        initial_cash=10000, start_date=date(2026, 9, 28),
        universe=['510300.SH'], commission=0.0001, min_commission=5, slippage=0,
    )


def bars(**overrides):
    return {'510300.SH': dict(open=10, high=11, low=9, close=10, volume=10000, **overrides)}


def test_sell_on_following_session_and_recover_full_ledger(account):
    account.advance_day(date(2026, 9, 28), bars(), targets={'510300.SH': 100}, signal_id='buy')
    bought = account.advance_day(date(2026, 9, 29), bars(), targets={}, signal_id='exit')
    assert len(bought['fills']) == 1
    sold = PaperAccount(account.path).advance_day(date(2026, 9, 30), bars())
    assert sold['positions'] == {}
    assert sold['cash'] == 9990
    assert sold['fills'][1]['execution_date'] > sold['fills'][1]['signal_date']
    assert sold['equity_curve'][-1]['drawdown_pct'] == pytest.approx(0.1)


def test_missing_price_does_not_change_account(account):
    before = account.path.read_bytes()
    with pytest.raises(ValueError, match='complete'):
        account.advance_day(date(2026, 9, 28), {}, targets={'510300.SH': 100}, signal_id='x')
    assert account.path.read_bytes() == before


def test_changed_same_day_and_backward_date_rejected(account):
    account.advance_day(date(2026, 9, 28), bars())
    changed = bars()
    changed['510300.SH']['close'] = 10.1
    with pytest.raises(ValueError, match='Changed data'):
        account.advance_day(date(2026, 9, 28), changed)
    with pytest.raises(ValueError):
        account.advance_day(date(2026, 9, 25), bars())


def test_insufficient_cash_has_no_fabricated_fills(account):
    account.advance_day(date(2026, 9, 28), bars(), targets={'510300.SH': 1100}, signal_id='too-big')
    state = account.advance_day(date(2026, 9, 29), bars())
    assert state['fills'] == []
    assert state['cash'] == 10000
    assert state['events'][-1]['rejected']


@pytest.mark.parametrize('limit_open,volume', [(11, 10000), (10, 0)])
def test_price_limit_and_zero_volume_do_not_fill(account, limit_open, volume):
    account.advance_day(date(2026, 9, 28), bars(), targets={'510300.SH': 100}, signal_id='x')
    blocked = bars()
    blocked['510300.SH'].update(open=limit_open, volume=volume)
    state = account.advance_day(date(2026, 9, 29), blocked)
    assert state['fills'] == []
    assert state['events'][-1]['rejected']


def test_corrupt_state_is_not_silently_reset(account):
    state = json.loads(account.path.read_text(encoding='utf-8'))
    state['cash'] = 999999
    account.path.write_text(json.dumps(state), encoding='utf-8')
    with pytest.raises(ValueError, match='integrity'):
        account.snapshot()


def test_create_never_overwrites_existing_account(account):
    with pytest.raises(FileExistsError):
        PaperAccount.create(account.path, strategy_id='other', strategy_config={},
                            initial_cash=20000, start_date=date(2026, 9, 28),
                            universe=['510300.SH'], commission=0, min_commission=0, slippage=0)


def test_concurrent_lock_never_touches_account(account):
    account.path.with_suffix('.json.lock').write_text('owned', encoding='utf-8')
    before = account.path.read_bytes()
    with pytest.raises(FileExistsError):
        account.advance_day(date(2026, 9, 28), bars())
    assert account.path.read_bytes() == before

