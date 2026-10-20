"""Compile ``checks.yml`` into SQL and run it with Spark.

Each check becomes a query that returns the *failing* rows; a check passes when it returns
none. Table names in the YAML and in custom SQL (``{table_name}``) are resolved to fully
qualified names at run time, so the same checks run on Databricks and locally.
"""

from __future__ import annotations

import string
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml

CHECK_TYPES = (
    "unique",
    "not_null",
    "accepted_values",
    "relationships",
    "row_count",
    "freshness",
    "expression",
    "sql",
)


@dataclass(frozen=True)
class Check:
    table: str
    name: str
    check_type: str
    severity: str
    description: str
    sql_template: str  # placeholders: {<table name>} and {today}

    @property
    def check_id(self) -> str:
        return f"{self.table}.{self.name}"

    def referenced_tables(self) -> set[str]:
        fields = {f for _, f, _, _ in string.Formatter().parse(self.sql_template) if f}
        return fields - {"today"}

    def render(self, resolve: Callable[[str], str], today_sql: str) -> str:
        mapping = {t: resolve(t) for t in self.referenced_tables()}
        mapping["today"] = today_sql
        return self.sql_template.format_map(mapping)


def _sql_literal(v) -> str:
    if isinstance(v, str):
        return "'" + v.replace("'", "\\'") + "'"
    return str(v)


def compile_check(table: str, spec: dict) -> Check:
    ctype = spec.get("type")
    if ctype not in CHECK_TYPES:
        raise ValueError(f"{table}: unknown check type {ctype!r}")
    t = "{" + table + "}"
    severity = spec.get("severity", "error")
    if severity not in ("error", "warn"):
        raise ValueError(f"{table}: severity must be error or warn")

    if ctype == "unique":
        cols = spec["columns"]
        name = "unique__" + "__".join(cols)
        sql = f"SELECT {', '.join(cols)} FROM {t} GROUP BY {', '.join(cols)} HAVING count(*) > 1"
        desc = f"({', '.join(cols)}) is unique"
    elif ctype == "not_null":
        cols = spec["columns"]
        name = "not_null__" + "__".join(cols)
        sql = f"SELECT * FROM {t} WHERE " + " OR ".join(f"{c} IS NULL" for c in cols)
        desc = f"{', '.join(cols)} never null"
    elif ctype == "accepted_values":
        col, values = spec["column"], spec["values"]
        name = f"accepted_values__{col}"
        sql = (
            f"SELECT {col} FROM {t} WHERE {col} IS NULL OR {col} NOT IN ({', '.join(_sql_literal(v) for v in values)})"
        )
        desc = f"{col} in {values}"
    elif ctype == "relationships":
        col, to, field = spec["column"], spec["to"], spec["field"]
        name = f"relationships__{col}__{to}"
        sql = (
            f"SELECT c.{col} FROM {t} c LEFT JOIN {{{to}}} p ON p.{field} = c.{col} "
            f"WHERE c.{col} IS NOT NULL AND p.{field} IS NULL"
        )
        desc = f"{col} references {to}.{field}"
    elif ctype == "row_count":
        lo = spec.get("min", 1)
        name = f"row_count_min_{lo}"
        sql = f"SELECT count(*) AS n FROM {t} HAVING count(*) < {lo}"
        desc = f"at least {lo} rows"
    elif ctype == "freshness":
        col, max_age = spec["column"], spec["max_age_days"]
        name = f"freshness__{col}"
        sql = f"SELECT max({col}) AS latest FROM {t} HAVING max({col}) IS NULL OR datediff({{today}}, max({col})) > {max_age}"
        desc = f"latest {col} at most {max_age} days old"
    elif ctype == "expression":
        expr = spec["expression"]
        name = spec.get("name") or "expression"
        sql = f"SELECT * FROM {t} WHERE NOT coalesce(({expr}), false)"
        desc = f"every row satisfies {expr}"
    else:  # sql
        name = spec["name"]
        sql = spec["sql"].strip()
        desc = spec.get("description", name)

    return Check(table, spec.get("name", name), ctype, severity, spec.get("description", desc), sql)


def default_checks_path() -> Path:
    return Path(str(resources.files("market_risk.quality").joinpath("checks.yml")))


def load_checks(path: str | Path | None = None) -> list[Check]:
    with open(path or default_checks_path()) as fh:
        config = yaml.safe_load(fh)
    checks = []
    for table, body in config["tables"].items():
        for spec in body.get("checks", []):
            checks.append(compile_check(table, spec))
    ids = [c.check_id for c in checks]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"Duplicate check names: {sorted(dupes)}")
    return checks


@dataclass
class CheckResult:
    check_id: str
    table: str
    check_type: str
    severity: str
    status: str  # PASS | FAIL | ERROR
    failing_rows: int
    description: str
    sql: str
    error: str | None = None


def run_checks(
    spark,
    checks: list[Check],
    resolve: Callable[[str], str],
    today_sql: str = "current_date()",
) -> list[CheckResult]:
    results = []
    for chk in checks:
        sql = chk.render(resolve, today_sql)
        try:
            n = spark.sql(f"SELECT count(*) AS n FROM ({sql}) failing").collect()[0]["n"]
            status, err = ("PASS" if n == 0 else "FAIL"), None
        except Exception as exc:  # noqa: BLE001 - a broken check is reported, not swallowed
            n, status, err = -1, "ERROR", str(exc).splitlines()[0][:500]
        results.append(
            CheckResult(
                chk.check_id, chk.table, chk.check_type, chk.severity, status, int(n), chk.description, sql, err
            )
        )
    return results


def blocking_failures(results: list[CheckResult]) -> list[CheckResult]:
    return [r for r in results if r.status == "ERROR" or (r.status == "FAIL" and r.severity == "error")]
