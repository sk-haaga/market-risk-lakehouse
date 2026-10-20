-- =============================================================================
-- SILVER: cleaned, typed and de-duplicated market data.
-- Each landing run re-sends a look-back window, so the same (series, date) can
-- arrive several times; the most recent ingestion wins (absorbs source revisions).
-- =============================================================================

CREATE OR REFRESH MATERIALIZED VIEW silver_fx_rates (
  currency        STRING    COMMENT 'Quoted currency (ISO 4217)',
  rate_date       DATE      COMMENT 'ECB reference rate date',
  fx_rate         DOUBLE    COMMENT 'Units of currency per 1 EUR',
  base_currency   STRING    COMMENT 'Always EUR',
  ingested_at     TIMESTAMP COMMENT 'Ingestion time of the winning (latest) record',
  source_file     STRING    COMMENT 'Landing file of the winning record',
  CONSTRAINT fx_rate_positive      EXPECT (fx_rate > 0)                              ON VIOLATION DROP ROW,
  CONSTRAINT fx_currency_iso       EXPECT (currency RLIKE '^[A-Z]{3}$')              ON VIOLATION DROP ROW,
  CONSTRAINT fx_not_eur            EXPECT (currency <> 'EUR')                        ON VIOLATION DROP ROW,
  CONSTRAINT fx_date_not_future    EXPECT (rate_date <= current_date())              ON VIOLATION DROP ROW,
  CONSTRAINT fx_date_is_weekday    EXPECT (dayofweek(rate_date) NOT IN (1, 7)),
  CONSTRAINT fx_rate_plausible     EXPECT (fx_rate BETWEEN 0.0001 AND 100000)
)
COMMENT 'Daily ECB reference rates, one row per (currency, rate_date). Lineage: bronze_ecb_fx_rates.'
TBLPROPERTIES ('quality' = 'silver')
AS
WITH ranked AS (
  SELECT
    upper(currency) AS currency,
    obs_date        AS rate_date,
    value           AS fx_rate,
    ingested_at,
    source_file,
    row_number() OVER (
      PARTITION BY upper(currency), obs_date
      ORDER BY ingested_at DESC, source_file DESC
    ) AS rn
  FROM bronze_ecb_fx_rates
  WHERE value IS NOT NULL
    AND obs_date IS NOT NULL
    AND currency IS NOT NULL
)
SELECT currency, rate_date, fx_rate, 'EUR' AS base_currency, ingested_at, source_file
FROM ranked
WHERE rn = 1;


CREATE OR REFRESH MATERIALIZED VIEW silver_index_prices (
  risk_factor_id    STRING    COMMENT 'Risk factor identifier (FK silver_risk_factors)',
  series_id         STRING    COMMENT 'FRED series id',
  quote_currency    STRING    COMMENT 'Index quote currency',
  price_date        DATE      COMMENT 'Trading date',
  close_price       DOUBLE    COMMENT 'Index closing level in quote currency',
  prev_close_price  DOUBLE    COMMENT 'Previous available close (for move plausibility checks)',
  ingested_at       TIMESTAMP COMMENT 'Ingestion time of the winning (latest) record',
  source_file       STRING    COMMENT 'Landing file of the winning record',
  -- series id AND quote currency must match the catalogue, otherwise risk_factor_id is null
  CONSTRAINT px_mapped_to_risk_factor EXPECT (risk_factor_id IS NOT NULL)                         ON VIOLATION DROP ROW,
  CONSTRAINT px_close_positive      EXPECT (close_price > 0)                                      ON VIOLATION DROP ROW,
  CONSTRAINT px_date_not_future     EXPECT (price_date <= current_date())                         ON VIOLATION DROP ROW,
  CONSTRAINT px_date_is_weekday     EXPECT (dayofweek(price_date) NOT IN (1, 7)),
  CONSTRAINT px_plausible_daily_move EXPECT (prev_close_price IS NULL OR abs(close_price / prev_close_price - 1) < 0.25)
)
COMMENT 'Daily equity index closes, one row per (risk_factor_id, price_date); holidays removed. Lineage: bronze_index_prices + silver_risk_factors.'
TBLPROPERTIES ('quality' = 'silver')
AS
WITH ranked AS (
  SELECT
    series_id,
    upper(currency) AS quote_currency,
    obs_date        AS price_date,
    value           AS close_price,
    ingested_at,
    source_file,
    row_number() OVER (
      PARTITION BY series_id, upper(currency), obs_date
      ORDER BY ingested_at DESC, source_file DESC
    ) AS rn
  FROM bronze_index_prices
  WHERE value IS NOT NULL
    AND obs_date IS NOT NULL
),
latest AS (
  SELECT * FROM ranked WHERE rn = 1
),
mapped AS (
  SELECT
    rf.risk_factor_id,
    l.series_id,
    l.quote_currency,
    l.price_date,
    l.close_price,
    lag(l.close_price) OVER (PARTITION BY l.series_id, l.quote_currency ORDER BY l.price_date) AS prev_close_price,
    l.ingested_at,
    l.source_file
  FROM latest AS l
  LEFT JOIN silver_risk_factors AS rf
    ON  rf.source = 'FRED'
    AND rf.source_series_id = l.series_id
    AND rf.quote_currency = l.quote_currency
)
SELECT
  risk_factor_id, series_id, quote_currency, price_date, close_price,
  prev_close_price, ingested_at, source_file
FROM mapped;
