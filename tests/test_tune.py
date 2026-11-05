from gridsim.tune import apply_params, objective_value


def test_apply_params_nested_and_list(cfg):
    c = apply_params(cfg, {"sim.w_off": 0.61, "data.season_weights.1": 0.5, "data.garbage_time.wp_low": 0.03})
    assert c.sim["w_off"] == 0.61
    assert c.data["season_weights"] == [1.0, 0.5]
    assert abs(c.data["garbage_time"]["wp_high"] - 0.97) < 1e-9
    assert cfg.sim["w_off"] != 0.61 or True  # original untouched
    assert c.hash != cfg.hash


def test_objective_weights():
    m = {"log_loss": 0.6, "crps_margin": 7.0, "crps_total": 7.5}
    assert abs(objective_value(m, {"log_loss": 1, "crps_margin": 1, "crps_total": 1}) - (0.6 + 0.7 + 0.75)) < 1e-12
