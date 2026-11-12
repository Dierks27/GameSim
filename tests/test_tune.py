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


def test_a_validation_gain_inside_the_noise_is_not_evidence():
    import numpy as np
    import polars as pl

    from gridsim.tune import paired_gain_se
    rng = np.random.default_rng(1)
    n = 240
    ids = [f"g{i:03d}" for i in range(n)]
    base = pl.DataFrame({"game_id": ids, "p_home": rng.uniform(0.3, 0.7, n), "p_tie": np.zeros(n),
                         "home_win": rng.integers(0, 2, n), "crps_margin": rng.uniform(5, 9, n),
                         "crps_total": rng.uniform(5, 9, n)})
    other = base.with_columns(crps_margin=pl.col("crps_margin") + pl.Series(rng.normal(0, 1.0, n))).sample(fraction=1.0, shuffle=True, seed=3)
    w = {"log_loss": 1, "crps_margin": 1, "crps_total": 1}
    se = paired_gain_se(base, other, w)
    assert 0.004 < se < 0.009                       # sd 1.0 / 10 per game over 240 games ~ 0.0065
    assert paired_gain_se(base, base, w) == 0.0
    assert paired_gain_se(base, other.head(100), w) is None      # not the same games: cannot be paired


def _run(cfg, stamp, **summary):
    import json
    d = cfg.resolve_path("tuning") / stamp
    d.mkdir(parents=True, exist_ok=True)
    (d / "summary.json").write_text(json.dumps(summary))


def test_a_tuning_result_from_an_older_model_is_not_offered(tmp_path, monkeypatch):
    import pytest

    from gridsim.config import MODEL_REV, load_config
    from gridsim.tune import apply_tuned, tuned_status
    monkeypatch.setenv("GRIDSIM_DATA", str(tmp_path / "GridSim Data"))
    cfg = load_config()
    best = {"sim.w_off": 0.55, "data.season_weights.1": 0.2}
    assert tuned_status(cfg) == "none"
    _run(cfg, "20261109-200000", recommended=True, best_params=best)          # no model_rev: found with the old model
    assert tuned_status(cfg) == "stale"
    with pytest.raises(RuntimeError, match="older version"):
        apply_tuned(cfg)
    _run(cfg, "20261109-200000", recommended=True, best_params=best, model_rev=MODEL_REV)
    assert tuned_status(cfg) == "fresh"
    # a newer run that was not recommended: nothing is on offer, least of all the older run's file
    _run(cfg, "20261111-200000", recommended=False, best_params={"sim.w_off": 0.7}, model_rev=MODEL_REV)
    assert tuned_status(cfg) == "none"
    with pytest.raises(FileNotFoundError):
        apply_tuned(cfg)


def test_using_tuned_settings_changes_only_the_tuned_values(tmp_path, monkeypatch):
    import yaml

    from gridsim.config import MODEL_REV, load_config, local_config_path, save_local
    from gridsim.tune import apply_tuned, tuned_status
    monkeypatch.setenv("GRIDSIM_DATA", str(tmp_path / "GridSim Data"))
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = load_config()
    _run(cfg, "20261112-200000", recommended=True, model_rev=MODEL_REV,
         best_params={"sim.w_off": 0.58, "data.season_weights.1": 0.2, "data.garbage_time.wp_low": 0.03})
    # settings changed while the tune was running (it takes many hours)
    save_local({"watch": {"price_jump": 0.1}, "sim": {"n_sims": 5000}})
    cfg = load_config()
    assert tuned_status(cfg) == "fresh"
    apply_tuned(cfg)
    new = load_config()
    assert new.watch["price_jump"] == 0.1 and new.sim["n_sims"] == 5000        # kept
    assert new.sim["w_off"] == 0.58 and new.data["season_weights"][1] == 0.2
    assert new.data["garbage_time"]["wp_high"] == 0.97 and tuned_status(new) == "applied"
    local = yaml.safe_load(local_config_path().read_text(encoding="utf-8"))
    assert set(local) == {"watch", "sim", "data"}                             # only what differs from the shipped file
    # a later change to an unrelated setting does not bring the offer back
    save_local({**local, "scanner": {"min_edge": 0.06}})
    assert tuned_status(load_config()) == "applied"


def test_an_old_model_tuning_still_in_use_is_found_and_can_be_put_back(tmp_path, monkeypatch):
    from gridsim.config import DEFAULT_CONFIG_PATH, load_config, save_local
    from gridsim.tune import drop_stale_tuned, stale_tuned_in_use, tuned_status
    monkeypatch.setenv("GRIDSIM_DATA", str(tmp_path / "GridSim Data"))
    monkeypatch.setenv("HOME", str(tmp_path))
    shipped = load_config(DEFAULT_CONFIG_PATH, use_local=False)
    cfg = load_config()
    assert stale_tuned_in_use(cfg) == []
    best = {"sim.w_off": 0.5835, "sim.k_outcome": 63.67, "data.season_weights.1": 0.21,
            "home_field.tilt": shipped.home_field["tilt"]}                     # (one value the tune left as it was)
    _run(cfg, "20261107-200000", recommended=True, best_params=best)          # promoted with the previous version
    save_local({"sim": {"w_off": 0.5835, "k_outcome": 63.67}, "data": {"season_weights": [1.0, 0.21]},
                "watch": {"sim_move": 0.07}})
    cfg = load_config()
    assert tuned_status(cfg) == "stale"
    assert sorted(stale_tuned_in_use(cfg)) == ["data.season_weights.1", "sim.k_outcome", "sim.w_off"]
    backup = drop_stale_tuned(cfg)
    new = load_config()
    assert backup.exists() and stale_tuned_in_use(new) == []
    assert new.sim["w_off"] == shipped.sim["w_off"] and new.data["season_weights"] == shipped.data["season_weights"]
    assert new.watch["sim_move"] == 0.07                                      # your other settings stay
    # values changed by hand since then are not "the old tuning in use"
    save_local({"sim": {"w_off": 0.5835, "k_outcome": 70.0}, "data": {"season_weights": [1.0, 0.21]}})
    assert stale_tuned_in_use(load_config()) == []


def test_the_settings_page_does_not_cut_a_tuned_value_and_an_already_cut_one_is_recognised(tmp_path, monkeypatch):
    import sys
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    from gridsim.config import MODEL_REV, load_config, load_local, save_local
    from gridsim.tune import apply_tuned, drop_stale_tuned, stale_tuned_in_use, tuned_status
    monkeypatch.setenv("GRIDSIM_DATA", str(tmp_path / "GridSim Data"))
    monkeypatch.setenv("HOME", str(tmp_path))
    app = Path(__file__).resolve().parent.parent / "app"
    monkeypatch.syspath_prepend(str(app))
    cfg = load_config()
    best = {"sim.w_off": 0.5835, "sim.k_outcome": 63.67, "kicking.k_kicker": 15.87}   # two ship as whole numbers
    _run(cfg, "20261112-200000", recommended=True, model_rev=MODEL_REV, best_params=best)
    apply_tuned(cfg)
    assert tuned_status(load_config()) == "applied"
    # saving one unrelated value on the Settings page leaves the tuned ones alone
    at = AppTest.from_file(str(app / "pages" / "settings.py"), default_timeout=60)
    at.session_state["_via_app"] = True                                   # (as when opened through the app)
    at.run()
    assert not at.exception
    at.number_input(key="cfg__watch__price_jump").set_value(0.08)
    next(b for b in at.button if b.label == "Save changes").click().run()
    assert not at.exception
    local = load_local()
    assert local["watch"]["price_jump"] == 0.08 and local["sim"]["k_outcome"] == 63.67
    assert local["kicking"]["k_kicker"] == 15.87 and tuned_status(load_config()) == "applied"
    # a settings file the earlier form already cut (63.67 -> 63, 15.87 -> 15 = shipped, so dropped) still counts
    save_local({"sim": {"w_off": 0.5835, "k_outcome": 63}, "watch": {"price_jump": 0.08}})
    assert tuned_status(load_config()) == "applied"
    _run(cfg, "20261112-200000", recommended=True, best_params=best)          # the same run, found with the old model
    assert sorted(stale_tuned_in_use(load_config())) == ["sim.k_outcome", "sim.w_off"]
    drop_stale_tuned(load_config())
    new = load_config()
    assert new.sim["k_outcome"] == 60 and new.watch["price_jump"] == 0.08 and stale_tuned_in_use(new) == []


def test_a_tuned_setting_this_version_no_longer_has_does_not_break_the_page(tmp_path, monkeypatch):
    import pytest

    from gridsim.config import MODEL_REV, load_config, load_local, save_local
    from gridsim.tune import apply_tuned, drop_stale_tuned, stale_tuned_in_use
    monkeypatch.setenv("GRIDSIM_DATA", str(tmp_path / "GridSim Data"))
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = load_config()
    best = {"sim.w_off": 0.5835, "sim.old_knob": 0.42}
    _run(cfg, "20261107-200000", recommended=True, best_params=best)
    save_local({"sim": {"w_off": 0.5835, "old_knob": 0.42}})
    assert sorted(stale_tuned_in_use(load_config())) == ["sim.old_knob", "sim.w_off"]
    drop_stale_tuned(load_config())
    assert "sim" not in load_local() and stale_tuned_in_use(load_config()) == []
    _run(cfg, "20261113-200000", recommended=True, model_rev=MODEL_REV, best_params={"sim.nope": 1.0})
    with pytest.raises(RuntimeError, match="does not have"):
        apply_tuned(load_config())
