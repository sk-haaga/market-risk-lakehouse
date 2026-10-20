"""Source connectors that turn public market data into a common landing format.

Every connector returns a DataFrame with exactly ``LANDING_COLUMNS`` so the bronze
layer can read all landing files with one fixed schema.
"""

LANDING_COLUMNS = ["source", "series_id", "currency", "obs_date", "value", "ingested_at"]

LANDING_SCHEMA_DDL = (
    "source STRING, series_id STRING, currency STRING, obs_date DATE, value DOUBLE, ingested_at TIMESTAMP"
)
