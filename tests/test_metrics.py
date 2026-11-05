import numpy as np

from gridsim.metrics import brier, calibration_table, crps_from_hist, crps_samples, log_loss, ml_to_prob


def test_crps_point_mass_is_abs_error():
    assert abs(crps_samples(np.full(50, 3.0), 7.0) - 4.0) < 1e-12


def test_crps_matches_bruteforce():
    rng = np.random.default_rng(0)
    x = rng.normal(0, 10, 300).round()
    y = 4.0
    brute = np.mean(np.abs(x - y)) - 0.5 * np.mean(np.abs(x[:, None] - x[None, :]))
    assert abs(crps_samples(x, y) - brute) < 1e-9
    vals, counts = np.unique(x, return_counts=True)
    assert abs(crps_from_hist(vals.tolist(), counts.tolist(), y) - brute) < 1e-9


def test_scores_and_market_prob():
    assert brier(np.array([1.0, 0.0]), np.array([1.0, 0.0])) == 0.0
    assert log_loss(np.array([0.5]), np.array([1.0])) == np.log(2)
    p = ml_to_prob(-200, 170)
    assert 0.62 < p < 0.66
    cal = calibration_table(np.array([0.15, 0.16, 0.85]), np.array([0, 0, 1]))
    assert cal[0]["n"] == 2 and cal[-1]["actual"] == 1.0
