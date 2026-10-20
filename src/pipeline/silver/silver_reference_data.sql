-- =============================================================================
-- SILVER: conformed reference data.
-- Broken reference data would silently produce wrong risk numbers, so most
-- expectations here FAIL the update instead of dropping rows.
-- =============================================================================

CREATE OR REFRESH MATERIALIZED VIEW silver_risk_factors (
  risk_factor_id    STRING COMMENT 'Risk factor identifier (primary key)',
  risk_factor_name  STRING COMMENT 'Display name',
  asset_class       STRING COMMENT 'EQUITY_INDEX or FX',
  source            STRING COMMENT 'Market data provider (ECB or FRED)',
  source_series_id  STRING COMMENT 'Series id at the provider',
  quote_currency    STRING COMMENT 'Currency the factor is quoted in (ISO 4217)',
  description       STRING COMMENT 'Free-text description',
  CONSTRAINT rf_id_not_null        EXPECT (risk_factor_id IS NOT NULL)                         ON VIOLATION FAIL UPDATE,
  CONSTRAINT rf_asset_class_valid  EXPECT (asset_class IN ('EQUITY_INDEX', 'FX'))              ON VIOLATION FAIL UPDATE,
  CONSTRAINT rf_source_valid       EXPECT (source IN ('ECB', 'FRED'))                          ON VIOLATION FAIL UPDATE,
  CONSTRAINT rf_currency_iso       EXPECT (quote_currency RLIKE '^[A-Z]{3}$')                  ON VIOLATION FAIL UPDATE,
  CONSTRAINT rf_fx_from_ecb        EXPECT (asset_class <> 'FX' OR source = 'ECB')              ON VIOLATION FAIL UPDATE,
  CONSTRAINT rf_fx_series_is_ccy   EXPECT (asset_class <> 'FX' OR source_series_id = quote_currency) ON VIOLATION FAIL UPDATE
)
COMMENT 'Risk factor catalogue: one row per market risk factor. Lineage: bronze_risk_factors.'
TBLPROPERTIES ('quality' = 'silver')
AS SELECT
  trim(risk_factor_id)          AS risk_factor_id,
  trim(risk_factor_name)        AS risk_factor_name,
  upper(trim(asset_class))      AS asset_class,
  upper(trim(source))           AS source,
  trim(source_series_id)        AS source_series_id,
  upper(trim(quote_currency))   AS quote_currency,
  description
FROM bronze_risk_factors;


CREATE OR REFRESH MATERIALIZED VIEW silver_positions (
  position_id      STRING COMMENT 'Position identifier (primary key)',
  portfolio_id     STRING COMMENT 'Portfolio the position belongs to',
  book             STRING COMMENT 'Trading book (EQUITY or FX)',
  instrument_type  STRING COMMENT 'EQUITY_INDEX or FX_CASH',
  risk_factor_id   STRING COMMENT 'Risk factor the position is exposed to (FK silver_risk_factors)',
  asset_class      STRING COMMENT 'Asset class of the risk factor',
  quote_currency   STRING COMMENT 'Currency of the risk factor',
  quantity         DOUBLE COMMENT 'Index units (multiplier 1) or currency units; negative = short',
  quantity_unit    STRING COMMENT 'index_units or currency_units',
  description      STRING COMMENT 'Free-text description',
  CONSTRAINT pos_id_not_null           EXPECT (position_id IS NOT NULL)                       ON VIOLATION FAIL UPDATE,
  CONSTRAINT pos_portfolio_not_null    EXPECT (portfolio_id IS NOT NULL)                      ON VIOLATION FAIL UPDATE,
  CONSTRAINT pos_known_risk_factor     EXPECT (asset_class IS NOT NULL)                       ON VIOLATION FAIL UPDATE,
  CONSTRAINT pos_instrument_type_valid EXPECT (instrument_type IN ('EQUITY_INDEX', 'FX_CASH')) ON VIOLATION FAIL UPDATE,
  CONSTRAINT pos_instrument_matches_factor EXPECT (
    (instrument_type = 'EQUITY_INDEX' AND asset_class = 'EQUITY_INDEX')
    OR (instrument_type = 'FX_CASH' AND asset_class = 'FX')
  ) ON VIOLATION FAIL UPDATE,
  CONSTRAINT pos_quantity_non_zero     EXPECT (quantity IS NOT NULL AND quantity <> 0)        ON VIOLATION DROP ROW
)
COMMENT 'Portfolio positions enriched with risk factor attributes; static positions (no intraday trading). Lineage: bronze_positions + silver_risk_factors.'
TBLPROPERTIES ('quality' = 'silver')
AS SELECT
  trim(p.position_id)             AS position_id,
  trim(p.portfolio_id)            AS portfolio_id,
  upper(trim(p.book))             AS book,
  upper(trim(p.instrument_type))  AS instrument_type,
  trim(p.risk_factor_id)          AS risk_factor_id,
  rf.asset_class,
  rf.quote_currency,
  p.quantity,
  p.quantity_unit,
  p.description
FROM bronze_positions AS p
LEFT JOIN silver_risk_factors AS rf
  ON rf.risk_factor_id = trim(p.risk_factor_id);
