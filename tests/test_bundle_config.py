"""The Databricks bundle must deploy every pipeline file and call real entry points."""

import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    return yaml.safe_load((ROOT / "resources" / name).read_text())


def test_pipeline_includes_every_sql_file():
    pipeline = _load("market_risk.pipeline.yml")["resources"]["pipelines"]["market_risk_pipeline"]
    declared = {(ROOT / "resources" / lib["file"]["path"]).resolve() for lib in pipeline["libraries"]}
    on_disk = {p.resolve() for p in (ROOT / "src" / "pipeline").rglob("*.sql")}
    assert declared == on_disk
    assert pipeline["serverless"] is True
    assert "landing_root" in pipeline["configuration"]


def test_job_entry_points_exist():
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    job = _load("market_risk.job.yml")["resources"]["jobs"]["market_risk_daily"]
    entry_points = [t["python_wheel_task"]["entry_point"] for t in job["tasks"] if "python_wheel_task" in t]
    assert entry_points == ["ingest_market_data", "compute_risk", "run_quality_checks"]
    assert set(entry_points) <= set(scripts)


def test_job_task_order():
    job = _load("market_risk.job.yml")["resources"]["jobs"]["market_risk_daily"]
    deps = {t["task_key"]: [d["task_key"] for d in t.get("depends_on", [])] for t in job["tasks"]}
    assert deps == {
        "ingest_market_data": [],
        "refresh_pipeline": ["ingest_market_data"],
        "compute_risk": ["refresh_pipeline"],
        "data_quality_checks": ["compute_risk"],
    }
