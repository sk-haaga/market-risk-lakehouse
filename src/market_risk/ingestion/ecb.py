"""ECB euro foreign exchange reference rates (dataflow EXR) from the ECB Data Portal API.

Rates are quoted as units of foreign currency per 1 EUR, published on TARGET business days
around 16:00 CET. API docs: https://data.ecb.europa.eu/help/api/data
"""

from __future__ import annotations

import io
from collections.abc import Iterable
from datetime import date, datetime

import pandas as pd

from market_risk.ingestion import LANDING_COLUMNS
from market_risk.ingestion.http import get_text

ECB_DATA_URL = "https://data-api.ecb.europa.eu/service/data/EXR"
SOURCE = "ECB"


def build_series_key(currencies: Iterable[str]) -> str:
    """SDMX key for daily spot reference rates against EUR, e.g. ``D.CHF+USD.EUR.SP00.A``."""
    ccys = sorted({c.strip().upper() for c in currencies if c and c.strip()})
    if not ccys:
        raise ValueError("At least one currency is required")
    bad = [c for c in ccys if len(c) != 3 or not c.isalpha()]
    if bad:
        raise ValueError(f"Not ISO 4217 currency codes: {bad}")
    if "EUR" in ccys:
        raise ValueError("EUR is the base currency and has no reference rate")
    return f"D.{'+'.join(ccys)}.EUR.SP00.A"


def build_request(currencies: Iterable[str], start: date, end: date | None = None) -> tuple[str, dict]:
    params = {"format": "csvdata", "startPeriod": start.isoformat()}
    if end is not None:
        params["endPeriod"] = end.isoformat()
    return f"{ECB_DATA_URL}/{build_series_key(currencies)}", params


def parse_csv(text: str, ingested_at: datetime) -> pd.DataFrame:
    """Parse an ECB ``csvdata`` response into the common landing format."""
    raw = pd.read_csv(io.StringIO(text), dtype=str)
    required = {"KEY", "CURRENCY", "TIME_PERIOD", "OBS_VALUE"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"ECB response is missing columns {sorted(missing)}")
    out = pd.DataFrame(
        {
            "source": SOURCE,
            "series_id": "EXR." + raw["KEY"].str.removeprefix("EXR."),
            "currency": raw["CURRENCY"].str.upper(),
            "obs_date": pd.to_datetime(raw["TIME_PERIOD"], format="%Y-%m-%d").dt.date,
            "value": pd.to_numeric(raw["OBS_VALUE"], errors="coerce"),
            "ingested_at": pd.Timestamp(ingested_at),
        }
    )
    return out[LANDING_COLUMNS].sort_values(["currency", "obs_date"]).reset_index(drop=True)


def fetch(currencies: Iterable[str], start: date, end: date | None, ingested_at: datetime) -> pd.DataFrame:
    url, params = build_request(currencies, start, end)
    return parse_csv(get_text(url, params=params), ingested_at)
