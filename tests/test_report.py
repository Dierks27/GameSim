import numpy as np

from gridsim.engine import NS, S, SimResults
from gridsim.report import american, moe, summarize


def _res(h, a):
    st = np.zeros((len(h), 2, NS))
    st[:, 0, S["pts"]] = h
    st[:, 1, S["pts"]] = a
    return SimResults("HOM", "AWY", st, {"neutral": False, "playoff": False})


def test_spread_convention_home_favored_negative():
    # home wins by 3 in every sim; home -2.5 covers, home -3 pushes, home -3.5 fails
    r = _res(np.full(100, 24), np.full(100, 21))
    assert summarize(r, spread=-2.5)["spread"]["home_cover"]["p"] == 1.0
    s3 = summarize(r, spread=-3.0)["spread"]
    assert s3["push"] == 1.0 and s3["home_cover"]["p"] == 0.0
    assert summarize(r, spread=-3.5)["spread"]["away_cover"]["p"] == 1.0


def test_total_and_moe():
    r = _res(np.array([20, 30] * 50), np.array([20, 20] * 50))
    t = summarize(r, total=45.0)["total"]
    assert t["over"]["p"] == 0.5
    assert abs(moe(0.5, 2000) - 0.0219) < 1e-3


def test_american_odds():
    assert american(0.75) == "-300"
    assert american(0.25) == "+300"
