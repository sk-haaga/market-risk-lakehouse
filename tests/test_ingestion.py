from datetime import date, datetime

import pandas as pd
import pytest

from market_risk.ingestion import LANDING_COLUMNS, ecb, fred
from market_risk.ingestion.landing import (
    market_data_path,
    publish_reference_data,
    write_market_data,
)
from market_risk.jobs.ingest import resolve_start
from market_risk.reference import load_positions, load_risk_factors

TS = datetime(2025, 10, 6, 21, 30, 0)


def test_ecb_series_key_sorted_and_deduplicated():
    assert ecb.build_series_key(["usd", "JPY", "USD"]) == "D.JPY+USD.EUR.SP00.A"


@pytest.mark.parametrize("bad", [[], ["EUR"], ["US"], ["U$D"]])
def test_ecb_series_key_rejects_invalid(bad):
    with pytest.raises(ValueError):
        ecb.build_series_key(bad)


def test_ecb_request_params():
    url, params = ecb.build_request(["USD"], date(2020, 1, 1), date(2020, 2, 1))
    assert url.endswith("/EXR/D.USD.EUR.SP00.A")
    assert params == {"format": "csvdata", "startPeriod": "2020-01-01", "endPeriod": "2020-02-01"}


def test_ecb_parse_csv(fixtures):
    df = ecb.parse_csv((fixtures / "ecb_exr_sample.csv").read_text(), TS)
    assert list(df.columns) == LANDING_COLUMNS
    assert len(df) == 5
    assert set(df["currency"]) == {"USD", "JPY"}
    usd = df[df.currency == "USD"].set_index("obs_date")["value"]
    assert usd[date(2025, 10, 1)] == pytest.approx(1.1741)
    assert pd.isna(usd[date(2025, 10, 3)])
    assert df["series_id"].str.startswith("EXR.D.").all()
    assert (df["source"] == "ECB").all()


def test_ecb_parse_csv_missing_columns():
    with pytest.raises(ValueError, match="missing columns"):
        ecb.parse_csv("KEY,TIME_PERIOD\nx,2025-01-01\n", TS)


def test_fred_request_params():
    url, params = fred.build_request("SP500", date(2016, 1, 1))
    assert url.endswith("fredgraph.csv")
    assert params == {"id": "SP500", "cosd": "2016-01-01"}
    with pytest.raises(ValueError):
        fred.build_request("SP500;DROP", date(2016, 1, 1))


def test_fred_parse_csv_turns_holidays_into_nulls(fixtures):
    df = fred.parse_csv((fixtures / "fred_sp500_sample.csv").read_text(), "SP500", "usd", TS)
    assert list(df.columns) == LANDING_COLUMNS
    assert len(df) == 5
    assert df["value"].isna().sum() == 2  # "." and blank
    assert (df["currency"] == "USD").all()
    assert df.loc[df.obs_date == date(2025, 10, 6), "value"].item() == pytest.approx(6740.28)


def test_fred_parse_handles_legacy_date_header():
    text = "DATE,NIKKEI225\n2025-10-01,44550.85\n"
    df = fred.parse_csv(text, "NIKKEI225", "JPY", TS)
    assert df["value"].item() == pytest.approx(44550.85)


def test_landing_path_layout(tmp_path):
    path = market_data_path(tmp_path, "ECB", TS)
    assert path.relative_to(tmp_path).as_posix() == "ecb/2025/10/ecb_20251006T213000Z.csv"


def test_write_market_data_roundtrip(tmp_path, fixtures):
    df = ecb.parse_csv((fixtures / "ecb_exr_sample.csv").read_text(), TS)
    path = write_market_data(df, tmp_path, "ecb", TS)
    back = pd.read_csv(path)
    assert list(back.columns) == LANDING_COLUMNS
    assert back["ingested_at"].iloc[0] == "2025-10-06T21:30:00"


def test_write_market_data_validates(tmp_path):
    with pytest.raises(ValueError, match="columns"):
        write_market_data(pd.DataFrame({"a": [1]}), tmp_path, "ecb", TS)
    with pytest.raises(ValueError, match="empty"):
        write_market_data(pd.DataFrame(columns=LANDING_COLUMNS), tmp_path, "ecb", TS)


def test_publish_reference_data(tmp_path):
    written = publish_reference_data(tmp_path)
    assert {p.relative_to(tmp_path).as_posix() for p in written} == {
        "reference/risk_factors/risk_factors.csv",
        "reference/positions/portfolio_positions.csv",
    }


def test_reference_data_is_consistent():
    rf, pos = load_risk_factors(), load_positions()
    assert rf["risk_factor_id"].is_unique
    assert pos["position_id"].is_unique
    assert set(pos["risk_factor_id"]) <= set(rf["risk_factor_id"])
    fx = rf[rf.asset_class == "FX"]
    assert (fx["source"] == "ECB").all() and (fx["source_series_id"] == fx["quote_currency"]).all()


@pytest.mark.parametrize(
    "watermark,expected",
    [
        (None, date(2016, 1, 1)),
        (date(2025, 10, 10), date(2025, 9, 30)),
        (date(2016, 1, 5), date(2016, 1, 1)),
    ],
)
def test_incremental_start(watermark, expected):
    assert resolve_start(watermark, date(2016, 1, 1), 10) == expected
