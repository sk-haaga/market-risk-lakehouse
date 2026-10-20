"""Job task 4: run the data quality checks and record the results.

Results are appended to ``ops_dq_results`` (one row per check per run) so data quality
history can be charted in Power BI. The task fails if any ``error`` check fails.

Databricks::

    run_quality_checks --catalog main --schema market_risk
"""

from __future__ import annotations

import argparse
import uuid
from dataclasses import asdict

from market_risk.jobs.common import fqn, get_logger, get_spark, utc_now
from market_risk.quality.checks import blocking_failures, load_checks, run_checks

log = get_logger("quality")

RESULTS_TABLE = "ops_dq_results"
RESULTS_DDL = (
    "run_id STRING, run_at TIMESTAMP, check_id STRING, table_name STRING, check_type STRING, "
    "severity STRING, status STRING, failing_rows BIGINT, description STRING, sql STRING, error STRING"
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--catalog", required=True)
    p.add_argument("--schema", required=True)
    p.add_argument("--checks-file", help="Override the packaged checks.yml")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    spark = get_spark()
    checks = load_checks(args.checks_file)
    results = run_checks(spark, checks, lambda t: fqn(args.catalog, args.schema, t))

    run_id, run_at = uuid.uuid4().hex[:12], utc_now()
    rows = [
        {
            "run_id": run_id,
            "run_at": run_at,
            "check_id": r.check_id,
            "table_name": r.table,
            "check_type": r.check_type,
            "severity": r.severity,
            "status": r.status,
            "failing_rows": r.failing_rows,
            "description": r.description,
            "sql": r.sql,
            "error": r.error,
        }
        for r in results
    ]
    target = fqn(args.catalog, args.schema, RESULTS_TABLE)
    spark.createDataFrame(rows, schema=RESULTS_DDL).write.mode("append").saveAsTable(target)

    passed = sum(r.status == "PASS" for r in results)
    log.info("%s/%s checks passed (run %s)", passed, len(results), run_id)
    for r in results:
        if r.status != "PASS":
            log.warning(
                "%s %s [%s]: %s failing rows %s", r.status, r.check_id, r.severity, r.failing_rows, r.error or ""
            )

    blocking = blocking_failures(results)
    if blocking:
        raise SystemExit("Data quality checks failed: " + ", ".join(asdict(b)["check_id"] for b in blocking))


if __name__ == "__main__":
    main()
