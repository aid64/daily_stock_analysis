# Small-Capital Quant Research: Primary-Source Evidence

Date: 2026-08-28

Scope: evidence collection only. This note does not authorize or implement a
strategy change, live trading, or a claim of profitability.

## Decision Summary

1. A high in-sample Sharpe, a best grid row, or a walk-forward winner is not
   evidence by itself. Multiple testing and the full research search process
   must be part of the inference problem.
2. Turnover is a first-order model input for a small account. An alpha estimate
   that is not net of product-specific costs, minimum commissions, fills, and
   unfilled orders is not a deployable result.
3. MWU/expert-advice theory is a regret comparison against the best *fixed*
   expert in hindsight under bounded, fully observed losses. It does not imply
   positive trading PnL, nor does it validate a price-return implementation
   that ignores execution costs.
4. The Shanghai Stock Exchange (SSE) identifies the current base rule as
   `Shanghai Stock Exchange Trading Rules (2023 Revision)`, SSE Notice
   `[2023] No. 32`. Trading constraints must be parameterized by instrument
   and effective date; a single A-share T+1 or price-limit flag is not a safe
   model for a mixed ETF/convertible-bond universe.

## 1. Backtest Overfitting and Multiple Testing

### Primary sources

| Source | Verified conclusion | Boundary |
| --- | --- | --- |
| Bailey, Borwein, Lopez de Prado, Zhu, [*The Probability of Backtest Overfitting*](https://doi.org/10.2139/ssrn.2326253), SSRN 2326253 (2013) | The paper defines PBO through combinatorially symmetric cross-validation (CSCV): it asks how often the in-sample-selected configuration performs below the out-of-sample median. It is designed to quantify selection overfitting across a collection of backtests. | PBO needs the full configuration-by-period return matrix and enough materially distinct trials. It is not meaningful for a tiny, near-duplicate parameter set; it also does not repair look-ahead bias, survivor bias, or incorrect fills. |
| White, [*A Reality Check for Data Snooping*](https://doi.org/10.1111/1468-0262.00152), *Econometrica* 68(5), 2000 | Selection from many models invalidates the usual single-model significance interpretation. The Reality Check tests the best rule after accounting for the searched rule set. | The bootstrap design and benchmark must fit serial dependence and the research question. It is a model-set test, not proof that a live strategy will execute as assumed. |
| Harvey, Liu, Zhu, [NBER Working Paper 20592](https://www.nber.org/papers/w20592) / [DOI](https://doi.org/10.3386/w20592), 2014; published version [DOI](https://doi.org/10.1093/rfs/hhv059) | The authors document extensive factor mining. Their stated estimate is that a newly discovered factor should clear a t-ratio above 3.0, rather than the customary 2.0; their framework accounts for correlation and missing tests. | This is an asset-pricing-factor study, not a universal 3.0 threshold for every strategy. The required hurdle depends on the effective number and dependence of tests, economic prior, and unobserved failed trials. |
| Bailey and Lopez de Prado, [*The Deflated Sharpe Ratio*](https://doi.org/10.2139/ssrn.2460551), SSRN 2460551 | The source addresses selection bias, non-normal returns, and multiple trials when interpreting a selected Sharpe ratio. | It corrects an evaluation statistic under stated distributional inputs; it does not substitute for honest out-of-sample data or realistic trading simulation. |

### Application to `quant_research`

- Current walk-forward validation is the right direction: each fold locks a
  training-selected configuration before its test period. The reported
  all-negative MWU+convertible-bond OOS result must remain the decision input;
  a later rule that improves the same historical fold is another research
  trial, not independent confirmation.
- Preserve an append-only experiment ledger: data snapshot SHA, universe,
  costs, execution assumptions, all grid values, selection metric, fold
  boundaries, and the reason a new hypothesis was introduced. Count abandoned
  variants and post-hoc fixes as trials.
- The present four-combination MWU+convertible-bond grid is too small and too
  behaviorally similar for a persuasive PBO/CSCV estimate. The existing
  conclusion to defer PBO until a sufficiently broad, predeclared matrix is
  defensible. Do not replace it with a PBO number manufactured from four
  correlated rows.
- Keep one terminal holdout period and do not use it to choose a pool-size,
  stop-loss, price-limit, or expert-weight parameter. Treat a result on that
  period as a final audit, not an optimizer objective.

## 2. Transaction Costs and Turnover

### Primary source

Novy-Marx and Velikov, [*A Taxonomy of Anomalies and their Trading Costs*](https://www.nber.org/papers/w20721), NBER Working Paper 20721 (2014), [DOI](https://doi.org/10.3386/w20721); published in *Review of Financial Studies* as [*A Taxonomy of Anomalies and Their Trading Costs*](https://doi.org/10.1093/rfs/hhv063), 2016.

The NBER abstract reports three directly relevant results: transaction costs
reduce profitability and statistical significance; a buy/hold spread is the
most effective simple mitigation they examine; and, after mitigation, most
anomalies with one-sided monthly turnover below 50% retain statistically
significant net spreads while few higher-turnover strategies do. The latter is
an empirical finding for the paper's U.S. anomaly sample, not a universal
turnover limit for China or for a personal account.

### Application to `quant_research`

- Report *gross and net* return, buy/sell notional, paid commissions, rejected
  or margin orders, and turnover together. The current order-level analyzer
  already exposes these fields, which is materially better than treating a
  closed-trade count as turnover.
- A 10,000 CNY account is disproportionately affected by per-order minimum
  commissions and integer lots. The break-even turnover is account- and
  broker-specific; it cannot be inferred from a percentage-only cost model.
- Do not use the paper's U.S. empirical costs as a China cost calibration.
  Calibrate bid/ask, market impact, commissions, and product fees from the
  actual broker schedule and, where available, order-level historical data.
- The current MWU implementation updates expert weights from raw selected
  closing-price returns. An expert that turns over more can therefore receive
  the same learning credit as an otherwise identical lower-turnover expert,
  even when its net implementation return is worse. This is a model mismatch
  that must be measured before any live-use decision.

## 3. Online Experts and MWU

### Primary sources

| Source | Verified conclusion | Boundary |
| --- | --- | --- |
| Arora, Hazan, Kale, [*The Multiplicative Weights Update Method: a Meta-Algorithm and Applications*](https://theoryofcomputing.org/articles/v008a006/), *Theory of Computing* 8, 2012, [PDF](https://theoryofcomputing.org/articles/v008a006/v008a006.pdf), [DOI](https://doi.org/10.4086/toc.2012.v008a006) | In the expert setting, the algorithm maintains a distribution over decisions and updates weights multiplicatively. Its stated general bound compares total expected loss with each fixed decision when losses are bounded in `[-1, 1]` and `eta <= 1/2`. | The comparator is the best fixed expert in hindsight, not a switching oracle. The theorem requires the loss sequence and loss range used in the proof; it says nothing about bid/ask spreads, fees, market impact, stale prices, or partial execution. |
| Freund and Schapire, [*A Decision-Theoretic Generalization of On-Line Learning and an Application to Boosting*](https://doi.org/10.1006/jcss.1997.1504), *Journal of Computer and System Sciences* 55(1), 1997 | Establishes the expert-advice/online-learning setting underlying multiplicative-weight style guarantees. | It is an online-learning result, not an asset-management performance theorem. |
| Cover, [*Universal Portfolios*](https://doi.org/10.1111/j.1467-9965.1991.tb00002.x), *Mathematical Finance* 1(1), 1991 | Provides a financial-market online-portfolio benchmark, which is useful for separating an online-allocation result from a predictive-alpha claim. | Its asymptotic comparison has its own market and friction assumptions; it cannot be used to waive transaction costs or market-specific settlement rules. |

### Consequences for the current implementation

- `ETFMWU._update_weights()` maps a selected expert's prior closing-price
  return to `clip(-return / loss_scale, 0, 1)`. This meets the bounded-loss
  shape only after that explicit clipping, but it is not the source theorem's
  general cost sequence: positive returns receive zero loss rather than a
  symmetric reward, and realized costs are absent.
- `share_alpha` Fixed Share mixing is an additional adaptation mechanism. The
  cited MWU survey does not validate the particular combination of one-sided
  loss clipping, numerical weight floor, and Fixed Share value in this project.
  Its value must be tested with predeclared alternatives and untouched OOS
  folds, not inferred from the generic regret theorem.
- Define the expert feedback unit before claiming any guarantee: decision time,
  executable price, fees, bid/ask or a conservative slippage proxy, tax,
  quantity rounding, and the return clock. A close-to-close paper return is
  acceptable only as a diagnostic signal, not as evidence of executable net
  performance.

## 4. A-Share Rules, Price Limits, and Settlement

### Confirmed official sources

1. SSE's [official business-rules overview](https://www.sse.com.cn/lawandrules/sselawsrules2025/overview/)
   and its linked [official current-rules catalogue PDF](https://www.sse.com.cn/lawandrules/sselawsrules2025/overview/c/10778726/files/ddfc82e93a85496bb075175d9a8d811d.pdf)
   list `Shanghai Stock Exchange Trading Rules (2023 Revision)` as a current
   basic business rule, issued 2023-02-17 as SSE Notice `[2023] No. 32`.
2. The State Council/Government of the PRC publishes the Ministry of Finance and
   State Taxation Administration announcement, [*Announcement on Halving the
   Securities Transaction Stamp Duty*](https://www.gov.cn/zhengce/202308/content_6900867.htm).
   It states that the securities transaction stamp duty is halved from
   2023-08-28. For an ordinary A-share sale, the commonly modelled post-change
   rate is 0.05%; verify product scope and the broker bill before using it for
   any other instrument.

### Confirmed project impact

- `BacktestEngine` installs one `TPlus1Broker` for every feed. The strategy
  universe includes ETFs and convertible bonds as well as A shares. Settlement
  and same-day-turnover eligibility must therefore be a per-instrument rule,
  not a global broker switch. Applying a stock T+1 restriction to an eligible
  T+0 product can understate executable turnover; failing to apply it to a
  stock can overstate liquidity.
- `get_limit_pct()` uses only code-prefix buckets: main board 10%, ChiNext and
  STAR 20%, and BSE 30%. This is insufficient as a production rule table: it
  has no effective dates, ST/*ST treatment, listing-day exceptions, suspension
  state, or product-specific rules. It also cannot distinguish an instrument
  solely from a code prefix in every case.
- `AShareCommission.stamp_duty = 0.0005` is directionally consistent with the
  2023 tax change for an eligible A-share sale. Its use must remain
  product-scoped; ETFs and convertible bonds must not inherit an A-share tax
  model simply because they share the same backtest engine.
- The `min_commission = 5.0` assumption is not an exchange rule. It must be
  configured from the user's actual broker agreement and product tariff.
  Likewise, transfer-fee and exchange-fee treatment should be checked against
  the current ChinaClear/exchange/broker schedule rather than treated as one
  constant across stocks, ETFs, and convertible bonds.

### Open official-rule gap

The SSE PDFs retrieved in this investigation are official catalogues: they
confirm the current rule title, issuer, date, and notice number, but do not
contain the operative text of the 2023 Trading Rules. The static SSE list for
that legacy rule did not return the rule attachment in this environment. No
unverified secondary quotation is used to fill that gap.

Before changing execution assumptions, obtain and version the full operative
text of SSE Notice `[2023] No. 32` and the corresponding SZSE/BSE rule for each
instrument traded. Record the exact article, effective date, applicable
security type, T+0/T+1 eligibility, price-limit percentage/exceptions, tick,
lot, and order type. This note is research evidence, not a regulatory or tax
opinion.

## 5. Minimum Research Gate for a Small Personal Account

Use the following as a gate, not as a scorecard to optimize against:

1. Freeze data, universe, corporate-action treatment, cost schedule, and
   execution timing before expanding a parameter search.
2. Retain every tried configuration and run selection strictly inside each
   training window; keep a final untouched period.
3. Reject a candidate if its net result is not robust to conservative
   product-specific costs, lot rounding, and adverse fills.
4. For MWU, compare raw-signal, cost-adjusted-signal, and portfolio-realized
   feedback on the same frozen folds. Do not interpret the regret theorem as a
   substitute for that comparison.
5. Keep the existing no-live-deployment conclusion until an independently
   frozen, net-of-cost OOS process meets a predeclared acceptance rule.

## Evidence and Limitations

- All academic claims above link to the original paper DOI, the author/institution
  working-paper page, or the journal's own publication page. The NBER and
  Theory of Computing pages/PDFs were retrieved directly in this investigation.
- The SSRN originals for PBO and Deflated Sharpe Ratio were resolved through
  their DOI records, but the SSRN full-text host was unreachable from this
  environment. Their conclusions are therefore stated conservatively and
  should be rechecked against the PDFs before implementing a statistical test.
- The official SSE catalogue PDF was parsed successfully. The missing item is
  the full text/attachment of the current 2023 Trading Rules, not the ability
  to parse the downloaded catalogue.
