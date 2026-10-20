"""Job task 3: compute 1-day VaR / ES, risk contributions and the VaR backtest.

Reads the gold pipeline tables, runs the numpy risk engine per portfolio and writes five
documented gold tables (column comments + lineage table properties). The data volume is
small (years x a handful of factors), so the engine runs on the driver in pandas.

Databricks::

    compute_risk --catalog main --schema market_risk
"""

from __future__ import annotations

import argparse
import uuid
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from market_risk.jobs.common import fqn, get_logger, get_spark, utc_now
from market_risk.risk import backtest as bt
from market_risk.risk.var import METHOD_DESCRIPTIONS, METHODS, rolling_var_es

log = get_logger("compute_risk")


@dataclass(frozen=True)
class RiskConfig:
    window: int = 250
    confidence_levels: tuple[float, ...] = (0.99, 0.975)
    methods: tuple[str, ...] = METHODS
    ewma_lambda: float = 0.94


@dataclass(frozen=True)
class TableSpec:
    description: str
    upstream: tuple[str, ...]
    columns: dict[str, tuple[str, str]] = field(default_factory=dict)  # name -> (type, comment)

    @property
    def ddl(self) -> str:
        return ", ".join(f"{c} {t}" for c, (t, _) in self.columns.items())


INPUT_TABLES = (
    "gold_fact_risk_factor_returns",
    "gold_fact_position_exposure_daily",
    "gold_fact_portfolio_pnl_daily",
)

_KEY = {
    "portfolio_id": ("STRING", "Portfolio"),
    "as_of_date": ("DATE", "Date the forecast is made (end of day), using returns up to this date"),
    "method": ("STRING", "Risk model (FK gold_dim_risk_method)"),
    "confidence_level": ("DOUBLE", "VaR / ES confidence level, e.g. 0.99"),
}

OUTPUT_TABLES: dict[str, TableSpec] = {
    "gold_dim_risk_method": TableSpec(
        "Risk model dimension: the VaR / ES methods compared in the backtest.",
        (),
        {
            "method": ("STRING", "Method id (primary key)"),
            "method_label": ("STRING", "Display label"),
            "description": ("STRING", "How the method works"),
            "window_days": ("INT", "Look-back window in trading days"),
            "ewma_lambda": ("DOUBLE", "EWMA decay (only for parametric_ewma)"),
        },
    ),
    "gold_fact_var_daily": TableSpec(
        "Daily 1-day VaR and Expected Shortfall per portfolio, method and confidence level.",
        ("gold_fact_risk_factor_returns", "gold_fact_position_exposure_daily"),
        {
            **_KEY,
            "forecast_date": ("DATE", "Next trading date the forecast applies to (null for the latest as_of_date)"),
            "window_days": ("INT", "Number of daily return scenarios used"),
            "horizon_days": ("INT", "Risk horizon in days (1)"),
            "portfolio_value_eur": ("DOUBLE", "Net market value of the portfolio in EUR"),
            "gross_exposure_eur": ("DOUBLE", "Sum of absolute exposures in EUR"),
            "var_eur": ("DOUBLE", "Value at Risk, positive EUR loss"),
            "es_eur": ("DOUBLE", "Expected Shortfall (average loss beyond VaR), positive EUR"),
            "var_pct": ("DOUBLE", "VaR / gross exposure"),
            "es_pct": ("DOUBLE", "ES / gross exposure"),
            "computed_at": ("TIMESTAMP", "UTC time the risk job ran"),
            "run_id": ("STRING", "Risk job run identifier"),
        },
    ),
    "gold_fact_risk_contribution": TableSpec(
        "Risk factor contributions: ES contributions for historical simulation, Euler VaR contributions for parametric methods. Contributions sum to the portfolio total.",
        ("gold_fact_risk_factor_returns", "gold_fact_position_exposure_daily"),
        {
            **_KEY,
            "risk_factor_id": ("STRING", "Risk factor (FK gold_dim_risk_factor)"),
            "exposure_eur": ("DOUBLE", "Portfolio exposure to the risk factor in EUR"),
            "contribution_measure": ("STRING", "ES (historical) or VaR (parametric)"),
            "contribution_eur": ("DOUBLE", "Contribution to the measure in EUR"),
            "contribution_pct": ("DOUBLE", "Share of the portfolio measure"),
        },
    ),
    "gold_fact_var_backtest": TableSpec(
        "VaR forecasts made at as_of_date compared with the realised hypothetical P&L on forecast_date.",
        ("gold_fact_var_daily", "gold_fact_portfolio_pnl_daily"),
        {
            **_KEY,
            "forecast_date": ("DATE", "Date of the realised P&L"),
            "var_eur": ("DOUBLE", "VaR forecast, positive EUR"),
            "es_eur": ("DOUBLE", "ES forecast, positive EUR"),
            "pnl_eur": ("DOUBLE", "Realised hypothetical P&L on forecast_date"),
            "loss_eur": ("DOUBLE", "-pnl_eur"),
            "is_breach": ("BOOLEAN", "Loss exceeded VaR"),
            "excess_loss_eur": ("DOUBLE", "Loss beyond VaR on breach days, else 0"),
            "breaches_last_250d": ("INT", "Breaches in the trailing 250 backtest days"),
            "traffic_light_zone": ("STRING", "Basel zone on the trailing 250 days: GREEN, YELLOW, RED or WARMING_UP"),
        },
    ),
    "gold_backtest_summary": TableSpec(
        "Backtest statistics per portfolio, method and confidence level over the full history.",
        ("gold_fact_var_backtest",),
        {
            "portfolio_id": _KEY["portfolio_id"],
            "method": _KEY["method"],
            "confidence_level": _KEY["confidence_level"],
            "start_date": ("DATE", "First forecast_date in the backtest"),
            "end_date": ("DATE", "Last forecast_date in the backtest"),
            "n_observations": ("INT", "Backtest days"),
            "n_breaches": ("INT", "Days the loss exceeded VaR"),
            "expected_breaches": ("DOUBLE", "n_observations * (1 - confidence_level)"),
            "breach_rate": ("DOUBLE", "n_breaches / n_observations"),
            "kupiec_lr": ("DOUBLE", "Kupiec POF likelihood ratio (chi2, 1 df)"),
            "kupiec_pvalue": ("DOUBLE", "Kupiec POF p-value"),
            "christoffersen_lr": ("DOUBLE", "Christoffersen independence likelihood ratio (chi2, 1 df)"),
            "christoffersen_pvalue": ("DOUBLE", "Christoffersen independence p-value"),
            "conditional_coverage_lr": ("DOUBLE", "Kupiec + Christoffersen (chi2, 2 df)"),
            "conditional_coverage_pvalue": ("DOUBLE", "Conditional coverage p-value"),
            "breaches_last_250d": ("INT", "Breaches in the latest 250 days"),
            "traffic_light_zone": ("STRING", "Basel zone for the latest 250 days"),
            "es_ratio": ("DOUBLE", "Mean loss on breach days / mean ES on those days"),
            "model_result": ("STRING", "ACCEPT if Kupiec and conditional coverage pass at 5%, else REJECT"),
            "computed_at": ("TIMESTAMP", "UTC time the risk job ran"),
            "run_id": ("STRING", "Risk job run identifier"),
        },
    ),
}

METHOD_LABELS = {
    "historical": "Historical simulation",
    "parametric_normal": "Parametric (normal)",
    "parametric_ewma": "Parametric (EWMA)",
}


def prepare_inputs(
    returns_long: pd.DataFrame, exposures_long: pd.DataFrame, pnl_long: pd.DataFrame, portfolio_id: str
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    exp = exposures_long[exposures_long["portfolio_id"] == portfolio_id]
    exp_wide = (
        exp.assign(as_of_date=pd.to_datetime(exp["as_of_date"]))
        .pivot_table(index="as_of_date", columns="risk_factor_id", values="exposure_eur", aggfunc="sum")
        .sort_index()
    )
    factors = list(exp_wide.columns)
    rets = returns_long[returns_long["risk_factor_id"].isin(factors)]
    ret_wide = (
        rets.assign(price_date=pd.to_datetime(rets["price_date"]))
        .pivot(index="price_date", columns="risk_factor_id", values="simple_return")
        .reindex(columns=factors)
        .sort_index()
    )
    pnl = pnl_long[pnl_long["portfolio_id"] == portfolio_id]
    pnl_s = pd.Series(pnl["pnl_eur"].to_numpy(), index=pd.to_datetime(pnl["pnl_date"]), name="pnl_eur").sort_index()
    return ret_wide, exp_wide, pnl_s


def compute(
    returns_long: pd.DataFrame,
    exposures_long: pd.DataFrame,
    pnl_long: pd.DataFrame,
    config: RiskConfig | None = None,
    computed_at: datetime | None = None,
    run_id: str | None = None,
) -> dict[str, pd.DataFrame]:
    config = config or RiskConfig()
    computed_at = computed_at or utc_now()
    run_id = run_id or uuid.uuid4().hex[:12]
    est_all, contrib_all, bt_all, summary_all = [], [], [], []

    for portfolio_id in sorted(exposures_long["portfolio_id"].unique()):
        rets, exps, pnl = prepare_inputs(returns_long, exposures_long, pnl_long, portfolio_id)
        est, contrib = rolling_var_es(
            rets, exps, config.window, config.confidence_levels, config.methods, config.ewma_lambda
        )
        if est.empty:
            log.warning("%s: fewer than %s complete return days, skipping", portfolio_id, config.window)
            continue
        backtest = bt.align_with_pnl(est, pnl)
        summary = bt.summarize(backtest)
        for df in (est, contrib, backtest, summary):
            df.insert(0, "portfolio_id", portfolio_id)
        est_all.append(est)
        contrib_all.append(contrib)
        bt_all.append(backtest)
        summary_all.append(summary)
        log.info("%s: %s VaR rows, %s backtest rows", portfolio_id, len(est), len(backtest))

    if not est_all:
        raise RuntimeError(f"Not enough history to compute VaR (need {config.window} return days)")

    est = pd.concat(est_all, ignore_index=True).assign(computed_at=computed_at, run_id=run_id)
    summary = pd.concat(summary_all, ignore_index=True).assign(computed_at=computed_at, run_id=run_id)
    methods = pd.DataFrame(
        {
            "method": list(config.methods),
            "method_label": [METHOD_LABELS[m] for m in config.methods],
            "description": [METHOD_DESCRIPTIONS[m] for m in config.methods],
            "window_days": config.window,
            "ewma_lambda": [config.ewma_lambda if m == "parametric_ewma" else np.nan for m in config.methods],
        }
    )
    outputs = {
        "gold_dim_risk_method": methods,
        "gold_fact_var_daily": est,
        "gold_fact_risk_contribution": pd.concat(contrib_all, ignore_index=True),
        "gold_fact_var_backtest": pd.concat(bt_all, ignore_index=True),
        "gold_backtest_summary": summary,
    }
    outputs = {name: conform(df, OUTPUT_TABLES[name]) for name, df in outputs.items()}
    validate(outputs)
    return outputs


def conform(df: pd.DataFrame, spec: TableSpec) -> pd.DataFrame:
    """Select and order the declared columns and coerce dates to python dates (None for NaT)."""
    missing = set(spec.columns) - set(df.columns)
    if missing:
        raise ValueError(f"Output is missing declared columns {sorted(missing)}")
    out = df[list(spec.columns)].copy()
    for col, (typ, _) in spec.columns.items():
        if typ == "DATE":
            s = pd.to_datetime(out[col])
            out[col] = [d.date() if not pd.isna(d) else None for d in s]
        elif typ == "INT":
            out[col] = out[col].astype("int64")
        elif typ == "BOOLEAN":
            out[col] = out[col].astype(bool)
    return out.reset_index(drop=True)


def validate(outputs: dict[str, pd.DataFrame], tol: float = 1e-6) -> None:
    """Invariants that must hold before anything is written."""
    var = outputs["gold_fact_var_daily"]
    if (var["var_eur"] < 0).any() or not np.isfinite(var["var_eur"]).all():
        raise ValueError("VaR must be finite and non-negative")
    if (var["es_eur"] + tol < var["var_eur"]).any():
        raise ValueError("ES must be >= VaR")
    key = ["portfolio_id", "as_of_date", "method", "confidence_level"]
    if var.duplicated(key).any():
        raise ValueError("Duplicate VaR rows")

    contrib = outputs["gold_fact_risk_contribution"]
    sums = contrib.groupby(key)["contribution_eur"].sum().rename("sum").reset_index()
    chk = sums.merge(var, on=key)
    target = np.where(chk["method"] == "historical", chk["es_eur"], chk["var_eur"])
    if not np.allclose(chk["sum"], target, rtol=1e-6, atol=1e-2):
        raise ValueError("Risk contributions do not add up to the portfolio measure")

    bt_df = outputs["gold_fact_var_backtest"]
    if (bt_df["is_breach"] != (bt_df["loss_eur"] > bt_df["var_eur"])).any():
        raise ValueError("Breach flag inconsistent with loss and VaR")


# --------------------------------------------------------------------------- Spark I/O


def read_inputs(spark, catalog: str | None, schema: str | None) -> tuple[pd.DataFrame, ...]:
    def q(t: str) -> str:
        return fqn(catalog, schema, t)

    returns = spark.sql(
        f"SELECT price_date, risk_factor_id, simple_return FROM {q('gold_fact_risk_factor_returns')}"
    ).toPandas()
    exposures = spark.sql(
        f"""SELECT as_of_date, portfolio_id, risk_factor_id, sum(exposure_eur) AS exposure_eur
            FROM {q("gold_fact_position_exposure_daily")}
            GROUP BY as_of_date, portfolio_id, risk_factor_id"""
    ).toPandas()
    pnl = spark.sql(f"SELECT pnl_date, portfolio_id, pnl_eur FROM {q('gold_fact_portfolio_pnl_daily')}").toPandas()
    return returns, exposures, pnl


def _sql_str(s: str) -> str:
    return "'" + s.replace("'", "\\'") + "'"


def write_outputs(
    spark, outputs: dict[str, pd.DataFrame], catalog: str | None, schema: str | None, as_temp_views: bool = False
) -> None:
    for name, pdf in outputs.items():
        spec = OUTPUT_TABLES[name]
        sdf = spark.createDataFrame(pdf, schema=spec.ddl)
        if as_temp_views:
            sdf.createOrReplaceTempView(name)
            continue
        target = fqn(catalog, schema, name)
        sdf.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(target)
        upstream = ",".join(fqn(catalog, schema, u) for u in spec.upstream) or "none"
        spark.sql(f"COMMENT ON TABLE {target} IS {_sql_str(spec.description)}")
        spark.sql(
            f"ALTER TABLE {target} SET TBLPROPERTIES ("
            f"'quality' = 'gold', 'producer' = 'market_risk.jobs.compute_risk', "
            f"'lineage.upstream' = {_sql_str(upstream)})"
        )
        for col, (_, comment) in spec.columns.items():
            spark.sql(f"ALTER TABLE {target} ALTER COLUMN {col} COMMENT {_sql_str(comment)}")
        log.info("Wrote %s (%s rows)", target, len(pdf))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--catalog", required=True)
    p.add_argument("--schema", required=True)
    p.add_argument("--window", type=int, default=250)
    p.add_argument("--confidence-levels", default="0.99,0.975")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    config = RiskConfig(
        window=args.window,
        confidence_levels=tuple(float(c) for c in args.confidence_levels.split(",")),
    )
    spark = get_spark()
    outputs = compute(*read_inputs(spark, args.catalog, args.schema), config=config)
    write_outputs(spark, outputs, args.catalog, args.schema)


if __name__ == "__main__":
    main()
