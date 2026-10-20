"""End-to-end local run: landing -> pipeline SQL -> risk engine -> data quality checks.

Mirrors the Databricks job on a local Spark session so the whole project can be tried
(and tested in CI) without a workspace. Outputs land as CSV files that Power BI Desktop
can also read for offline dashboard development.
"""

from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from market_risk import sample_data
from market_risk.jobs import compute_risk, ingest
from market_risk.local_pipeline import LocalPipelineRun, run_pipeline
from market_risk.pipeline_sql import load_models
from market_risk.quality.checks import CheckResult, load_checks, run_checks


@dataclass
class LocalRunResult:
    pipeline: LocalPipelineRun
    risk_outputs: dict[str, pd.DataFrame]
    checks: list[CheckResult]


def local_spark(app_name: str = "market-risk-lakehouse"):
    from pyspark.sql import SparkSession

    spark = (
        SparkSession.builder.master("local[2]")
        .appName(app_name)
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.driver.memory", "2g")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    return spark


def run(spark, landing_root: str | Path, window: int = 250) -> LocalRunResult:
    pipeline = run_pipeline(spark, landing_root, load_models())
    inputs = compute_risk.read_inputs(spark, None, None)
    outputs = compute_risk.compute(*inputs, config=compute_risk.RiskConfig(window=window))
    compute_risk.write_outputs(spark, outputs, None, None, as_temp_views=True)

    latest = spark.sql("SELECT max(rate_date) AS d FROM silver_fx_rates").collect()[0]["d"]
    checks = run_checks(spark, load_checks(), lambda t: t, today_sql=f"DATE'{latest}'")
    return LocalRunResult(pipeline, outputs, checks)


def export(spark, result: LocalRunResult, output_dir: str | Path) -> list[Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    tables = [n for n in result.pipeline.row_counts if n.startswith(("gold_", "silver_"))]
    for name in tables:
        path = out / f"{name}.csv"
        spark.table(name).toPandas().to_csv(path, index=False)
        written.append(path)
    for name, df in result.risk_outputs.items():
        path = out / f"{name}.csv"
        df.to_csv(path, index=False)
        written.append(path)
    dq = pd.DataFrame([r.__dict__ for r in result.checks]).drop(columns=["sql"])
    dq.to_csv(out / "ops_dq_results.csv", index=False)
    written.append(out / "ops_dq_results.csv")
    return written


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", default="local_data", help="Folder for landing files and outputs")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--fetch", action="store_true", help="Download real ECB and FRED data (needs internet)")
    src.add_argument("--synthetic", action="store_true", help="Generate synthetic data (default)")
    p.add_argument("--start-date", type=date.fromisoformat, default=date(2016, 1, 1))
    p.add_argument("--window", type=int, default=250)
    args = p.parse_args(argv)

    data_dir = Path(args.data_dir)
    landing = data_dir / "landing"
    shutil.rmtree(landing, ignore_errors=True)
    if args.fetch:
        ingest.run(landing, args.start_date, args.start_date)
    else:
        sample_data.write_landing(landing, sample_data.build())

    spark = local_spark()
    result = run(spark, landing, window=args.window)
    files = export(spark, result, data_dir / "output")

    print("\nRows per model:")
    for name, n in result.pipeline.row_counts.items():
        print(f"  {name:40s} {n:>8,}")
    failed_exp = result.pipeline.failures()
    print(f"\nExpectations evaluated: {len(result.pipeline.expectation_results)}, with violations: {len(failed_exp)}")
    for k, v in failed_exp.items():
        print(f"  {k}: {v}")
    passed = sum(r.status == "PASS" for r in result.checks)
    print(f"\nData quality checks passed: {passed}/{len(result.checks)}")
    for r in result.checks:
        if r.status != "PASS":
            print(f"  {r.status} {r.check_id} [{r.severity}] {r.failing_rows} {r.error or ''}")
    summary = result.risk_outputs["gold_backtest_summary"]
    cols = [
        "method",
        "confidence_level",
        "n_observations",
        "n_breaches",
        "expected_breaches",
        "kupiec_pvalue",
        "traffic_light_zone",
        "model_result",
    ]
    print("\nBacktest summary:\n" + summary[cols].to_string(index=False))
    print(f"\nWrote {len(files)} CSV files to {data_dir / 'output'}")


if __name__ == "__main__":
    main()
