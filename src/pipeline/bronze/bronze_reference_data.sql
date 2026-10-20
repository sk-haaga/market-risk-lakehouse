-- =============================================================================
-- BRONZE: reference data (risk factor catalogue and the demo portfolio).
-- Small, fully re-read on every refresh, so these are materialized views.
-- =============================================================================

CREATE OR REFRESH MATERIALIZED VIEW bronze_risk_factors (
  risk_factor_id    STRING COMMENT 'Risk factor identifier, e.g. EQ_SPX or FX_USD',
  risk_factor_name  STRING COMMENT 'Display name',
  asset_class       STRING COMMENT 'EQUITY_INDEX or FX',
  source            STRING COMMENT 'Market data provider (ECB or FRED)',
  source_series_id  STRING COMMENT 'Series id at the provider (FRED series id or ECB currency)',
  quote_currency    STRING COMMENT 'Currency the factor is quoted in',
  description       STRING COMMENT 'Free-text description',
  source_file       STRING COMMENT 'Landing file the row was read from',
  CONSTRAINT rf_id_present EXPECT (risk_factor_id IS NOT NULL)
)
COMMENT 'Raw risk factor catalogue as landed from src/market_risk/reference/risk_factors.csv.'
TBLPROPERTIES ('quality' = 'bronze', 'source' = 'repository reference data')
AS SELECT
  risk_factor_id, risk_factor_name, asset_class, source, source_series_id, quote_currency, description,
  _metadata.file_path AS source_file
FROM read_files(
  '${landing_root}/reference/risk_factors/',
  format => 'csv',
  header => true,
  schema => 'risk_factor_id STRING, risk_factor_name STRING, asset_class STRING, source STRING, source_series_id STRING, quote_currency STRING, description STRING'
);


CREATE OR REFRESH MATERIALIZED VIEW bronze_positions (
  position_id      STRING COMMENT 'Position identifier',
  portfolio_id     STRING COMMENT 'Portfolio the position belongs to',
  book             STRING COMMENT 'Trading book (EQUITY or FX)',
  instrument_type  STRING COMMENT 'EQUITY_INDEX or FX_CASH',
  risk_factor_id   STRING COMMENT 'Risk factor the position is exposed to',
  quantity         DOUBLE COMMENT 'Index units (multiplier 1) or currency units; negative = short',
  quantity_unit    STRING COMMENT 'index_units or currency_units',
  description      STRING COMMENT 'Free-text description',
  source_file      STRING COMMENT 'Landing file the row was read from',
  CONSTRAINT pos_id_present EXPECT (position_id IS NOT NULL)
)
COMMENT 'Raw demo portfolio positions as landed from src/market_risk/reference/portfolio_positions.csv.'
TBLPROPERTIES ('quality' = 'bronze', 'source' = 'repository reference data')
AS SELECT
  position_id, portfolio_id, book, instrument_type, risk_factor_id, quantity, quantity_unit, description,
  _metadata.file_path AS source_file
FROM read_files(
  '${landing_root}/reference/positions/',
  format => 'csv',
  header => true,
  schema => 'position_id STRING, portfolio_id STRING, book STRING, instrument_type STRING, risk_factor_id STRING, quantity DOUBLE, quantity_unit STRING, description STRING'
);
