"""Scoring rules for probabilistic game forecasts.

CRPS from samples (exact for the empirical distribution of the sims):
    CRPS(F, y) = E|X - y| - 0.5 * E|X - X'|
with E|X - X'| = 2/n^2 * sum_i (2i - n - 1) x_(i) over sorted samples. Lower is better; it is in the
units of the variable (points), and rewards getting both the center and the spread right.
"""
from __future__ import annotations

import math

import numpy as np


def crps_samples(samples: np.ndarray, y: float) -> float:
    x = np.sort(np.asarray(samples, dtype=float))
    n = len(x)
    if n == 0:
        return float("nan")
    term1 = np.mean(np.abs(x - y))
    i = np.arange(1, n + 1)
    term2 = 2.0 / (n * n) * np.sum((2 * i - n - 1) * x)
    return float(term1 - 0.5 * term2)


def crps_from_hist(values: list, counts: list, y: float) -> float:
    return crps_samples(np.repeat(np.asarray(values, dtype=float), np.asarray(counts, dtype=int)), y)


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2))


def log_loss(p: np.ndarray, y: np.ndarray, eps: float = 1e-4) -> float:
    p = np.clip(np.asarray(p, dtype=float), eps, 1 - eps)
    y = np.asarray(y, dtype=float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def calibration_table(p: np.ndarray, y: np.ndarray, bins: int = 10) -> list[dict]:
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        m = (p >= a) & ((p < b) if b < 1 else (p <= b))
        if m.sum() == 0:
            continue
        out.append({"bin": f"{a:.1f}-{b:.1f}", "n": int(m.sum()), "pred": float(p[m].mean()), "actual": float(y[m].mean())})
    return out


def ml_to_prob(home_ml, away_ml) -> float | None:
    """De-vigged home win probability from American moneylines."""
    def imp(ml):
        if ml is None or (isinstance(ml, float) and math.isnan(ml)):
            return None
        ml = float(ml)
        return 100 / (ml + 100) if ml > 0 else -ml / (-ml + 100)
    h, a = imp(home_ml), imp(away_ml)
    if h is None or a is None or h + a <= 0:
        return None
    return h / (h + a)


def corr(a, b) -> float:
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    m = ~(np.isnan(a) | np.isnan(b))
    if m.sum() < 3 or np.std(a[m]) == 0 or np.std(b[m]) == 0:
        return float("nan")
    return float(np.corrcoef(a[m], b[m])[0, 1])


def summarize_games(g) -> dict:
    """g: polars DataFrame of backtest game rows (see backtest.py). Returns headline metrics."""
    y = g["home_win"].to_numpy().astype(float)          # 1, 0, or 0.5 for ties
    p = g["p_home"].to_numpy() + 0.5 * g["p_tie"].to_numpy()
    out = {
        "games": int(g.height),
        "brier": brier(p, y), "log_loss": log_loss(p, y),
        "mae_margin": float(np.mean(np.abs(g["pred_margin"].to_numpy() - g["margin"].to_numpy()))),
        "mae_total": float(np.mean(np.abs(g["pred_total"].to_numpy() - g["total"].to_numpy()))),
        "crps_margin": float(g["crps_margin"].mean()), "crps_total": float(g["crps_total"].mean()),
        "crps_points": float((g["crps_home"].mean() + g["crps_away"].mean()) / 2),
        "bias_margin": float(np.mean(g["pred_margin"].to_numpy() - g["margin"].to_numpy())),
        "bias_total": float(np.mean(g["pred_total"].to_numpy() - g["total"].to_numpy())),
        "corr_margin_vs_spread": corr(g["pred_margin"].to_numpy(), g["mkt_margin"].to_numpy()),
        "corr_total_vs_line": corr(g["pred_total"].to_numpy(), g["mkt_total"].to_numpy()),
        "coverage_80_margin": float(g["cov_margin"].mean()), "coverage_80_total": float(g["cov_total"].mean()),
        "coverage_80_points": float((g["cov_home"].mean() + g["cov_away"].mean()) / 2),
        "sd_pred_margin": float(np.std(g["pred_margin"].to_numpy())),
        "sd_mkt_margin": float(np.nanstd(g["mkt_margin"].to_numpy())),
        "calibration": calibration_table(p, y),
    }
    mk = g.filter(g["mkt_p_home"].is_not_null())
    if mk.height:
        ym = mk["home_win"].to_numpy().astype(float)
        out["market"] = {
            "games": int(mk.height),
            "brier": brier(mk["mkt_p_home"].to_numpy(), ym), "log_loss": log_loss(mk["mkt_p_home"].to_numpy(), ym),
            "mae_margin": float(np.nanmean(np.abs(mk["mkt_margin"].to_numpy() - mk["margin"].to_numpy()))),
            "mae_total": float(np.nanmean(np.abs(mk["mkt_total"].to_numpy() - mk["total"].to_numpy()))),
        }
        ats = mk.filter(mk["p_cover"].is_not_null() & (mk["margin"] + mk["mkt_spread"] != 0))
        if ats.height:
            pick_home = ats["p_cover"].to_numpy() > 0.5
            home_cov = (ats["margin"] + ats["mkt_spread"]).to_numpy() > 0
            out["ats_pick_rate"] = float(np.mean(pick_home == home_cov))
    return out
