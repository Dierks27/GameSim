import numpy as np

from gridsim.engine import S, simulate
from gridsim.players import PI, build_player_model


def test_player_stats_sum_to_team(cfg, model_2025):
    pm = build_player_model(cfg, model_2025, 2025, None, "DET", "GB", None)
    r = simulate(model_2025, "DET", "GB", 60, seed=3, players=pm)
    arr = pm.arrays()
    for t in (0, 1):
        rec_yds = sum(a[:, PI["rec_yds"]] for (tt, _), a in arr.items() if tt == t)
        comp = sum(a[:, PI["comp"]] for (tt, _), a in arr.items() if tt == t)
        assert np.allclose(rec_yds, r.col("pass_yds_gross", t))
        assert np.allclose(comp, r.col("comp", t))
    assert r.players and set(r.players) == {"DET", "GB"}


def test_rush_yards_sum_to_team(cfg, model_2025):
    pm = build_player_model(cfg, model_2025, 2025, None, "BAL", "PIT", None)
    r = simulate(model_2025, "BAL", "PIT", 60, seed=4, players=pm)
    arr = pm.arrays()
    for t in (0, 1):
        rush = sum(a[:, PI["rush_yds"]] for (tt, _), a in arr.items() if tt == t)
        # team rush yards include kneels (-1 each), which the player model also charges to the QB
        assert np.allclose(rush, r.col("rush_yds", t))
