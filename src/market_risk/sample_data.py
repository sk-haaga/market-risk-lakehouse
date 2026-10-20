"""Synthetic market data in the exact landing format, for tests and offline demos.

Fat-tailed (multivariate Student-t), correlated daily moves for the four equity indices
and four ECB currencies, with realistic starting levels, market holidays, a re-ingested
look-back window containing one revised ECB print, and (optionally) a few deliberately
bad records so the data quality layer has something to catch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from market_risk.ingestion import LANDING_COLUMNS
from market_risk.ingestion.landing import publish_reference_data, write_market_data

# id: (start level, annual vol, currency)
INDICES = {
    "SP500": (4700.0, 0.18, "USD"),
    "NASDAQCOM": (15000.0, 0.24, "USD"),
    "DJIA": (36000.0, 0.15, "USD"),
    "NIKKEI225": (29000.0, 0.20, "JPY"),
}
FX = {  # currency per 1 EUR
    "USD": (1.13, 0.07),
    "JPY": (130.0, 0.10),
    "GBP": (0.84, 0.06),
    "CHF": (1.04, 0.06),
}
SERIES = list(INDICES) + list(FX)
CORR = np.array(
    [
        # SPX  NDQ   DJIA  N225  USD   JPY   GBP   CHF
        [1.00, 0.92, 0.95, 0.45, -0.10, -0.20, 0.05, -0.15],
        [0.92, 1.00, 0.85, 0.42, -0.10, -0.18, 0.05, -0.15],
        [0.95, 0.85, 1.00, 0.43, -0.10, -0.20, 0.05, -0.15],
        [0.45, 0.42, 0.43, 1.00, 0.05, 0.30, 0.00, 0.05],
        [-0.10, -0.10, -0.10, 0.05, 1.00, 0.55, 0.50, 0.45],
        [-0.20, -0.18, -0.20, 0.30, 0.55, 1.00, 0.35, 0.50],
        [0.05, 0.05, 0.05, 0.00, 0.50, 0.35, 1.00, 0.30],
        [-0.15, -0.15, -0.15, 0.05, 0.45, 0.50, 0.30, 1.00],
    ]
)

ECB_HOLIDAYS = {(1, 1), (5, 1), (12, 25), (12, 26)}
US_HOLIDAYS = {(1, 1), (7, 4), (12, 25)}
JP_HOLIDAYS = {(1, 1), (1, 2), (1, 3), (5, 3), (5, 4), (5, 5)}


@dataclass
class SyntheticLanding:
    ecb: pd.DataFrame
    fred: pd.DataFrame
    revised: dict  # details of the revised ECB print, for assertions


def weekdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def simulate_levels(days: list[date], seed: int = 7, dof: float = 4.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    vols = np.array([v[1] for v in INDICES.values()] + [v[1] for v in FX.values()]) / np.sqrt(252)
    chol = np.linalg.cholesky(CORR)
    z = rng.standard_normal((len(days), len(SERIES))) @ chol.T
    # multivariate Student-t with unit variance
    w = np.sqrt(rng.chisquare(dof, size=(len(days), 1)) / (dof - 2))
    shocks = z / w * vols
    shocks[0] = 0.0
    start = np.array([v[0] for v in INDICES.values()] + [v[0] for v in FX.values()])
    levels = start * np.exp(np.cumsum(shocks, axis=0))
    return pd.DataFrame(levels, index=pd.Index(days, name="obs_date"), columns=SERIES)


def build(
    start: date = date(2022, 1, 3),
    n_days: int = 700,
    seed: int = 7,
    inject_anomalies: bool = False,
    first_run: datetime = datetime(2024, 9, 1, 21, 0, 0),
    second_run: datetime = datetime(2024, 9, 2, 21, 0, 0),
) -> SyntheticLanding:
    days = weekdays(start, n_days)
    levels = simulate_levels(days, seed)

    ecb_rows, fred_rows = [], []
    for d in days:
        md = (d.month, d.day)
        for ccy in FX:
            if md not in ECB_HOLIDAYS:
                ecb_rows.append(("ECB", f"EXR.D.{ccy}.EUR.SP00.A", ccy, d, round(levels.at[d, ccy], 4)))
        for sid, (_, _, ccy) in INDICES.items():
            closed = md in (JP_HOLIDAYS if ccy == "JPY" else US_HOLIDAYS)
            value = None if closed else round(levels.at[d, sid], 2)
            fred_rows.append(("FRED", sid, ccy, d, value))  # FRED lists holidays with "."

    cols = LANDING_COLUMNS[:-1]
    ecb = pd.DataFrame(ecb_rows, columns=cols)
    fred = pd.DataFrame(fred_rows, columns=cols)

    # first run lands everything up to 5 days before the end ...
    cutoff_first = days[-5]
    lookback_from = days[-15]
    ecb1 = ecb[ecb.obs_date <= cutoff_first].assign(ingested_at=first_run)
    fred1 = fred[fred.obs_date <= cutoff_first].assign(ingested_at=first_run)
    # ... the second run re-sends a 15-day look-back window, with one revised ECB print
    ecb2 = ecb[ecb.obs_date >= lookback_from].assign(ingested_at=second_run).reset_index(drop=True)
    fred2 = fred[fred.obs_date >= lookback_from].assign(ingested_at=second_run)
    revised_idx = ecb2.index[(ecb2.currency == "USD")][0]
    old_value = ecb2.at[revised_idx, "value"]
    ecb2.at[revised_idx, "value"] = round(old_value * 1.001, 4)
    revised = {
        "currency": "USD",
        "obs_date": ecb2.at[revised_idx, "obs_date"],
        "old_value": old_value,
        "new_value": ecb2.at[revised_idx, "value"],
    }

    ecb_all = pd.concat([ecb1, ecb2], ignore_index=True)[LANDING_COLUMNS]
    fred_all = pd.concat([fred1, fred2], ignore_index=True)[LANDING_COLUMNS]

    if inject_anomalies:
        d = days[100]
        bad_fred = pd.DataFrame(
            [
                ("FRED", "UNKNOWN_SERIES", "USD", d, 123.0, first_run),  # not in catalogue
                ("FRED", "SP500", "EUR", d, 4500.0, first_run),  # wrong currency
            ],
            columns=LANDING_COLUMNS,
        )
        bad_ecb = pd.DataFrame(
            [("ECB", "EXR.D.USD.EUR.SP00.A", "USD", days[101], -1.0, datetime(2020, 1, 1))],
            columns=LANDING_COLUMNS,
        )  # negative rate, older ingestion -> loses de-dup anyway; plus a lone bad date below
        bad_ecb2 = pd.DataFrame(
            [("ECB", "EXR.D.XYZ.EUR.SP00.A", "XYZ", days[102], -5.0, first_run)],
            columns=LANDING_COLUMNS,
        )
        fred_all = pd.concat([fred_all, bad_fred], ignore_index=True)
        ecb_all = pd.concat([ecb_all, bad_ecb, bad_ecb2], ignore_index=True)

    return SyntheticLanding(ecb=ecb_all, fred=fred_all, revised=revised)


def write_landing(landing_root: str | Path, data: SyntheticLanding) -> None:
    """Write the synthetic data as one landing file per (source, ingestion run)."""
    for source, df in (("ecb", data.ecb), ("fred", data.fred)):
        for run_ts, part in df.groupby("ingested_at"):
            write_market_data(part.reset_index(drop=True), landing_root, source, run_ts.to_pydatetime())
    publish_reference_data(landing_root)
