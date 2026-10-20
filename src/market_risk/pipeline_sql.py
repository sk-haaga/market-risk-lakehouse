"""Parse the Lakeflow Declarative Pipeline SQL files into a model catalogue.

The parsed catalogue is used to:

* generate the lineage and data quality docs (``scripts/build_docs.py``),
* enforce "every model is documented and tested" in CI (``tests/test_model_contracts.py``),
* run the exact same SQL on a local Spark session (``market_risk.local_pipeline``).

Only the subset of the pipeline SQL grammar used in this repository is supported:
``CREATE OR REFRESH {STREAMING TABLE | MATERIALIZED VIEW} name (columns, constraints)
COMMENT '...' TBLPROPERTIES (...) AS query``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"

_CREATE_RE = re.compile(
    r"^\s*CREATE\s+OR\s+REFRESH\s+(STREAMING\s+TABLE|MATERIALIZED\s+VIEW)\s+([A-Za-z_][\w.]*)\s*",
    re.IGNORECASE,
)
_CONSTRAINT_RE = re.compile(
    r"^CONSTRAINT\s+(\w+)\s+EXPECT\s*\((.*)\)\s*(?:ON\s+VIOLATION\s+(FAIL\s+UPDATE|DROP\s+ROW))?\s*$",
    re.IGNORECASE | re.DOTALL,
)
_COLUMN_RE = re.compile(
    r"^(\w+)\s+([A-Za-z]+(?:\(\s*\d+\s*(?:,\s*\d+\s*)?\))?)(?:\s+COMMENT\s+'((?:[^']|'')*)')?\s*$",
    re.IGNORECASE | re.DOTALL,
)
_TABLE_REF_RE = re.compile(r"\b(?:FROM|JOIN)\s+(?:STREAM\s*\(\s*)?([A-Za-z_][\w.]*)", re.IGNORECASE)


@dataclass(frozen=True)
class Column:
    name: str
    data_type: str
    comment: str | None


@dataclass(frozen=True)
class Expectation:
    name: str
    expression: str
    action: str  # "warn" | "drop" | "fail"

    @property
    def action_label(self) -> str:
        return {"warn": "warn (keep row)", "drop": "drop row", "fail": "fail update"}[self.action]


@dataclass
class Model:
    name: str
    kind: str  # "streaming_table" | "materialized_view"
    columns: list[Column]
    expectations: list[Expectation]
    comment: str | None
    properties: dict[str, str]
    query: str
    source_file: Path
    upstream: list[str] = field(default_factory=list)

    @property
    def layer(self) -> str:
        return self.name.split("_", 1)[0]


# --------------------------------------------------------------------------- lexing helpers


def strip_sql_comments(sql: str) -> str:
    """Remove ``--`` line comments while leaving string literals intact."""
    out, i, in_str = [], 0, False
    while i < len(sql):
        ch = sql[i]
        if in_str:
            out.append(ch)
            if ch == "'":
                if i + 1 < len(sql) and sql[i + 1] == "'":
                    out.append("'")
                    i += 1
                else:
                    in_str = False
        elif ch == "'":
            in_str = True
            out.append(ch)
        elif sql.startswith("--", i):
            while i < len(sql) and sql[i] != "\n":
                i += 1
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def split_top_level(text: str, sep: str) -> list[str]:
    """Split on ``sep`` outside parentheses and string literals."""
    parts, depth, in_str, buf = [], 0, False, []
    i = 0
    while i < len(text):
        ch = text[i]
        if in_str:
            buf.append(ch)
            if ch == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    buf.append("'")
                    i += 1
                else:
                    in_str = False
        elif ch == "'":
            in_str = True
            buf.append(ch)
        elif ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def matching_paren(text: str, open_idx: int) -> int:
    """Index of the parenthesis closing the one at ``open_idx``."""
    depth, in_str = 0, False
    for i in range(open_idx, len(text)):
        ch = text[i]
        if in_str:
            if ch == "'":
                in_str = False
        elif ch == "'":
            in_str = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("Unbalanced parentheses")


def _read_string_literal(text: str, start: int) -> tuple[str, int]:
    assert text[start] == "'"
    i, buf = start + 1, []
    while i < len(text):
        if text[i] == "'":
            if i + 1 < len(text) and text[i + 1] == "'":
                buf.append("'")
                i += 2
                continue
            return "".join(buf), i + 1
        buf.append(text[i])
        i += 1
    raise ValueError("Unterminated string literal")


# --------------------------------------------------------------------------- parsing


def _parse_spec(spec: str) -> tuple[list[Column], list[Expectation]]:
    columns, expectations = [], []
    for item in split_top_level(spec, ","):
        flat = " ".join(item.split())
        if flat.upper().startswith("CONSTRAINT"):
            m = _CONSTRAINT_RE.match(flat)
            if not m:
                raise ValueError(f"Cannot parse expectation: {flat}")
            action = (m.group(3) or "").upper().replace("  ", " ")
            expectations.append(
                Expectation(
                    name=m.group(1),
                    expression=m.group(2).strip(),
                    action={"": "warn", "DROP ROW": "drop", "FAIL UPDATE": "fail"}[action],
                )
            )
        else:
            m = _COLUMN_RE.match(flat)
            if not m:
                raise ValueError(f"Cannot parse column definition: {flat}")
            comment = m.group(3).replace("''", "'") if m.group(3) is not None else None
            columns.append(Column(m.group(1), m.group(2).upper(), comment))
    return columns, expectations


def parse_statement(stmt: str, source_file: Path) -> Model | None:
    m = _CREATE_RE.match(stmt)
    if not m:
        return None
    kind = "streaming_table" if "STREAMING" in m.group(1).upper() else "materialized_view"
    name = m.group(2)
    rest = stmt[m.end() :]

    columns: list[Column] = []
    expectations: list[Expectation] = []
    if rest.startswith("("):
        end = matching_paren(rest, 0)
        columns, expectations = _parse_spec(rest[1:end])
        rest = rest[end + 1 :]

    comment, props = None, {}
    while True:
        rest = rest.lstrip()
        upper = rest.upper()
        if upper.startswith("COMMENT"):
            q = rest.index("'")
            comment, nxt = _read_string_literal(rest, q)
            rest = rest[nxt:]
        elif upper.startswith("TBLPROPERTIES"):
            p = rest.index("(")
            end = matching_paren(rest, p)
            for kv in split_top_level(rest[p + 1 : end], ","):
                k, v = (s.strip().strip("'") for s in kv.split("=", 1))
                props[k] = v
            rest = rest[end + 1 :]
        elif re.match(r"AS\b", upper):
            query = rest[2:].strip()
            break
        else:
            raise ValueError(f"Unexpected clause in {name}: {rest[:40]!r}")

    return Model(name, kind, columns, expectations, comment, props, query, source_file)


def parse_file(path: Path) -> list[Model]:
    sql = strip_sql_comments(path.read_text())
    models = []
    for stmt in split_top_level(sql, ";"):
        model = parse_statement(stmt, path)
        if model is not None:
            models.append(model)
    return models


def table_references(query: str) -> set[str]:
    return {m.group(1) for m in _TABLE_REF_RE.finditer(query)}


def load_models(pipeline_dir: Path = PIPELINE_DIR) -> dict[str, Model]:
    """All pipeline models keyed by name, with upstream dependencies resolved."""
    models: dict[str, Model] = {}
    for path in sorted(pipeline_dir.rglob("*.sql")):
        for model in parse_file(path):
            if model.name in models:
                raise ValueError(f"Model {model.name} defined twice")
            models[model.name] = model
    for model in models.values():
        model.upstream = sorted(t for t in table_references(model.query) if t in models)
    return models


def topological_order(models: dict[str, Model]) -> list[str]:
    order, state = [], {}

    def visit(name: str) -> None:
        if state.get(name) == "done":
            return
        if state.get(name) == "visiting":
            raise ValueError(f"Cycle detected at {name}")
        state[name] = "visiting"
        for up in models[name].upstream:
            visit(up)
        state[name] = "done"
        order.append(name)

    for name in sorted(models):
        visit(name)
    return order
