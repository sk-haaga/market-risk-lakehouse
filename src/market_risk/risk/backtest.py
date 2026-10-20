"""VaR backtesting: does the model hold up against realised (hypothetical) P&L?

* A **breach** (exception) on day t+1 is a loss larger than the VaR forecast made at t.
* **Kupiec POF** (proportion of failures) tests whether the breach rate equals 1 - a.
* **Christoffersen independence** tests whether breaches cluster (a breach today makes
  one tomorrow more likely); **conditional coverage** combines both (chi-square, 2 df).
* **Basel traffic light** classifies the number of breaches in the last 250 days using
  the cumulative binomial probability: green < 95%, yellow < 99.99%, red otherwise
  (at 99% / 250 days: green 0-4, yellow 5-9, red 10+).
* **ES ratio**: average loss on breach days divided by the average ES forecast on those
  days; close to 1 means ES describes the tail well, well above 1 means it understates it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.special import xlogy
from scipy.stats import binom, chi2

TRAFFIC_LIGHT_WINDOW = 250


def _log_lik(n_fail: float, n_ok: float, p: float) -> float:
    """Bernoulli log-likelihood with 0 * log(0) = 0."""
    return float(xlogy(n_fail, p) + xlogy(n_ok, 1.0 - p))


def kupiec_pof(n_obs: int, n_breaches: int, expected_rate: float) -> tuple[float, float]:
    """Likelihood ratio and p-value of Kupiec's proportion-of-failures test."""
    if n_obs <= 0:
        return float("nan"), float("nan")
    if not 0 <= n_breaches <= n_obs:
        raise ValueError("n_breaches must be between 0 and n_obs")
    observed = n_breaches / n_obs
    lr = -2.0 * (
        _log_lik(n_breaches, n_obs - n_breaches, expected_rate) - _log_lik(n_breaches, n_obs - n_breaches, observed)
    )
    lr = max(lr, 0.0)
    return lr, float(chi2.sf(lr, df=1))


def christoffersen_independence(breaches: np.ndarray) -> tuple[float, float]:
    """Likelihood ratio and p-value of Christoffersen's Markov independence test."""
    b = np.asarray(breaches, dtype=bool)
    if b.size < 2:
        return float("nan"), float("nan")
    prev, curr = b[:-1], b[1:]
    n00 = int(np.sum(~prev & ~curr))
    n01 = int(np.sum(~prev & curr))
    n10 = int(np.sum(prev & ~curr))
    n11 = int(np.sum(prev & curr))
    pi = (n01 + n11) / (n00 + n01 + n10 + n11)
    pi01 = n01 / (n00 + n01) if (n00 + n01) else 0.0
    pi11 = n11 / (n10 + n11) if (n10 + n11) else 0.0
    ll_null = _log_lik(n01 + n11, n00 + n10, pi)
    ll_alt = _log_lik(n01, n00, pi01) + _log_lik(n11, n10, pi11)
    lr = max(-2.0 * (ll_null - ll_alt), 0.0)
    return lr, float(chi2.sf(lr, df=1))


def traffic_light_zone(n_breaches: int, n_obs: int = TRAFFIC_LIGHT_WINDOW, expected_rate: float = 0.01) -> str:
    cum_prob = float(binom.cdf(n_breaches, n_obs, expected_rate))
    if cum_prob < 0.95:
        return "GREEN"
    if cum_prob < 0.9999:
        return "YELLOW"
    return "RED"


def align_with_pnl(estimates: pd.DataFrame, pnl: pd.Series) -> pd.DataFrame:
    """Join each VaR forecast (made at as_of_date) to the realised P&L on forecast_date."""
    realised = pnl.rename("pnl_eur").rename_axis("forecast_date").reset_index()
    bt = estimates.dropna(subset=["forecast_date"]).merge(realised, on="forecast_date", how="inner")
    bt["loss_eur"] = -bt["pnl_eur"]
    bt["is_breach"] = bt["loss_eur"] > bt["var_eur"]
    bt["excess_loss_eur"] = np.where(bt["is_breach"], bt["loss_eur"] - bt["var_eur"], 0.0)
    bt = bt.sort_values(["method", "confidence_level", "forecast_date"]).reset_index(drop=True)

    grp = bt.groupby(["method", "confidence_level"], sort=False)["is_breach"]
    bt["breaches_last_250d"] = grp.transform(
        lambda s: s.astype(int).rolling(TRAFFIC_LIGHT_WINDOW, min_periods=1).sum()
    ).astype(int)
    bt["obs_last_250d"] = grp.cumcount().clip(upper=TRAFFIC_LIGHT_WINDOW - 1) + 1
    bt["traffic_light_zone"] = [
        traffic_light_zone(int(x), int(n), 1 - c) if n == TRAFFIC_LIGHT_WINDOW else "WARMING_UP"
        for x, n, c in zip(bt["breaches_last_250d"], bt["obs_last_250d"], bt["confidence_level"], strict=True)
    ]
    return bt


def summarize(backtest: pd.DataFrame, significance: float = 0.05) -> pd.DataFrame:
    rows = []
    for (method, conf), g in backtest.groupby(["method", "confidence_level"], sort=True):
        g = g.sort_values("forecast_date")
        n, x = len(g), int(g["is_breach"].sum())
        p = 1.0 - conf
        lr_pof, p_pof = kupiec_pof(n, x, p)
        lr_ind, p_ind = christoffersen_independence(g["is_breach"].to_numpy())
        lr_cc = lr_pof + lr_ind
        p_cc = float(chi2.sf(lr_cc, df=2)) if np.isfinite(lr_cc) else float("nan")
        last = g.tail(TRAFFIC_LIGHT_WINDOW)
        x_last = int(last["is_breach"].sum())
        breach_days = g[g["is_breach"]]
        es_ratio = (
            float(breach_days["loss_eur"].mean() / breach_days["es_eur"].mean()) if len(breach_days) else float("nan")
        )
        rows.append(
            {
                "method": method,
                "confidence_level": conf,
                "start_date": g["forecast_date"].min(),
                "end_date": g["forecast_date"].max(),
                "n_observations": n,
                "n_breaches": x,
                "expected_breaches": n * p,
                "breach_rate": x / n if n else float("nan"),
                "kupiec_lr": lr_pof,
                "kupiec_pvalue": p_pof,
                "christoffersen_lr": lr_ind,
                "christoffersen_pvalue": p_ind,
                "conditional_coverage_lr": lr_cc,
                "conditional_coverage_pvalue": p_cc,
                "breaches_last_250d": x_last,
                "traffic_light_zone": (
                    traffic_light_zone(x_last, TRAFFIC_LIGHT_WINDOW, p)
                    if len(last) == TRAFFIC_LIGHT_WINDOW
                    else "WARMING_UP"
                ),
                "es_ratio": es_ratio,
                "model_result": (
                    "ACCEPT" if (p_pof >= significance and (np.isnan(p_cc) or p_cc >= significance)) else "REJECT"
                ),
            }
        )
    return pd.DataFrame(rows)
