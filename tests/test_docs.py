"""The committed docs must match what the code generates."""

import pytest

from market_risk.docs_gen import DOCS_DIR, GENERATED, quality_summary


@pytest.mark.parametrize("name", sorted(GENERATED))
def test_generated_docs_are_up_to_date(name):
    committed = (DOCS_DIR / name).read_text()
    assert committed == GENERATED[name](), f"docs/{name} is stale: run python scripts/build_docs.py"


def test_every_table_has_a_test():
    s = quality_summary()
    assert s["tables_covered"] == s["tables"]
