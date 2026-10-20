import math

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from market_risk.risk import backtest as bt
from market_risk.risk import var as v


def test_kupiec_matches_textbook_formula():
    n, x, p = 250, 8, 0.01
    phat = x / n
    expected = -2 * ((n - x) * math.log(1 - p) + x * math.log(p) - (n - x) * math.log(1 - phat) - x * math.log(phat))
    lr, pval = bt.kupiec_pof(n, x, p)
    assert lr == pytest.approx(expected)
    assert 0 < pval < 0.05  # 8 breaches vs 2.5 expected is rejected


def test_kupiec_edge_cases():
    lr0, p0 = bt.kupiec_pof(250, 0, 0.01)
    assert lr0 == pytest.approx(-2 * 250 * math.log(0.99))
    assert 0 < p0 < 1
    lr_exact, p_exact = bt.kupiec_pof(1000, 10, 0.01)
    assert lr_exact == pytest.approx(0.0, abs=1e-12) and p_exact == pytest.approx(1.0)
    with pytest.raises(ValueError):
        bt.kupiec_pof(10, 11, 0.01)


def test_christoffersen_detects_clustering():
    clustered = np.zeros(500, dtype=bool)
    clustered[100:110] = True  # 10 consecutive breaches
    spread = np.zeros(500, dtype=bool)
    spread[::50] = True  # 10 isolated breaches
    _, p_clustered = bt.christoffersen_independence(clustered)
    _, p_spread = bt.christoffersen_independence(spread)
    assert p_clustered < 0.01
    assert p_spread > 0.05


@pytest.mark.parametrize("x,zone", [(0, "GREEN"), (4, "GREEN"), (5, "YELLOW"), (9, "YELLOW"), (10, "RED")])
def test_basel_traffic_light(x, zone):
    assert bt.traffic_light_zone(x, 250, 0.01) == zone


def _backtest(true_dist: str, n=1500, seed=11):
    """Single-asset backtest where the risk model is a normal VaR with the true volatility."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=n + 1)
    if true_dist == "normal":
        r = rng.normal(0, 0.01, n + 1)
    else:  # Student-t with 3 dof, scaled to the same 1% volatility
        r = rng.standard_t(3, n + 1) * 0.01 / math.sqrt(3)
    exposure = 1e6
    var99 = norm.ppf(0.99) * 0.01 * exposure
    est = pd.DataFrame(
        {
            "as_of_date": dates[:-1],
            "forecast_date": dates[1:],
            "method": "parametric_normal",
            "confidence_level": 0.99,
            "var_eur": var99,
            "es_eur": 0.01 * exposure * norm.pdf(norm.ppf(0.99)) / 0.01,
        }
    )
    pnl = pd.Series(r[1:] * exposure, index=dates[1:])
    return bt.align_with_pnl(est, pnl)


def test_backtest_accepts_correct_model():
    detail = _backtest("normal")
    summary = bt.summarize(detail)
    assert summary["model_result"].item() == "ACCEPT"
    assert summary["n_breaches"].item() == int(detail["is_breach"].sum())


def test_backtest_rejects_normal_var_on_fat_tails():
    summary = bt.summarize(_backtest("student_t", n=8000))
    # same volatility, fatter tails: normal 99% VaR is breached too often ...
    assert summary["breach_rate"].item() > 0.015
    assert summary["model_result"].item() == "REJECT"
    # ... and the normal ES understates how bad the tail losses are
    assert summary["es_ratio"].item() > 1.2


def test_align_flags_breaches_and_rolling_counts():
    detail = _backtest("normal", n=300)
    assert (detail["is_breach"] == (detail["loss_eur"] > detail["var_eur"])).all()
    assert (detail.loc[~detail.is_breach, "excess_loss_eur"] == 0).all()
    assert (detail.loc[detail.is_breach, "excess_loss_eur"] > 0).all()
    assert detail["traffic_light_zone"].iloc[:249].eq("WARMING_UP").all()
    assert detail["traffic_light_zone"].iloc[249:].isin(["GREEN", "YELLOW", "RED"]).all()
    expected_last = int(detail["is_breach"].iloc[-250:].sum())
    assert detail["breaches_last_250d"].iloc[-1] == expected_last


def test_end_to_end_engine_with_backtest():
    rng = np.random.default_rng(2)
    dates = pd.bdate_range("2023-01-02", periods=600)
    rets = pd.DataFrame(rng.normal(0, 0.01, size=(600, 2)), index=dates, columns=["X", "Y"])
    exps = pd.DataFrame({"X": 1e6, "Y": 5e5}, index=dates)
    pnl = (rets * exps.shift(1)).sum(axis=1).iloc[1:]
    est, _ = v.rolling_var_es(rets, exps, window=250)
    detail = bt.align_with_pnl(est, pnl)
    summary = bt.summarize(detail)
    assert len(summary) == len(v.METHODS) * 2
    assert set(summary["model_result"]) <= {"ACCEPT", "REJECT"}
    # 350 forecasts per series: 600 dates - 250 window, minus the open latest forecast
    assert (summary["n_observations"] == 350).all()
