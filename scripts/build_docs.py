"""Regenerate docs/lineage.md, docs/data_quality.md and docs/data_dictionary.md."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from market_risk.docs_gen import build, quality_summary  # noqa: E402

if __name__ == "__main__":
    for path in build():
        print(f"wrote {path}")
    s = quality_summary()
    print(
        f"{s['total']} data quality tests ({s['expectations']} expectations + {s['checks']} checks) "
        f"covering {s['tables_covered']}/{s['tables']} tables"
    )
