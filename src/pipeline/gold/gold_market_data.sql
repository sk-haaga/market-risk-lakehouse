-- =============================================================================
-- GOLD: market data converted to EUR on a common weekday grid, plus returns.
--
-- ECB fixes on TARGET days, FRED indices on their own exchange calendars. To value
-- a EUR portfolio on one grid, every weekday gets the last available local close
-- and the last available ECB rate (as-of / forward fill). Fills older than 10
-- calendar days are dropped, so long outages never masquerade as zero returns.
-- =============================================================================

CREATE OR REFRESH MATERIALIZED VIEW gold_fact_market_prices_eur (
  price_date            DATE    COMMENT 'Valuation date (weekday grid)',
  risk_factor_id        STRING  COMMENT 'Risk factor (FK gold_dim_risk_factor)',
  asset_class           STRING  COMMENT 'EQUITY_INDEX or FX',
  quote_currency        STRING  COMMENT 'Currency of the local price',
  local_price           DOUBLE  COMMENT 'Index level in quote currency; 1.0 for FX factors (one unit of currency)',
  fx_rate               DOUBLE  COMMENT 'ECB rate used: units of quote currency per 1 EUR (1.0 for EUR)',
  price_eur             DOUBLE  COMMENT 'Price in EUR = local_price / fx_rate',
  local_price_age_days  INT     COMMENT 'Calendar days since the local close was observed (0 = same day)',
  fx_rate_age_days      INT     COMMENT 'Calendar days since the ECB rate was observed (0 = same day)',
  is_filled             BOOLEAN COMMENT 'True if the local price or the FX rate was carried forward',
  CONSTRAINT mkt_price_eur_positive    EXPECT (price_eur > 0)                ON VIOLATION FAIL UPDATE,
  CONSTRAINT mkt_local_price_not_stale EXPECT (local_price_age_days <= 10)   ON VIOLATION DROP ROW,
  CONSTRAINT mkt_fx_rate_not_stale     EXPECT (fx_rate_age_days <= 10)       ON VIOLATION DROP ROW,
  CONSTRAINT mkt_weekday_grid          EXPECT (dayofweek(price_date) NOT IN (1, 7)) ON VIOLATION FAIL UPDATE,
  CONSTRAINT mkt_fill_within_week      EXPECT (local_price_age_days <= 5 AND fx_rate_age_days <= 5)
)
COMMENT 'Daily EUR prices per risk factor on a weekday grid with as-of filled local closes and ECB rates. Lineage: silver_index_prices + silver_fx_rates + silver_risk_factors + gold_dim_date.'
TBLPROPERTIES ('quality' = 'gold')
AS
WITH weekdays AS (
  SELECT calendar_date AS price_date FROM gold_dim_date WHERE NOT is_weekend
),
grid AS (
  SELECT w.price_date, rf.risk_factor_id, rf.asset_class, rf.quote_currency
  FROM weekdays AS w
  CROSS JOIN silver_risk_factors AS rf
),
observed AS (
  SELECT
    g.price_date,
    g.risk_factor_id,
    g.asset_class,
    g.quote_currency,
    CASE WHEN g.asset_class = 'FX' THEN 1.0 ELSE px.close_price END       AS local_price,
    CASE WHEN g.asset_class = 'FX' THEN g.price_date ELSE px.price_date END AS local_obs_date,
    CASE WHEN g.quote_currency = 'EUR' THEN 1.0 ELSE fx.fx_rate END        AS fx_rate,
    CASE WHEN g.quote_currency = 'EUR' THEN g.price_date ELSE fx.rate_date END AS fx_obs_date
  FROM grid AS g
  LEFT JOIN silver_index_prices AS px
    ON px.risk_factor_id = g.risk_factor_id AND px.price_date = g.price_date
  LEFT JOIN silver_fx_rates AS fx
    ON fx.currency = g.quote_currency AND fx.rate_date = g.price_date
),
filled AS (
  SELECT
    price_date,
    risk_factor_id,
    asset_class,
    quote_currency,
    last_value(local_price, true)    OVER w AS local_price,
    last_value(local_obs_date, true) OVER w AS local_obs_date,
    last_value(fx_rate, true)        OVER w AS fx_rate,
    last_value(fx_obs_date, true)    OVER w AS fx_obs_date
  FROM observed
  WINDOW w AS (
    PARTITION BY risk_factor_id ORDER BY price_date
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
  )
)
SELECT
  price_date,
  risk_factor_id,
  asset_class,
  quote_currency,
  local_price,
  fx_rate,
  local_price / fx_rate                          AS price_eur,
  datediff(price_date, local_obs_date)           AS local_price_age_days,
  datediff(price_date, fx_obs_date)              AS fx_rate_age_days,
  (local_obs_date < price_date OR fx_obs_date < price_date) AS is_filled
FROM filled
WHERE local_price IS NOT NULL
  AND fx_rate IS NOT NULL;


CREATE OR REFRESH MATERIALIZED VIEW gold_fact_risk_factor_returns (
  price_date        DATE   COMMENT 'Return end date',
  risk_factor_id    STRING COMMENT 'Risk factor (FK gold_dim_risk_factor)',
  prev_price_date   DATE   COMMENT 'Return start date (previous available grid date)',
  price_eur         DOUBLE COMMENT 'EUR price at price_date',
  prev_price_eur    DOUBLE COMMENT 'EUR price at prev_price_date',
  simple_return     DOUBLE COMMENT 'price_eur / prev_price_eur - 1',
  log_return        DOUBLE COMMENT 'ln(price_eur / prev_price_eur)',
  days_since_prev   INT    COMMENT 'Calendar days between prev_price_date and price_date',
  CONSTRAINT ret_not_null           EXPECT (simple_return IS NOT NULL AND log_return IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT ret_within_hard_limit  EXPECT (abs(log_return) < 0.5)   ON VIOLATION FAIL UPDATE,
  CONSTRAINT ret_within_soft_limit  EXPECT (abs(log_return) < 0.15),
  CONSTRAINT ret_gap_reasonable     EXPECT (days_since_prev <= 5)
)
COMMENT 'Daily EUR returns per risk factor; the scenario set for historical VaR. Lineage: gold_fact_market_prices_eur.'
TBLPROPERTIES ('quality' = 'gold')
AS
WITH lagged AS (
  SELECT
    price_date,
    risk_factor_id,
    price_eur,
    lag(price_date) OVER (PARTITION BY risk_factor_id ORDER BY price_date) AS prev_price_date,
    lag(price_eur)  OVER (PARTITION BY risk_factor_id ORDER BY price_date) AS prev_price_eur
  FROM gold_fact_market_prices_eur
)
SELECT
  price_date,
  risk_factor_id,
  prev_price_date,
  price_eur,
  prev_price_eur,
  price_eur / prev_price_eur - 1      AS simple_return,
  ln(price_eur / prev_price_eur)      AS log_return,
  datediff(price_date, prev_price_date) AS days_since_prev
FROM lagged
WHERE prev_price_eur IS NOT NULL;
