"""Integration tests: run the real pipeline SQL, risk job and checks on local Spark."""

import shutil

import pandas as pd
import pytest

from market_risk import sample_data
from market_risk.local_pipeline import ExpectationFailed, run_pipeline
from market_risk.local_run import run

pytestmark = pytest.mark.spark


@pytest.fixture(scope="module")
def synthetic():
    return sample_data.build(inject_anomalies=True)


@pytest.fixture(scope="module")
def result(spark, synthetic, tmp_path_factory):
    landing = tmp_path_factory.mktemp("landing")
    sample_data.write_landing(landing, synthetic)
    return run(spark, landing)


def test_all_post_load_checks_pass(result):
    failed = [(r.check_id, r.status, r.failing_rows, r.error) for r in result.checks if r.status != "PASS"]
    assert not failed


def test_bad_records_are_dropped_by_expectations(result):
    f = result.pipeline.failures()
    assert f["silver_fx_rates.fx_rate_positive"] == 1  # negative XYZ rate
    assert f["silver_index_prices.px_mapped_to_risk_factor"] == 2  # unknown series + wrong currency


def test_latest_ingestion_wins(spark, synthetic, result):
    rev = synthetic.revised
    row = spark.sql(
        f"SELECT fx_rate FROM silver_fx_rates WHERE currency = 'USD' AND rate_date = DATE'{rev['obs_date']}'"
    ).collect()
    assert len(row) == 1 and row[0]["fx_rate"] == pytest.approx(rev["new_value"])


def test_no_weekend_prices_and_holidays_filled(spark, result):
    stats = spark.sql(
        """SELECT count_if(dayofweek(price_date) IN (1, 7)) AS weekend,
                  count_if(is_filled) AS filled,
                  max(local_price_age_days) AS max_age
           FROM gold_fact_market_prices_eur"""
    ).collect()[0]
    assert stats["weekend"] == 0
    assert stats["filled"] > 0  # market holidays were carried forward
    assert stats["max_age"] <= 10


def test_eur_conversion(spark, result):
    row = spark.sql(
        """SELECT m.price_eur, m.local_price, f.fx_rate
           FROM gold_fact_market_prices_eur m
           JOIN silver_fx_rates f ON f.currency = 'USD' AND f.rate_date = m.price_date
           WHERE m.risk_factor_id = 'EQ_SPX' AND NOT m.is_filled
           ORDER BY m.price_date LIMIT 1"""
    ).collect()[0]
    assert row["price_eur"] == pytest.approx(row["local_price"] / row["fx_rate"])


def test_risk_outputs(result):
    var = result.risk_outputs["gold_fact_var_daily"]
    assert set(var["method"]) == {"historical", "parametric_normal", "parametric_ewma"}
    assert (var["es_eur"] >= var["var_eur"]).all()
    summary = result.risk_outputs["gold_backtest_summary"]
    assert len(summary) == 6
    assert (summary["n_observations"] > 400).all()


def test_backtest_pnl_is_gold_pnl(spark, result):
    gold = spark.table("gold_fact_portfolio_pnl_daily").toPandas().set_index("pnl_date")["pnl_eur"]
    detail = result.risk_outputs["gold_fact_var_backtest"]
    one = detail[(detail.method == "historical") & (detail.confidence_level == 0.99)]
    bt = one.set_index("forecast_date")["pnl_eur"]
    joined = pd.concat([gold, bt], axis=1, join="inner").dropna()
    assert len(joined) > 400
    assert (joined.iloc[:, 0] - joined.iloc[:, 1]).abs().max() < 1e-6


def test_broken_reference_data_fails_the_update(spark, synthetic, tmp_path):
    sample_data.write_landing(tmp_path, synthetic)
    positions = tmp_path / "reference/positions/portfolio_positions.csv"
    df = pd.read_csv(positions)
    df.loc[0, "risk_factor_id"] = "EQ_DOES_NOT_EXIST"
    df.to_csv(positions, index=False)
    with pytest.raises(ExpectationFailed, match="pos_known_risk_factor"):
        run_pipeline(spark, tmp_path)
    shutil.rmtree(tmp_path, ignore_errors=True)
