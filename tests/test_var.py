import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from market_risk.risk import var as v


def test_tail_size():
    assert v.tail_size(250, 0.99) == 3
    assert v.tail_size(100, 0.99) == 1
    assert v.tail_size(1000, 0.975) == 25
    assert v.tail_size(10, 0.99) == 1
    with pytest.raises(ValueError):
        v.tail_size(250, 1.0)


def test_historical_var_es_known_answer():
    pnl = -np.arange(1, 251, dtype=float)  # losses 1..250
    var, es = v.historical_var_es(pnl, 0.99)
    assert var == 248.0  # 3rd worst loss
    assert es == pytest.approx((250 + 249 + 248) / 3)


def test_historical_is_order_invariant():
    rng = np.random.default_rng(1)
    pnl = rng.normal(size=500)
    assert v.historical_var_es(pnl, 0.99) == v.historical_var_es(rng.permutation(pnl), 0.99)


def test_historical_rejects_bad_input():
    with pytest.raises(ValueError):
        v.historical_var_es(np.array([]), 0.99)
    with pytest.raises(ValueError):
        v.historical_var_es(np.array([1.0, np.nan]), 0.99)


def test_parametric_single_asset_matches_closed_form():
    exposure = np.array([1_000_000.0])
    sigma = 0.01
    est = v.parametric_estimate(exposure, np.array([[sigma**2]]), 0.99)
    z = norm.ppf(0.99)
    assert est.var == pytest.approx(z * sigma * 1_000_000)
    assert est.es == pytest.approx(sigma * 1_000_000 * norm.pdf(z) / 0.01)
    assert est.es > est.var


def test_parametric_diversification():
    e = np.array([1.0, 1.0])
    cov_perfect = np.array([[1e-4, 1e-4], [1e-4, 1e-4]])
    cov_hedge = np.array([[1e-4, -1e-4], [-1e-4, 1e-4]])
    assert v.parametric_estimate(e, cov_perfect, 0.99).var == pytest.approx(2 * norm.ppf(0.99) * 0.01)
    assert v.parametric_estimate(e, cov_hedge, 0.99).var == pytest.approx(0.0, abs=1e-12)


def test_ewma_covariance_weights_recent_data_more():
    calm = np.full((200, 1), 0.001)
    stressed = np.full((5, 1), 0.05)
    recent_stress = np.vstack([calm, stressed])
    old_stress = np.vstack([stressed, calm])
    assert v.ewma_covariance(recent_stress)[0, 0] > v.ewma_covariance(old_stress)[0, 0]
    # constant returns: EWMA variance equals the squared return
    assert v.ewma_covariance(calm)[0, 0] == pytest.approx(0.001**2)


@pytest.mark.parametrize("method", v.METHODS)
def test_contributions_add_up(method):
    rng = np.random.default_rng(3)
    returns = rng.standard_t(4, size=(250, 4)) * 0.01
    e = np.array([2e6, -5e5, 1e6, 3e5])
    est = v.estimate(method, e, returns, 0.99)
    target = est.es if method == v.HISTORICAL else est.var
    assert est.contributions.sum() == pytest.approx(target, rel=1e-9)
    assert est.es >= est.var - 1e-9


def _frames(n_days=300, seed=5):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n_days)
    rets = pd.DataFrame(rng.normal(0, 0.01, size=(n_days, 2)), index=dates, columns=["A", "B"])
    exps = pd.DataFrame({"A": 1e6, "B": -4e5}, index=dates)
    return rets, exps


def test_rolling_var_es_shape_and_alignment():
    rets, exps = _frames()
    est, contrib = v.rolling_var_es(rets, exps, window=250, confidence_levels=(0.99,))
    # first estimate when 250 returns are available, one per method per date
    first = rets.index[249]
    assert est["as_of_date"].min() == first
    assert len(est) == (300 - 249) * len(v.METHODS)
    # forecast date is the next return date; the latest forecast is still open
    row = est[(est.as_of_date == first) & (est.method == v.HISTORICAL)].iloc[0]
    assert row.forecast_date == rets.index[250]
    assert est.loc[est.as_of_date == rets.index[-1], "forecast_date"].isna().all()
    assert len(contrib) == len(est) * 2
    assert (est["es_eur"] >= est["var_eur"] - 1e-9).all()


def test_rolling_uses_only_past_returns():
    rets, exps = _frames()
    est1, _ = v.rolling_var_es(rets, exps, window=250, confidence_levels=(0.99,), methods=(v.HISTORICAL,))
    shocked = rets.copy()
    shocked.iloc[-1] = -0.5  # a crash on the last day must not change earlier forecasts
    est2, _ = v.rolling_var_es(shocked, exps, window=250, confidence_levels=(0.99,), methods=(v.HISTORICAL,))
    earlier = est1.as_of_date < rets.index[-1]
    pd.testing.assert_series_equal(est1.loc[earlier, "var_eur"], est2.loc[earlier, "var_eur"])
    assert est2["var_eur"].iloc[-1] > est1["var_eur"].iloc[-1]


def test_rolling_requires_returns_for_every_factor():
    rets, exps = _frames()
    with pytest.raises(ValueError, match="No returns"):
        v.rolling_var_es(rets[["A"]], exps)


def test_unknown_method():
    with pytest.raises(ValueError):
        v.estimate("monte_carlo", np.ones(1), np.ones((10, 1)), 0.99)
