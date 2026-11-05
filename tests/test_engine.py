import numpy as np

from gridsim.engine import S, simulate


def test_seeded_reproducible(model_2025):
    a = simulate(model_2025, "MIN", "GB", 60, seed=123)
    b = simulate(model_2025, "MIN", "GB", 60, seed=123)
    assert np.array_equal(a.stats, b.stats)
    c = simulate(model_2025, "MIN", "GB", 60, seed=124)
    assert not np.array_equal(a.stats, c.stats)


def test_box_score_consistency(model_2025):
    r = simulate(model_2025, "KC", "BUF", 200, seed=5)
    for t in (0, 1):
        pts = r.col("pts", t)
        quarters = sum(r.col(q, t) for q in ("q1", "q2", "q3", "q4", "ot"))
        assert np.array_equal(pts, quarters)
        assert (pts >= 0).all()
        assert (r.col("fgm", t) <= r.col("fga", t)).all()
        assert (r.col("comp", t) <= r.col("pass_att", t)).all()
    top = r.col("top", 0) + r.col("top", 1)
    reg = top[(r.col("ot", 0) + r.col("ot", 1)) == 0]
    # possession time accounts for (nearly) the whole 60 minutes in regulation games
    assert np.median(reg) > 3500 and reg.max() <= 3600.5


def test_ties_only_after_overtime(model_2025):
    r = simulate(model_2025, "DET", "CHI", 400, seed=9)
    ties = r.col("pts", 0) == r.col("pts", 1)
    assert ties.mean() < 0.03


def test_playoff_no_ties(model_2025):
    r = simulate(model_2025, "PHI", "WAS", 300, seed=11, playoff=True)
    assert (r.col("pts", 0) != r.col("pts", 1)).all()
