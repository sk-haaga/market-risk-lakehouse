"""Write landing files to a Unity Catalog volume (or any local folder for local runs).

Layout::

    <landing_root>/ecb/2026/10/ecb_20261018T213000Z.csv
    <landing_root>/fred/2026/10/fred_20261018T213000Z.csv
    <landing_root>/reference/risk_factors/risk_factors.csv
    <landing_root>/reference/positions/portfolio_positions.csv

Market data files are append-only and immutable; each run writes a new file and the
silver layer keeps the latest ingested value per (series, date), which also absorbs
revisions published by the source.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd

from market_risk.ingestion import LANDING_COLUMNS
from market_risk.reference import reference_path

REFERENCE_TARGETS = {
    "risk_factors.csv": "reference/risk_factors/risk_factors.csv",
    "portfolio_positions.csv": "reference/positions/portfolio_positions.csv",
}


def market_data_path(landing_root: str | Path, source: str, run_ts: datetime) -> Path:
    stamp = run_ts.strftime("%Y%m%dT%H%M%SZ")
    folder = Path(landing_root) / source.lower() / f"{run_ts:%Y}" / f"{run_ts:%m}"
    return folder / f"{source.lower()}_{stamp}.csv"


def write_market_data(df: pd.DataFrame, landing_root: str | Path, source: str, run_ts: datetime) -> Path:
    if list(df.columns) != LANDING_COLUMNS:
        raise ValueError(f"Landing frame must have columns {LANDING_COLUMNS}, got {list(df.columns)}")
    if df.empty:
        raise ValueError(f"Refusing to write an empty {source} landing file")
    path = market_data_path(landing_root, source, run_ts)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    out["ingested_at"] = pd.to_datetime(out["ingested_at"]).dt.strftime("%Y-%m-%dT%H:%M:%S")
    out.to_csv(path, index=False)
    return path


def publish_reference_data(landing_root: str | Path) -> list[Path]:
    """Copy packaged reference CSVs (risk factors, positions) into the landing area."""
    written = []
    for name, rel in REFERENCE_TARGETS.items():
        dest = Path(landing_root) / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(reference_path(name), dest)
        written.append(dest)
    return written
