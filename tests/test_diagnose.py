"""Diagnose must not mistake a less-informed but calibrated forecast for a compressed one."""
import numpy as np
import polars as pl

from gridsim.diagnose import margin_calibration, residual_breakdown


def _games(n=4000, seed=0):
    rng = np.random.default_rng(seed)
    strength = rng.normal(0, 6, n)                 # true expected home margin
    seen = strength + rng.normal(0, 4.5, n)        # what the sim's data shows (noisy)
    sim = seen * (36 / 56.25)                      # calibrated given that: E[strength | seen]
    mkt = strength                                 # the market knows more
    margin = strength + rng.normal(0, 13, n)
    return pl.DataFrame({"pred_margin": sim, "mkt_margin": mkt, "margin": margin, "pred_total": np.full(n, 44.0),
                         "total": np.full(n, 44.0), "mkt_total": np.full(n, 44.0), "home_rest": [7] * n, "away_rest": [7] * n,
                         "weekday": ["Sunday"] * n, "gametime": ["13:00"] * n, "roof": ["outdoors"] * n, "div_game": [0] * n,
                         "week": [8] * n, "home_qb_change": [False] * n, "away_qb_change": [False] * n,
                         "location": ["Home"] * n})


def test_calibrated_less_informed_sim_is_not_compressed():
    g = _games()
    c = margin_calibration(g)
    assert abs(c["slope_market"] - 1) < 3 * c["se_market"]     # calibrated, although sd(sim) < sd(market)
    assert g["pred_margin"].std() < 0.85 * g["mkt_margin"].std()


def test_market_groups_report_the_market_share_of_the_error():
    g = _games()
    r = {(x["group"], x["metric"]): x for x in residual_breakdown(g)}
    away = r[("away favorite", "margin (home) error")]
    # the sim "underrates away favorites" (positive home error) only because the market knows more
    assert away["mean_error"] > 0
    assert abs(away["mean_error"] - away["market_error"] - away["sim_vs_market"]) < 1e-9
    own = r[("sim: away favored", "margin (home) error")]
    assert abs(own["mean_error"]) < 3 * own["se"]                # conditioning on the sim's own forecast: no bias


def test_market_picked_groups_do_not_reach_the_summary():
    from gridsim.diagnose import plain_summary
    g = _games()
    out = plain_summary([], residual_breakdown(g), {}, None, margin_calibration(g))
    assert not any("favorite' games" in s for s in out), out


def test_a_group_the_closing_line_missed_just_as_much_is_not_listed():
    from gridsim.diagnose import plain_summary
    row = {"group": "QB change (either team)", "metric": "margin (home) error", "n": 15, "mean_error": 10.41, "se": 2.97,
           "t": 3.5, "flag": True, "market_error": 10.97, "sim_vs_market": -0.55, "se_vs_market": 0.70}
    assert not any("QB change" in s for s in plain_summary([], [row], {}, None, None))
    own = {**row, "market_error": 4.0, "sim_vs_market": 6.41}
    assert any("QB change" in s for s in plain_summary([], [own], {}, None, None))


def test_components_are_scored_on_the_backtests_that_have_them():
    from gridsim.diagnose import component_scorecard
    rng = np.random.default_rng(0)
    n = 200
    from gridsim.diagnose import COMPONENTS
    num, den = COMPONENTS["ypc_designed"]
    old = pl.DataFrame({"sim_points": rng.normal(22, 5, n), "act_points": rng.normal(22, 9, n)})
    new = old.with_columns(**{f"sim_{num}": pl.lit(100.0), f"act_{num}": pl.lit(98.0),
                              f"sim_{den}": pl.lit(24.0), f"act_{den}": pl.lit(24.0)})
    rows = {r["component"]: r for r in component_scorecard(pl.concat([old, new], how="diagonal"))}
    assert np.isfinite(rows["ypc_designed"]["bias"]) and abs(rows["ypc_designed"]["bias"] - 2 / 24) < 1e-9


def test_a_total_the_closing_line_missed_just_as_much_is_not_listed():
    from gridsim.diagnose import plain_summary
    row = {"group": "primetime", "metric": "total error", "n": 12, "mean_error": 11.83, "se": 2.96, "t": 4.0,
           "flag": True, "market_error": 11.67, "sim_vs_market": 0.16, "se_vs_market": 0.77}
    assert not any("primetime" in s for s in plain_summary([], [row], {}, None, None))
    own = {**row, "market_error": 4.0, "sim_vs_market": 7.83}
    assert any("primetime" in s and "the sim's own part is +7.83" in s for s in plain_summary([], [own], {}, None, None))
    bare = {k: v for k, v in row.items() if k not in ("market_error", "sim_vs_market", "se_vs_market")}
    assert any("primetime" in s for s in plain_summary([], [bare], {}, None, None))   # no closing totals: as before
