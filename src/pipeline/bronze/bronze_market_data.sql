-- =============================================================================
-- BRONZE: raw market data, exactly as landed by the ingestion task.
-- Append-only streaming tables over the landing volume (Auto Loader via read_files).
-- Expectations here only WARN (no ON VIOLATION): bronze keeps every record so that
-- issues stay visible in pipeline event-log metrics; silver decides what to drop.
-- Pipeline parameter: ${landing_root} = /Volumes/<catalog>/<schema>/landing
-- =============================================================================

CREATE OR REFRESH STREAMING TABLE bronze_ecb_fx_rates (
  source        STRING    COMMENT 'Data provider, always ECB',
  series_id     STRING    COMMENT 'ECB SDMX series key, e.g. EXR.D.USD.EUR.SP00.A',
  currency      STRING    COMMENT 'Quoted currency (ISO 4217)',
  obs_date      DATE      COMMENT 'Reference rate date (TARGET business day)',
  value         DOUBLE    COMMENT 'Units of currency per 1 EUR',
  ingested_at   TIMESTAMP COMMENT 'UTC time the ingestion task fetched the data',
  source_file   STRING    COMMENT 'Landing file the row was read from',
  loaded_at     TIMESTAMP COMMENT 'Time the pipeline loaded the row into bronze',
  CONSTRAINT ecb_source_is_ecb      EXPECT (source = 'ECB'),
  CONSTRAINT ecb_obs_date_present   EXPECT (obs_date IS NOT NULL),
  CONSTRAINT ecb_currency_present   EXPECT (currency IS NOT NULL),
  CONSTRAINT ecb_value_present      EXPECT (value IS NOT NULL),
  CONSTRAINT ecb_ingested_at_present EXPECT (ingested_at IS NOT NULL)
)
COMMENT 'Raw ECB euro foreign exchange reference rates (dataflow EXR), one row per landed observation. Lineage: ECB Data Portal API -> landing volume /ecb -> this table.'
TBLPROPERTIES ('quality' = 'bronze', 'source' = 'ECB Data Portal')
AS SELECT
  source,
  series_id,
  currency,
  obs_date,
  value,
  ingested_at,
  _metadata.file_path AS source_file,
  current_timestamp() AS loaded_at
FROM STREAM read_files(
  '${landing_root}/ecb/',
  format => 'csv',
  header => true,
  schema => 'source STRING, series_id STRING, currency STRING, obs_date DATE, value DOUBLE, ingested_at TIMESTAMP'
);


CREATE OR REFRESH STREAMING TABLE bronze_index_prices (
  source        STRING    COMMENT 'Data provider, always FRED',
  series_id     STRING    COMMENT 'FRED series id, e.g. SP500',
  currency      STRING    COMMENT 'Index quote currency (ISO 4217)',
  obs_date      DATE      COMMENT 'Trading date',
  value         DOUBLE    COMMENT 'Index closing level in quote currency; null on market holidays',
  ingested_at   TIMESTAMP COMMENT 'UTC time the ingestion task fetched the data',
  source_file   STRING    COMMENT 'Landing file the row was read from',
  loaded_at     TIMESTAMP COMMENT 'Time the pipeline loaded the row into bronze',
  CONSTRAINT fred_source_is_fred      EXPECT (source = 'FRED'),
  CONSTRAINT fred_obs_date_present    EXPECT (obs_date IS NOT NULL),
  CONSTRAINT fred_series_present      EXPECT (series_id IS NOT NULL),
  CONSTRAINT fred_ingested_at_present EXPECT (ingested_at IS NOT NULL)
)
COMMENT 'Raw equity index closing levels from FRED, one row per landed observation (holidays arrive as null values). Lineage: FRED fredgraph.csv -> landing volume /fred -> this table.'
TBLPROPERTIES ('quality' = 'bronze', 'source' = 'FRED')
AS SELECT
  source,
  series_id,
  currency,
  obs_date,
  value,
  ingested_at,
  _metadata.file_path AS source_file,
  current_timestamp() AS loaded_at
FROM STREAM read_files(
  '${landing_root}/fred/',
  format => 'csv',
  header => true,
  schema => 'source STRING, series_id STRING, currency STRING, obs_date DATE, value DOUBLE, ingested_at TIMESTAMP'
);
