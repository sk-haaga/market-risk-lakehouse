"""Run the Lakeflow pipeline SQL on a local Spark session.

This is a test harness, not a replacement for Databricks: it executes each model's
query (in dependency order) as a temp view, enforces the declared column contract and
applies expectations with the same semantics as the pipeline (warn / drop row /
fail update). ``read_files`` calls are replaced by plain CSV reads of the landing
folder. It lets CI prove that the SQL is valid and the models produce correct data
without a Databricks workspace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from market_risk.pipeline_sql import Model, load_models, matching_paren, topological_order

_SPARK_TYPES = {
    "STRING": "string",
    "DOUBLE": "double",
    "INT": "int",
    "BIGINT": "bigint",
    "BOOLEAN": "boolean",
    "DATE": "date",
    "TIMESTAMP": "timestamp",
}


class ExpectationFailed(RuntimeError):
    pass


@dataclass
class ExpectationResult:
    model: str
    expectation: str
    action: str
    failed_records: int
    total_records: int


@dataclass
class LocalPipelineRun:
    expectation_results: list[ExpectationResult] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)

    def failures(self, model: str | None = None) -> dict[str, int]:
        return {
            f"{r.model}.{r.expectation}": r.failed_records
            for r in self.expectation_results
            if r.failed_records and (model is None or r.model == model)
        }


def _replace_read_files(spark, query: str, view_prefix: str) -> str:
    """Swap every ``[STREAM] read_files('<path>', ..., schema => '<ddl>')`` for a temp view."""
    from pyspark.sql import functions as F
    from pyspark.sql.types import StringType, StructField, StructType

    n = 0
    while True:
        m = re.search(r"(STREAM\s+)?read_files\s*\(", query, flags=re.IGNORECASE)
        if not m:
            return query
        open_idx = m.end() - 1
        close_idx = matching_paren(query, open_idx)
        args = query[open_idx + 1 : close_idx]
        path = re.match(r"\s*'([^']+)'", args).group(1)
        schema = re.search(r"schema\s*=>\s*'([^']+)'", args).group(1)

        files = sorted(Path(path).rglob("*.csv")) if Path(path).exists() else []
        if files:
            df = (
                spark.read.schema(schema)
                .option("header", True)
                .option("recursiveFileLookup", True)
                .csv(path)
                .select("*", "_metadata")
            )
        else:
            meta = StructType([StructField("file_path", StringType())])
            df = spark.createDataFrame([], schema).withColumn("_metadata", F.lit(None).cast(meta))
        view = f"{view_prefix}_src{n}"
        df.createOrReplaceTempView(view)
        query = query[: m.start()] + view + query[close_idx + 1 :]
        n += 1


def _check_contract(model: Model, df) -> None:
    actual = [(f.name, f.dataType.simpleString()) for f in df.schema.fields]
    declared = [(c.name, _SPARK_TYPES.get(c.data_type, c.data_type.lower())) for c in model.columns]
    if [a[0] for a in actual] != [d[0] for d in declared]:
        raise AssertionError(
            f"{model.name}: query columns {[a[0] for a in actual]} != declared {[d[0] for d in declared]}"
        )
    mismatched = [(a, d) for a, d in zip(actual, declared, strict=True) if a[1] != d[1]]
    if mismatched:
        raise AssertionError(f"{model.name}: column type mismatch (actual, declared): {mismatched}")


def run_pipeline(
    spark,
    landing_root: str | Path,
    models: dict[str, Model] | None = None,
    extra_params: dict[str, str] | None = None,
) -> LocalPipelineRun:
    models = models or load_models()
    params = {"landing_root": str(Path(landing_root).resolve()), **(extra_params or {})}
    run = LocalPipelineRun()

    for name in topological_order(models):
        model = models[name]
        query = model.query
        for key, value in params.items():
            query = query.replace("${" + key + "}", value)
        query = _replace_read_files(spark, query, name)

        df = spark.sql(query)
        _check_contract(model, df)
        df = df.cache()
        total = df.count()

        for exp in model.expectations:
            ok = f"coalesce(({exp.expression}), false)"
            failed = df.where(f"NOT {ok}").count()
            run.expectation_results.append(ExpectationResult(name, exp.name, exp.action, failed, total))
            if failed and exp.action == "fail":
                raise ExpectationFailed(f"{name}.{exp.name} failed for {failed} of {total} rows: {exp.expression}")
            if failed and exp.action == "drop":
                df = df.where(ok)

        # Materialise and cut the logical plan: downstream models then read a plain
        # relation, like a published table on Databricks (it also sidesteps a Spark CTE
        # inlining bug when temp views built from CTE queries are nested several deep).
        df = df.localCheckpoint(eager=True)
        df.createOrReplaceTempView(name)
        run.row_counts[name] = df.count()
    return run
