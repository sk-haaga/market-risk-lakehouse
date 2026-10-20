"""Job task 1: land ECB FX rates, FRED index prices and reference data in the landing volume.

Incremental by default: the start date is the latest date already in bronze minus a
look-back window (to pick up late prints and revisions). The first run, or
``--full-refresh``, loads from ``--start-date``.

Databricks::

    ingest_market_data --catalog main --schema market_risk --volume landing

Local::

    python -m market_risk.jobs.ingest --landing-root ./local_landing --start-date 2016-01-01
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from market_risk.ingestion import ecb, fred
from market_risk.ingestion.landing import publish_reference_data, write_market_data
from market_risk.jobs.common import fqn, get_logger, utc_now, volume_path
from market_risk.reference import load_risk_factors

log = get_logger("ingest")

DEFAULT_START = date(2016, 1, 1)


def bronze_watermark(catalog: str, schema: str, table: str) -> date | None:
    """Latest observation date already in a bronze table, or None if it does not exist yet."""
    try:
        from market_risk.jobs.common import get_spark

        spark = get_spark()
        name = fqn(catalog, schema, table)
        if not spark.catalog.tableExists(name):
            return None
        return spark.sql(f"SELECT max(obs_date) AS d FROM {name}").collect()[0]["d"]
    except Exception as exc:  # noqa: BLE001 - watermark is an optimisation, never fatal
        log.warning("Could not read watermark from %s: %s", table, exc)
        return None


def resolve_start(watermark: date | None, default_start: date, lookback_days: int) -> date:
    if watermark is None:
        return default_start
    return max(default_start, watermark - timedelta(days=lookback_days))


def run(
    landing_root: str | Path,
    ecb_start: date,
    fred_start: date,
    end: date | None = None,
    run_ts: datetime | None = None,
    risk_factors: pd.DataFrame | None = None,
) -> dict[str, Path]:
    run_ts = run_ts or utc_now()
    rf = risk_factors if risk_factors is not None else load_risk_factors()
    written: dict[str, Path] = {}

    fx_ccys = rf.loc[rf["source"] == "ECB", "source_series_id"].tolist()
    log.info("ECB: %s from %s", fx_ccys, ecb_start)
    fx = ecb.fetch(fx_ccys, ecb_start, end, run_ts)
    written["ecb"] = write_market_data(fx, landing_root, "ecb", run_ts)
    log.info("ECB: %s rows -> %s", len(fx), written["ecb"])

    frames = []
    for row in rf.loc[rf["source"] == "FRED"].itertuples():
        log.info("FRED: %s from %s", row.source_series_id, fred_start)
        frames.append(fred.fetch(row.source_series_id, row.quote_currency, fred_start, end, run_ts))
    prices = pd.concat(frames, ignore_index=True)
    written["fred"] = write_market_data(prices, landing_root, "fred", run_ts)
    log.info("FRED: %s rows -> %s", len(prices), written["fred"])

    for path in publish_reference_data(landing_root):
        written[path.stem] = path
    return written


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--catalog")
    p.add_argument("--schema")
    p.add_argument("--volume", default="landing")
    p.add_argument("--landing-root", help="Override the landing folder (local runs)")
    p.add_argument("--start-date", type=date.fromisoformat, default=DEFAULT_START)
    p.add_argument("--end-date", type=date.fromisoformat)
    p.add_argument("--lookback-days", type=int, default=10)
    p.add_argument("--full-refresh", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.landing_root:
        landing_root = args.landing_root
    elif args.catalog and args.schema:
        landing_root = volume_path(args.catalog, args.schema, args.volume)
    else:
        raise SystemExit("Pass --landing-root, or --catalog and --schema")

    ecb_start = fred_start = args.start_date
    if not args.full_refresh and args.catalog and args.schema:
        ecb_start = resolve_start(
            bronze_watermark(args.catalog, args.schema, "bronze_ecb_fx_rates"),
            args.start_date,
            args.lookback_days,
        )
        fred_start = resolve_start(
            bronze_watermark(args.catalog, args.schema, "bronze_index_prices"),
            args.start_date,
            args.lookback_days,
        )
    run(landing_root, ecb_start, fred_start, args.end_date)


if __name__ == "__main__":
    main()
