from datetime import date

import pandas as pd

from quant_research.trade.paper_validation import PaperAccount
from scripts.run_etf_paper import advance_snapshot


def test_runner_does_not_backfill_before_inception_or_fill_signal_same_day(tmp_path):
    config = {'name': 'etf_momentum', 'params': {'lookback': 2, 'ma_filter': 3,
              'top_k': 1, 'min_volume': 1}, 'rebalance_freq': 'W', 'universe': ['510300.SH']}
    account = PaperAccount.create(
        tmp_path / 'account.json', strategy_id='etf_momentum', strategy_config=config,
        initial_cash=10000, start_date=date(2026, 9, 28), universe=['510300.SH'],
        commission=0.0001, min_commission=5, slippage=0,
    )
    frame = pd.DataFrame({'date': pd.to_datetime(['2026-09-22', '2026-09-23', '2026-09-24',
                         '2026-09-28', '2026-09-29']),
                          'open': [8, 9, 10, 11, 11.5], 'high': [9, 10, 11, 12, 12],
                          'low': [7, 8, 9, 10, 11], 'close': [8, 9, 10, 11, 11.5],
                          'volume': [10000]*5, 'amount': [100000]*5})
    state = advance_snapshot(account, {'510300.SH': frame}, through=date(2026, 9, 28))
    assert state['last_day'] == '2026-09-28'
    assert len(state['equity_curve']) == 1
    assert state['fills'] == []
    assert state['pending']['targets'] == {'510300.SH': 800}
    final = advance_snapshot(account, {'510300.SH': frame}, through=date(2026, 9, 29))
    assert final['cash'] == 795
    assert final['fills'][0]['execution_date'] == '2026-09-29'
    assert final['fills'][0]['price'] == 11.5
    assert advance_snapshot(account, {'510300.SH': frame}, through=date(2026, 9, 29)) == final
