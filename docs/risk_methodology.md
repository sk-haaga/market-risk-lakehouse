# Risk methodology

## Portfolio and P&L

The demo portfolio (`src/market_risk/reference/portfolio_positions.csv`) is a EUR-based global
macro book:

- long positions in four equity indices: S&P 500, NASDAQ Composite, Dow Jones and Nikkei 225;
- FX cash balances, long USD and CHF, short GBP and JPY (funding).

Every position maps to one **risk factor** priced in EUR:

| Risk factor | EUR price |
|---|---|
| Equity index (`EQ_*`) | index close in local currency / ECB rate (currency per EUR) |
| Currency (`FX_*`) | 1 / ECB rate, the EUR value of one unit of currency |

An S&P 500 position therefore carries both equity risk and USD/EUR risk, as it would for a euro
investor. Positions are static, so the daily **hypothetical P&L** is
`quantity x (EUR price today - EUR price yesterday)`. This is the P&L that regulatory
backtesting compares VaR against: it is free of intraday trading, fees and new positions.

### Calendar alignment

ECB rates follow the TARGET calendar, US indices the NYSE calendar and the Nikkei the Tokyo
calendar. Prices are put on a common **weekday grid** with an as-of (last observation carried
forward) join. A carried-forward price is flagged (`is_filled`). Fills older than 10 calendar
days are dropped rather than turned into artificial zero returns.

## Value at Risk and Expected Shortfall

1-day horizon, EUR, reported as positive loss amounts, at 99% and 97.5% confidence, using a
rolling **250 trading day** window ending on the as-of date. Exposures are the EUR market values at
the as-of date. Scenario P&L is `sum_i exposure_i x return_i`.

| Method | VaR | ES |
|---|---|---|
| **Historical simulation** | k-th worst of the 250 scenario losses, with k = ceil(250 x (1 - a)) (3rd worst at 99%) | mean of the k worst losses |
| **Parametric (normal)** | z_a x sigma, sigma from the equally weighted sample covariance | sigma x phi(z_a) / (1 - a) |
| **Parametric (EWMA)** | as above, with a RiskMetrics EWMA covariance (lambda = 0.94) | as above |

Historical simulation makes no distributional assumption but reacts slowly to new volatility.
The normal model is smooth but tends to understate fat tails. EWMA reacts fastest to volatility
clusters. Running all three side by side is what makes the backtest informative.

### Risk contributions

- Historical: **ES contribution** of factor i = average loss from factor i across the k tail scenarios.
- Parametric: **Euler VaR contribution** = z x e_i x (Sigma e)_i / sigma.

Both sum exactly to the portfolio figure, and this is enforced by a data quality check.

## Backtesting

A forecast made at the close of day t is compared with the hypothetical P&L of day t+1. A
**breach** (exception) is a loss larger than VaR.

| Test | Question | Statistic |
|---|---|---|
| Kupiec POF | Is the breach rate equal to 1 - a? | LR ~ chi2(1) |
| Christoffersen independence | Do breaches cluster? | LR ~ chi2(1) on the breach transition matrix |
| Conditional coverage | Both at once | LR_pof + LR_ind ~ chi2(2) |
| Basel traffic light | How many breaches in the last 250 days? | green < 95%, yellow < 99.99%, red otherwise (cumulative binomial). At 99%: green 0-4, yellow 5-9, red 10+ |
| ES ratio | Does ES describe the tail? | mean loss on breach days / mean ES forecast on those days (about 1 is good) |

A model is marked **ACCEPT** when both the Kupiec and the conditional coverage tests pass at the
5% level. The traffic light is defined by Basel for 99% VaR; the same binomial rule is applied to
97.5% for comparison.

## Limitations

- Linear, static positions only: no options, no intraday trading, no carry or dividends
  (price indices, not total return).
- 1-day horizon; no scaling to 10 days.
- FRED licenses some index series for limited history (about 10 years for S&P 500 and Dow Jones),
  which bounds the backtest length.
- The ES "backtest" is a diagnostic ratio, not a formal test such as Acerbi-Szekely.
