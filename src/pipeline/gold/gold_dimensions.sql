-- =============================================================================
-- GOLD: conformed dimensions for the Power BI star schema.
-- =============================================================================

CREATE OR REFRESH MATERIALIZED VIEW gold_dim_date (
  calendar_date       DATE    COMMENT 'Calendar date (primary key)',
  year                INT     COMMENT 'Calendar year',
  quarter             INT     COMMENT 'Calendar quarter 1-4',
  month               INT     COMMENT 'Month number 1-12',
  month_name          STRING  COMMENT 'Short month name, e.g. Oct',
  year_month          STRING  COMMENT 'yyyy-MM, sortable month label',
  day_of_week         INT     COMMENT 'ISO day of week, Monday = 1 ... Sunday = 7',
  day_name            STRING  COMMENT 'Short day name, e.g. Mon',
  is_weekend          BOOLEAN COMMENT 'Saturday or Sunday',
  is_ecb_fixing_day   BOOLEAN COMMENT 'ECB published reference rates on this date (TARGET business day)',
  is_us_trading_day   BOOLEAN COMMENT 'At least one USD equity index has a close on this date',
  CONSTRAINT dim_date_not_null        EXPECT (calendar_date IS NOT NULL)          ON VIOLATION FAIL UPDATE,
  CONSTRAINT dim_date_iso_dow_valid   EXPECT (day_of_week BETWEEN 1 AND 7)        ON VIOLATION FAIL UPDATE,
  CONSTRAINT dim_date_no_weekend_fix  EXPECT (NOT (is_weekend AND is_ecb_fixing_day))
)
COMMENT 'Calendar dimension spanning all market data dates. Lineage: silver_fx_rates + silver_index_prices.'
TBLPROPERTIES ('quality' = 'gold')
AS
WITH all_dates AS (
  SELECT rate_date AS d FROM silver_fx_rates
  UNION ALL
  SELECT price_date AS d FROM silver_index_prices
),
bounds AS (
  SELECT min(d) AS d0, max(d) AS d1 FROM all_dates
),
spine AS (
  SELECT explode(sequence(d0, d1, INTERVAL 1 DAY)) AS calendar_date FROM bounds
),
ecb_days AS (
  SELECT DISTINCT rate_date AS d FROM silver_fx_rates
),
us_days AS (
  SELECT DISTINCT price_date AS d FROM silver_index_prices WHERE quote_currency = 'USD'
)
SELECT
  s.calendar_date,
  year(s.calendar_date)                              AS year,
  quarter(s.calendar_date)                           AS quarter,
  month(s.calendar_date)                             AS month,
  date_format(s.calendar_date, 'MMM')                AS month_name,
  date_format(s.calendar_date, 'yyyy-MM')            AS year_month,
  ((dayofweek(s.calendar_date) + 5) % 7) + 1         AS day_of_week,
  date_format(s.calendar_date, 'E')                  AS day_name,
  dayofweek(s.calendar_date) IN (1, 7)               AS is_weekend,
  e.d IS NOT NULL                                    AS is_ecb_fixing_day,
  u.d IS NOT NULL                                    AS is_us_trading_day
FROM spine AS s
LEFT JOIN ecb_days AS e ON e.d = s.calendar_date
LEFT JOIN us_days  AS u ON u.d = s.calendar_date;


CREATE OR REFRESH MATERIALIZED VIEW gold_dim_risk_factor (
  risk_factor_id     STRING COMMENT 'Risk factor identifier (primary key)',
  risk_factor_name   STRING COMMENT 'Display name',
  asset_class        STRING COMMENT 'EQUITY_INDEX or FX',
  quote_currency     STRING COMMENT 'Currency the factor is quoted in',
  source             STRING COMMENT 'Market data provider (ECB or FRED)',
  source_series_id   STRING COMMENT 'Series id at the provider',
  description        STRING COMMENT 'Free-text description',
  first_price_date   DATE   COMMENT 'First date with a EUR price in gold_fact_market_prices_eur',
  last_price_date    DATE   COMMENT 'Latest date with a EUR price in gold_fact_market_prices_eur',
  CONSTRAINT dim_rf_id_not_null  EXPECT (risk_factor_id IS NOT NULL) ON VIOLATION FAIL UPDATE,
  CONSTRAINT dim_rf_has_prices   EXPECT (last_price_date IS NOT NULL)
)
COMMENT 'Risk factor dimension with market data coverage. Lineage: silver_risk_factors + gold_fact_market_prices_eur.'
TBLPROPERTIES ('quality' = 'gold')
AS
WITH coverage AS (
  SELECT risk_factor_id, min(price_date) AS first_price_date, max(price_date) AS last_price_date
  FROM gold_fact_market_prices_eur
  GROUP BY risk_factor_id
)
SELECT
  rf.risk_factor_id, rf.risk_factor_name, rf.asset_class, rf.quote_currency,
  rf.source, rf.source_series_id, rf.description,
  c.first_price_date, c.last_price_date
FROM silver_risk_factors AS rf
LEFT JOIN coverage AS c ON c.risk_factor_id = rf.risk_factor_id;


CREATE OR REFRESH MATERIALIZED VIEW gold_dim_position (
  position_id        STRING COMMENT 'Position identifier (primary key)',
  portfolio_id       STRING COMMENT 'Portfolio the position belongs to',
  book               STRING COMMENT 'Trading book (EQUITY or FX)',
  instrument_type    STRING COMMENT 'EQUITY_INDEX or FX_CASH',
  risk_factor_id     STRING COMMENT 'Risk factor (FK gold_dim_risk_factor)',
  risk_factor_name   STRING COMMENT 'Risk factor display name',
  asset_class        STRING COMMENT 'Asset class of the risk factor',
  quote_currency     STRING COMMENT 'Currency of the risk factor',
  quantity           DOUBLE COMMENT 'Index units or currency units; negative = short',
  quantity_unit      STRING COMMENT 'index_units or currency_units',
  direction          STRING COMMENT 'LONG or SHORT',
  description        STRING COMMENT 'Free-text description',
  CONSTRAINT dim_pos_id_not_null   EXPECT (position_id IS NOT NULL)       ON VIOLATION FAIL UPDATE,
  CONSTRAINT dim_pos_direction_set EXPECT (direction IN ('LONG', 'SHORT')) ON VIOLATION FAIL UPDATE
)
COMMENT 'Position dimension for slicing exposures and P&L. Lineage: silver_positions + silver_risk_factors.'
TBLPROPERTIES ('quality' = 'gold')
AS SELECT
  p.position_id, p.portfolio_id, p.book, p.instrument_type, p.risk_factor_id,
  rf.risk_factor_name, p.asset_class, p.quote_currency, p.quantity, p.quantity_unit,
  CASE WHEN p.quantity >= 0 THEN 'LONG' ELSE 'SHORT' END AS direction,
  p.description
FROM silver_positions AS p
LEFT JOIN silver_risk_factors AS rf ON rf.risk_factor_id = p.risk_factor_id;
