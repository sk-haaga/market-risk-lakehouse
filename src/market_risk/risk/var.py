"""Value at Risk (VaR) and Expected Shortfall (ES) for a linear portfolio.

Conventions
-----------
* Exposures are EUR market values per risk factor; scenario P&L is ``returns @ exposures``
  (first-order, exact for the static linear positions in this project).
* VaR and ES are reported as positive EUR loss amounts over a 1-day horizon.
* Historical simulation: with ``n`` scenarios and confidence ``a``, the tail holds the
  ``k = ceil(n * (1 - a))`` worst losses. VaR is the k-th worst loss, ES is the mean of
  the k worst losses (so ES >= VaR by construction). n=250, a=99% -> k=3.
* Parametric methods assume zero-mean normal P&L: VaR = z * sigma,
  ES = sigma * pdf(z) / (1 - a), with sigma from either the equally weighted sample
  covariance or a RiskMetrics EWMA covariance (lambda = 0.94).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import norm

HISTORICAL = "historical"
PARAMETRIC_NORMAL = "parametric_normal"
PARAMETRIC_EWMA = "parametric_ewma"
METHODS = (HISTORICAL, PARAMETRIC_NORMAL, PARAMETRIC_EWMA)

METHOD_DESCRIPTIONS = {
    HISTORICAL: "Historical simulation: full revaluation of today's exposures under each of the last N daily returns.",
    PARAMETRIC_NORMAL: "Variance-covariance (delta-normal) with an equally weighted sample covariance over the last N days.",
    PARAMETRIC_EWMA: "Variance-covariance with a RiskMetrics EWMA covariance (lambda 0.94) over the last N days.",
}


@dataclass(frozen=True)
class RiskEstimate:
    var: float
    es: float
    contributions: np.ndarray  # per risk factor; sums to ES (historical) or VaR (parametric)


def _check_confidence(confidence: float) -> None:
    if not 0.5 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0.5, 1), got {confidence}")


def tail_size(n_scenarios: int, confidence: float) -> int:
    _check_confidence(confidence)
    # small epsilon so that e.g. 250 * 0.01 = 2.5000000000000004 does not round up twice
    return max(1, math.ceil(n_scenarios * (1.0 - confidence) - 1e-9))


def historical_var_es(pnl: np.ndarray, confidence: float) -> tuple[float, float]:
    """VaR and ES from a vector of scenario P&L (positive = profit)."""
    pnl = np.asarray(pnl, dtype=float)
    if pnl.ndim != 1 or pnl.size == 0 or not np.all(np.isfinite(pnl)):
        raise ValueError("pnl must be a non-empty 1-D array of finite values")
    k = tail_size(pnl.size, confidence)
    worst = np.sort(-pnl)[::-1][:k]  # largest losses first
    return float(worst[-1]), float(worst.mean())


def historical_estimate(exposures: np.ndarray, returns: np.ndarray, confidence: float) -> RiskEstimate:
    scen = returns @ exposures
    var, es = historical_var_es(scen, confidence)
    k = tail_size(scen.size, confidence)
    tail_idx = np.argsort(scen, kind="stable")[:k]
    # ES contribution of factor i: average loss from factor i in the tail scenarios
    contrib = -(returns[tail_idx] * exposures).mean(axis=0)
    return RiskEstimate(var, es, contrib)


def sample_covariance(returns: np.ndarray) -> np.ndarray:
    return np.atleast_2d(np.cov(returns, rowvar=False, ddof=1))


def ewma_covariance(returns: np.ndarray, lam: float = 0.94) -> np.ndarray:
    """Zero-mean EWMA covariance, most recent observation last, weights normalised to 1."""
    if not 0 < lam < 1:
        raise ValueError("lambda must be in (0, 1)")
    n = returns.shape[0]
    weights = (1 - lam) * lam ** np.arange(n - 1, -1, -1)
    weights /= weights.sum()
    return (returns * weights[:, None]).T @ returns


def parametric_estimate(exposures: np.ndarray, cov: np.ndarray, confidence: float) -> RiskEstimate:
    _check_confidence(confidence)
    sigma_e = cov @ exposures
    sigma = float(np.sqrt(max(exposures @ sigma_e, 0.0)))
    z = float(norm.ppf(confidence))
    var = z * sigma
    es = sigma * float(norm.pdf(z)) / (1.0 - confidence)
    # Euler allocation: contributions sum to VaR
    contrib = np.zeros_like(exposures) if sigma == 0 else z * exposures * sigma_e / sigma
    return RiskEstimate(var, es, contrib)


def estimate(
    method: str, exposures: np.ndarray, returns: np.ndarray, confidence: float, ewma_lambda: float = 0.94
) -> RiskEstimate:
    if method == HISTORICAL:
        return historical_estimate(exposures, returns, confidence)
    if method == PARAMETRIC_NORMAL:
        return parametric_estimate(exposures, sample_covariance(returns), confidence)
    if method == PARAMETRIC_EWMA:
        return parametric_estimate(exposures, ewma_covariance(returns, ewma_lambda), confidence)
    raise ValueError(f"Unknown method {method!r}; expected one of {METHODS}")


def rolling_var_es(
    returns: pd.DataFrame,
    exposures: pd.DataFrame,
    window: int = 250,
    confidence_levels: tuple[float, ...] = (0.99, 0.975),
    methods: tuple[str, ...] = METHODS,
    ewma_lambda: float = 0.94,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """1-day VaR / ES for every date that has ``window`` complete return days behind it.

    Parameters
    ----------
    returns:   simple EUR returns, index = date, columns = risk factors (no NaNs used).
    exposures: EUR exposures, index = as-of date, columns = risk factors.

    Returns
    -------
    (estimates, contributions): long DataFrames keyed by as_of_date, method, confidence.
    ``forecast_date`` is the next return date after ``as_of_date`` (NaT for the latest date).
    """
    factors = list(exposures.columns)
    missing = set(factors) - set(returns.columns)
    if missing:
        raise ValueError(f"No returns for risk factors {sorted(missing)}")
    rets = returns[factors].dropna().sort_index()
    exps = exposures[factors].sort_index()
    dates = rets.index
    rets_np = rets.to_numpy()

    est_rows, contrib_rows = [], []
    for as_of in exps.index:
        end = dates.searchsorted(as_of, side="right")  # returns up to and including as_of
        if end < window or (end > 0 and dates[end - 1] != as_of):
            continue  # not enough history, or as_of is not a complete return date
        sample = rets_np[end - window : end]
        e = exps.loc[as_of].to_numpy(dtype=float)
        if not np.all(np.isfinite(e)):
            continue
        forecast_date = dates[end] if end < len(dates) else pd.NaT
        for method in methods:
            for conf in confidence_levels:
                r = estimate(method, e, sample, conf, ewma_lambda)
                est_rows.append(
                    {
                        "as_of_date": as_of,
                        "forecast_date": forecast_date,
                        "method": method,
                        "confidence_level": conf,
                        "window_days": window,
                        "horizon_days": 1,
                        "portfolio_value_eur": float(e.sum()),
                        "gross_exposure_eur": float(np.abs(e).sum()),
                        "var_eur": r.var,
                        "es_eur": r.es,
                    }
                )
                measure = "ES" if method == HISTORICAL else "VaR"
                for f, c, ei in zip(factors, r.contributions, e, strict=True):
                    contrib_rows.append(
                        {
                            "as_of_date": as_of,
                            "method": method,
                            "confidence_level": conf,
                            "risk_factor_id": f,
                            "exposure_eur": float(ei),
                            "contribution_measure": measure,
                            "contribution_eur": float(c),
                        }
                    )

    estimates = pd.DataFrame(est_rows)
    contributions = pd.DataFrame(contrib_rows)
    if not estimates.empty:
        estimates["var_pct"] = estimates["var_eur"] / estimates["gross_exposure_eur"]
        estimates["es_pct"] = estimates["es_eur"] / estimates["gross_exposure_eur"]
        totals = estimates.set_index(["as_of_date", "method", "confidence_level"])
        key = ["as_of_date", "method", "confidence_level"]
        total = contributions.merge(totals[["var_eur", "es_eur"]].reset_index(), on=key, how="left")
        denom = np.where(total["contribution_measure"] == "ES", total["es_eur"], total["var_eur"])
        contributions["contribution_pct"] = np.where(denom != 0, total["contribution_eur"] / denom, 0.0)
    return estimates, contributions
