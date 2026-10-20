"""Reference data shipped with the package (risk factor catalogue and the demo portfolio)."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import pandas as pd

REFERENCE_FILES = ("risk_factors.csv", "portfolio_positions.csv")


def reference_path(name: str) -> Path:
    """Filesystem path of a packaged reference CSV."""
    if name not in REFERENCE_FILES:
        raise ValueError(f"Unknown reference file {name!r}; expected one of {REFERENCE_FILES}")
    return Path(str(resources.files(__package__).joinpath(name)))


def load_risk_factors() -> pd.DataFrame:
    return pd.read_csv(reference_path("risk_factors.csv"))


def load_positions() -> pd.DataFrame:
    return pd.read_csv(reference_path("portfolio_positions.csv"))
