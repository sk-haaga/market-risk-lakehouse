"""Repository-wide contracts: every model is documented, tested and part of the lineage graph."""

import pytest

from market_risk.jobs.compute_risk import INPUT_TABLES, OUTPUT_TABLES
from market_risk.pipeline_sql import (
    load_models,
    split_top_level,
    strip_sql_comments,
    table_references,
    topological_order,
)
from market_risk.quality.checks import load_checks

MODELS = load_models()
CHECKS = load_checks()
ALL_TABLES = set(MODELS) | set(OUTPUT_TABLES)


def test_parser_helpers():
    assert strip_sql_comments("SELECT '--keep' -- drop\nFROM t") == "SELECT '--keep' \nFROM t"
    assert split_top_level("a, f(b, c), 'x,y'", ",") == ["a", "f(b, c)", "'x,y'"]
    assert table_references("SELECT * FROM a JOIN b ON 1=1 LEFT JOIN STREAM(c)") == {"a", "b", "c"}


def test_models_follow_medallion_naming():
    for name, model in MODELS.items():
        assert model.layer in {"bronze", "silver", "gold"}, name
        assert model.properties.get("quality") == model.layer, name


def test_dependency_graph_is_acyclic_and_layered():
    order = topological_order(MODELS)
    assert len(order) == len(MODELS)
    rank = {"bronze": 0, "silver": 1, "gold": 2}
    for model in MODELS.values():
        for up in model.upstream:
            assert rank[MODELS[up].layer] <= rank[model.layer], f"{model.name} reads {up}"
        if model.layer == "bronze":
            assert not model.upstream, f"bronze model {model.name} must read only landing files"


@pytest.mark.parametrize("name", sorted(MODELS))
def test_every_pipeline_model_is_documented(name):
    model = MODELS[name]
    assert model.comment and len(model.comment) > 20
    assert "Lineage" in model.comment or model.layer == "bronze"
    assert model.columns, "declare the column contract"
    undocumented = [c.name for c in model.columns if not c.comment]
    assert not undocumented, f"columns without COMMENT: {undocumented}"


@pytest.mark.parametrize("name", sorted(MODELS))
def test_every_pipeline_model_has_expectations(name):
    assert MODELS[name].expectations, f"{name} has no CONSTRAINT ... EXPECT"


@pytest.mark.parametrize("name", sorted(OUTPUT_TABLES))
def test_every_risk_table_is_documented(name):
    spec = OUTPUT_TABLES[name]
    assert spec.description
    assert all(comment for _, comment in spec.columns.values())
    assert set(spec.upstream) <= ALL_TABLES


def test_risk_job_inputs_exist():
    assert set(INPUT_TABLES) <= set(MODELS)


def test_checks_reference_known_tables():
    for chk in CHECKS:
        unknown = chk.referenced_tables() - ALL_TABLES
        assert not unknown, f"{chk.check_id} references {unknown}"


def test_every_non_bronze_table_has_post_load_checks():
    checked = {c.table for c in CHECKS}
    missing = {t for t in ALL_TABLES if not t.startswith("bronze_")} - checked
    assert not missing, f"tables without checks.yml entries: {sorted(missing)}"


def test_every_table_is_covered_by_a_test():
    covered = {c.table for c in CHECKS} | {m for m, model in MODELS.items() if model.expectations}
    assert covered == ALL_TABLES
