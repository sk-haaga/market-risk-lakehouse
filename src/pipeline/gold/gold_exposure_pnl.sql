-- =============================================================================
-- GOLD: position exposures and hypothetical P&L in EUR.
-- Positions are static, so the daily P&L is the "hypothetical" P&L used for VaR
-- backtesting: yesterday's positions revalued at today's prices.
-- =============================================================================

CREATE OR REFRESH MATERIALIZED VIEW gold_fact_position_exposure_daily (
  as_of_date       DATE   COMMENT 'Valuation date',
  position_id      STRING COMMENT 'Position (FK gold_dim_position)',
  portfolio_id     STRING COMMENT 'Portfolio',
  risk_factor_id   STRING COMMENT 'Risk factor (FK gold_dim_risk_factor)',
  quote_currency   STRING COMMENT 'Currency of the risk factor',
  quantity         DOUBLE COMMENT 'Index units or currency units; negative = short',
  local_price      DOUBLE COMMENT 'Price in quote currency',
  fx_rate          DOUBLE COMMENT 'Units of quote currency per 1 EUR',
  price_eur        DOUBLE COMMENT 'Price in EUR',
  exposure_local   DOUBLE COMMENT 'quantity * local_price, in quote currency',
  exposure_eur     DOUBLE COMMENT 'quantity * price_eur, market value in EUR',
  is_filled        BOOLEAN COMMENT 'True if a carried-forward price was used',
  CONSTRAINT exp_eur_not_null      EXPECT (exposure_eur IS NOT NULL)                         ON VIOLATION FAIL UPDATE,
  CONSTRAINT exp_sign_matches_qty  EXPECT (sign(exposure_eur) = sign(quantity))              ON VIOLATION FAIL UPDATE,
  CONSTRAINT exp_eur_consistent    EXPECT (abs(exposure_eur - exposure_local / fx_rate) < 0.01) ON VIOLATION FAIL UPDATE
)
COMMENT 'Daily market value of every position in EUR. Lineage: silver_positions + gold_fact_market_prices_eur.'
TBLPROPERTIES ('quality' = 'gold')
AS SELECT
  m.price_date                AS as_of_date,
  p.position_id,
  p.portfolio_id,
  p.risk_factor_id,
  m.quote_currency,
  p.quantity,
  m.local_price,
  m.fx_rate,
  m.price_eur,
  p.quantity * m.local_price  AS exposure_local,
  p.quantity * m.price_eur    AS exposure_eur,
  m.is_filled
FROM silver_positions AS p
JOIN gold_fact_market_prices_eur AS m
  ON m.risk_factor_id = p.risk_factor_id;


CREATE OR REFRESH MATERIALIZED VIEW gold_fact_position_pnl_daily (
  pnl_date            DATE   COMMENT 'P&L date (end of the return period)',
  prev_date           DATE   COMMENT 'Start of the return period',
  position_id         STRING COMMENT 'Position (FK gold_dim_position)',
  portfolio_id        STRING COMMENT 'Portfolio',
  risk_factor_id      STRING COMMENT 'Risk factor (FK gold_dim_risk_factor)',
  exposure_prev_eur   DOUBLE COMMENT 'EUR market value at prev_date',
  simple_return       DOUBLE COMMENT 'EUR return of the risk factor over the period',
  pnl_eur             DOUBLE COMMENT 'Hypothetical P&L = quantity * (price_eur - prev_price_eur)',
  CONSTRAINT ppnl_not_null         EXPECT (pnl_eur IS NOT NULL)                                       ON VIOLATION FAIL UPDATE,
  CONSTRAINT ppnl_equals_exp_x_ret EXPECT (abs(pnl_eur - exposure_prev_eur * simple_return) < 0.01)   ON VIOLATION FAIL UPDATE
)
COMMENT 'Daily hypothetical P&L per position in EUR. Lineage: silver_positions + gold_fact_risk_factor_returns.'
TBLPROPERTIES ('quality' = 'gold')
AS SELECT
  r.price_date                                   AS pnl_date,
  r.prev_price_date                              AS prev_date,
  p.position_id,
  p.portfolio_id,
  p.risk_factor_id,
  p.quantity * r.prev_price_eur                  AS exposure_prev_eur,
  r.simple_return,
  p.quantity * (r.price_eur - r.prev_price_eur)  AS pnl_eur
FROM silver_positions AS p
JOIN gold_fact_risk_factor_returns AS r
  ON r.risk_factor_id = p.risk_factor_id;


CREATE OR REFRESH MATERIALIZED VIEW gold_fact_portfolio_pnl_daily (
  pnl_date                DATE   COMMENT 'P&L date',
  portfolio_id            STRING COMMENT 'Portfolio',
  portfolio_value_prev_eur DOUBLE COMMENT 'Portfolio market value at the start of the period',
  pnl_eur                 DOUBLE COMMENT 'Hypothetical portfolio P&L in EUR (sum over positions)',
  return_pct              DOUBLE COMMENT 'pnl_eur / gross exposure at start of period',
  n_positions             BIGINT COMMENT 'Positions contributing on this date',
  n_positions_expected    BIGINT COMMENT 'Positions in the portfolio',
  CONSTRAINT ppf_complete        EXPECT (n_positions = n_positions_expected) ON VIOLATION DROP ROW,
  CONSTRAINT ppf_pnl_not_null    EXPECT (pnl_eur IS NOT NULL)                ON VIOLATION FAIL UPDATE
)
COMMENT 'Daily hypothetical portfolio P&L in EUR; only dates where every position has a return. This is the realised series the VaR backtest compares against. Lineage: gold_fact_position_pnl_daily + silver_positions.'
TBLPROPERTIES ('quality' = 'gold')
AS
WITH expected AS (
  SELECT portfolio_id, count(*) AS n_positions_expected
  FROM silver_positions
  GROUP BY portfolio_id
),
agg AS (
  SELECT
    pnl_date,
    portfolio_id,
    sum(exposure_prev_eur)      AS portfolio_value_prev_eur,
    sum(abs(exposure_prev_eur)) AS gross_exposure_prev_eur,
    sum(pnl_eur)                AS pnl_eur,
    count(*)                    AS n_positions
  FROM gold_fact_position_pnl_daily
  GROUP BY pnl_date, portfolio_id
)
SELECT
  a.pnl_date,
  a.portfolio_id,
  a.portfolio_value_prev_eur,
  a.pnl_eur,
  a.pnl_eur / nullif(a.gross_exposure_prev_eur, 0) AS return_pct,
  a.n_positions,
  e.n_positions_expected
FROM agg AS a
JOIN expected AS e ON e.portfolio_id = a.portfolio_id;
