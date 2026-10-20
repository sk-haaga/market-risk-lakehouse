"""Equity index closing prices from FRED (Federal Reserve Bank of St. Louis).

Uses the public ``fredgraph.csv`` download, which needs no API key. Missing observations
(market holidays) are returned as ``.`` or blank and are landed as nulls; the silver layer
drops them. Note: some index series on FRED are licensed for limited history (e.g. SP500
and DJIA only cover the last ~10 years).
"""

from __future__ import annotations

import io
from datetime import date, datetime

import pandas as pd

from market_risk.ingestion import LANDING_COLUMNS
from market_risk.ingestion.http import get_text

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
SOURCE = "FRED"


def build_request(series_id: str, start: date, end: date | None = None) -> tuple[str, dict]:
    if not series_id or not series_id.replace("_", "").isalnum():
        raise ValueError(f"Invalid FRED series id {series_id!r}")
    params = {"id": series_id, "cosd": start.isoformat()}
    if end is not None:
        params["coed"] = end.isoformat()
    return FRED_CSV_URL, params


def parse_csv(text: str, series_id: str, currency: str, ingested_at: datetime) -> pd.DataFrame:
    """Parse a fredgraph CSV (date column + one value column) into the landing format."""
    raw = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    if raw.shape[1] < 2:
        raise ValueError("FRED response must contain a date column and a value column")
    date_col = raw.columns[0]
    value_col = series_id if series_id in raw.columns else raw.columns[1]
    values = raw[value_col].str.strip().replace({".": None, "": None})
    out = pd.DataFrame(
        {
            "source": SOURCE,
            "series_id": series_id,
            "currency": currency.upper(),
            "obs_date": pd.to_datetime(raw[date_col], format="%Y-%m-%d").dt.date,
            "value": pd.to_numeric(values, errors="coerce"),
            "ingested_at": pd.Timestamp(ingested_at),
        }
    )
    return out[LANDING_COLUMNS].sort_values("obs_date").reset_index(drop=True)


def fetch(series_id: str, currency: str, start: date, end: date | None, ingested_at: datetime) -> pd.DataFrame:
    url, params = build_request(series_id, start, end)
    return parse_csv(get_text(url, params=params), series_id, currency, ingested_at)
