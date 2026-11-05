import json

import polars as pl

from gridsim import live
from gridsim.config import Config


def _summary(p_home, created, ko):
    return {"win": {"home": {"p": p_home}, "tie": {"p": 0.0}},
            "scores": {"margin": {"mean": 3.0}, "total": {"mean": 44.0}},
            "hist": {"margin": {"values": [-3, 3, 7], "counts": [3, 4, 3]}, "total": {"values": [40, 44, 50], "counts": [3, 4, 3]}},
            "frozen": {"created_utc": created, "kickoff_utc": ko, "config_hash": "abc", "game_id": "2030_01_AAA_BBB"}}


def test_grade_uses_latest_pre_kickoff_prediction(tmp_path, cfg, monkeypatch):
    c = Config(cfg.raw, cfg.path)
    monkeypatch.setattr(Config, "resolve_path", lambda self, p: tmp_path / p)
    sched = pl.DataFrame([{"game_id": "2030_01_AAA_BBB", "season": 2030, "week": 1, "gameday": "2030-09-08",
                           "gametime": "13:00", "home_team": "BBB", "away_team": "AAA", "home_score": 27, "away_score": 20,
                           "spread_line": 3.0, "total_line": 45.5, "home_moneyline": -150, "away_moneyline": 130}])
    import gridsim.data as D
    monkeypatch.setattr(D, "load_schedules", lambda cfg, force=False, verbose=False: sched)
    d = live.pred_dir(c, 2030, 1)
    d.mkdir(parents=True)
    # kickoff 2030-09-08 13:00 ET = 17:00Z ; one prediction before, one after kickoff
    (d / "2030_01_AAA_BBB__early__abc.json").write_text(json.dumps(_summary(0.60, "2030-09-07T12:00:00+00:00", "2030-09-08T17:00:00+00:00")))
    (d / "2030_01_AAA_BBB__late__abc.json").write_text(json.dumps(_summary(0.99, "2030-09-08T18:00:00+00:00", "2030-09-08T17:00:00+00:00")))
    rows = live.grade(c, 2030, 1, verbose=False)
    assert len(rows) == 1 and rows[0]["p_home"] == 0.60 and rows[0]["home_win"] == 1.0
    assert rows[0]["pred_file"].endswith("early__abc.json")
    sc = live.scorecard(c, 2030)
    assert sc["overall"]["games"] == 1 and abs(sc["overall"]["brier"] - 0.16) < 1e-9
    # grading again does not duplicate
    assert live.grade(c, 2030, 1, verbose=False) == []
