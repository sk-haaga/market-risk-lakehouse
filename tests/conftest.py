from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def spark():
    pytest.importorskip("pyspark")
    from market_risk.local_run import local_spark

    session = local_spark("market-risk-tests")
    yield session
    session.stop()
