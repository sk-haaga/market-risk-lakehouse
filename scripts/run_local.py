"""Run the whole project locally on Spark: landing -> pipeline -> risk -> checks -> CSV.

python scripts/run_local.py               # synthetic data, no internet needed
python scripts/run_local.py --fetch       # real ECB + FRED data
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from market_risk.local_run import main  # noqa: E402

if __name__ == "__main__":
    main()
