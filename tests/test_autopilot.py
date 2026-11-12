"""Autopilot: what is due when GridSim starts, and that a run does every step once, survives a failing step and
does nothing the second time. No network, no simulation: the steps themselves are stubbed."""
from __future__ import annotations

import datetime as dt
import json

import polars as pl
import pytest

from gridsim import autopilot as ap
from gridsim.config import load_config
from gridsim.live import ET

UTC = dt.timezone.utc


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIDSIM_DATA", str(tmp_path / "GridSim Data"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(ap, "_LAST_LOOK", 0.0)
    return load_config()


def game(gid, day, time_="13:00", score=None):
    home, away = gid.split("_")[3], gid.split("_")[2]
    return {"game_id": gid, "season": 2026, "week": int(gid.split("_")[1]), "home_team": home, "away_team": away,
            "gameday": day, "gametime": time_, "home_score": score, "away_score": score}


def week(monkeypatch, games, wk=10):
    import gridsim.api as api
    monkeypatch.setattr(api, "week_slate", lambda cfg_, s=None, w=None: (2026, wk, [g for g in games if g["week"] == wk]))
    sched = pl.DataFrame(games, schema_overrides={"home_score": pl.Int64, "away_score": pl.Int64})
    monkeypatch.setattr(ap, "_season_sched", lambda cfg_: (2026, sched))
    monkeypatch.setattr(ap, "_data_due", lambda cfg_, season, sched_=None: None)
    return sched


def lock(cfg, gid, when: dt.datetime, wk=10):
    from gridsim.live import pred_dir
    d = pred_dir(cfg, 2026, wk)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{gid}__{when.astimezone(UTC):%Y%m%dT%H%M%SZ}__abc.json").write_text("{}")


def test_the_friday_cut_belongs_to_the_nfl_week_tuesday_to_monday():
    fri = dt.datetime(2026, 11, 13, 19, 0, tzinfo=ET)
    for day, hour in ((10, 9), (12, 12), (13, 18), (13, 20), (15, 13), (16, 21)):     # Tue ... Mon of that week
        assert ap._friday_cut(dt.datetime(2026, 11, day, hour, tzinfo=ET)) == fri.astimezone(UTC)
    assert ap._friday_cut(dt.datetime(2026, 11, 17, 9, tzinfo=ET)) == (fri + dt.timedelta(days=7)).astimezone(UTC)


def test_lock_in_once_early_in_the_week_and_once_more_after_fridays_reports(cfg, monkeypatch):
    games = [game("2026_10_WAS_NYG", "2026-11-12", "20:15"), game("2026_10_MIN_GB", "2026-11-15"),
             game("2026_10_LAC_BAL", "2026-11-16", "20:15")]
    week(monkeypatch, games)
    tue = dt.datetime(2026, 11, 10, 10, tzinfo=ET)
    assert "no locked-in prediction" in ap._lockin_due(cfg, tue)
    for g in games:
        lock(cfg, g["game_id"], tue)
    assert ap._lockin_due(cfg, tue + dt.timedelta(hours=1)) is None
    assert ap._lockin_due(cfg, dt.datetime(2026, 11, 13, 18, tzinfo=ET)) is None            # Friday, before 7 pm
    sat = dt.datetime(2026, 11, 14, 9, tzinfo=ET)
    assert "final injury reports" in ap._lockin_due(cfg, sat)                              # (Thursday's game is over)
    for g in games[1:]:
        lock(cfg, g["game_id"], sat)
    assert ap._lockin_due(cfg, sat + dt.timedelta(hours=1)) is None
    assert ap._lockin_due(cfg, dt.datetime(2026, 11, 16, 23, 30, tzinfo=ET)) is None        # everything has kicked off


def test_grading_is_due_for_finished_games_that_have_a_prediction_and_no_grade(cfg, monkeypatch):
    games = [game("2026_09_DAL_PHI", "2026-11-08", score=20), game("2026_09_SF_SEA", "2026-11-08", score=17),
             game("2026_10_MIN_GB", "2026-11-15")]
    sched = week(monkeypatch, games)
    assert ap._grade_due(cfg, 2026, sched) == []                          # nothing was locked in: nothing to grade
    lock(cfg, "2026_09_DAL_PHI", dt.datetime(2026, 11, 6, tzinfo=UTC), wk=9)
    assert ap._grade_due(cfg, 2026, sched) == [9]
    card = cfg.resolve_path("predictions") / "scorecard.csv"
    card.write_text("game_id,season\n2026_09_DAL_PHI,2026\n")
    assert ap._grade_due(cfg, 2026, sched) == []
    assert ap._signals_due(cfg, sched) == []                              # no Kalshi record at all
    led = cfg.resolve_path("signals") / "ledger.csv"
    led.parent.mkdir(parents=True, exist_ok=True)
    led.write_text("ts_utc\n")
    assert ap._signals_due(cfg, sched) == ["2026_09_DAL_PHI", "2026_09_SF_SEA"]


def test_prices_are_checked_when_the_last_check_is_old_and_games_are_still_to_come(cfg, monkeypatch):
    import os
    now = dt.datetime.now(UTC)
    soon = (now + dt.timedelta(days=2)).astimezone(ET)
    games = [game("2026_10_MIN_GB", soon.strftime("%Y-%m-%d"), soon.strftime("%H:%M"))]
    week(monkeypatch, games)
    assert "no Kalshi prices" in ap._prices_due(cfg, now)
    d = cfg.resolve_path("signals") / "snapshots"
    d.mkdir(parents=True)
    f = d / "2026_wk10_20261112T100000Z.json"
    f.write_text("{}")
    assert ap._prices_due(cfg, now) is None                               # just checked
    os.utime(f, (now.timestamp() - 3600, now.timestamp() - 3600))
    assert "60 minutes ago" in ap._prices_due(cfg, now)
    week(monkeypatch, [game("2026_10_MIN_GB", "2026-11-01", score=3)])
    assert ap._prices_due(cfg, now) is None                               # nothing left to kick off


def test_games_with_no_saved_simulation_get_a_price_check_at_once_but_not_again_and_again(cfg, monkeypatch, tmp_path):
    import gridsim.api as api
    import gridsim.scanner as scanner
    now = dt.datetime.now(UTC)
    soon = (now + dt.timedelta(days=2)).astimezone(ET)
    games = [game("2026_10_MIN_GB", soon.strftime("%Y-%m-%d"), soon.strftime("%H:%M")),
             game("2026_10_DAL_PHI", "2026-11-01", score=3)]                       # (a finished game never counts)
    week(monkeypatch, games)
    lock(cfg, "2026_10_MIN_GB", now - dt.timedelta(minutes=30))
    monkeypatch.setattr(ap, "_friday_cut", lambda now_: now + dt.timedelta(days=1))
    d = cfg.resolve_path("signals") / "snapshots"
    d.mkdir(parents=True)
    (d / "2026_wk10_20261112T100000Z.json").write_text("{}")                # prices were checked a moment ago
    sim = tmp_path / "sim.json"
    monkeypatch.setattr(api, "cache_path", lambda req, cfg_: sim)
    assert ap._prices_due(cfg, now) is None
    assert "1 of this week's games" in ap._sims_missing(cfg, now)           # a new install: nothing simulated yet
    assert [x["key"] for x in ap.due(cfg, now)] == ["prices"]
    scans = []
    monkeypatch.setattr(scanner, "scan", lambda cfg_, **kw: scans.append(1) or [])
    assert ap._do(cfg, "prices").startswith("prices saved")
    assert scans == [1]
    assert ap._sims_missing(cfg, _later(now, 5)) is None                     # the game could not be simulated: not asked
    assert ap.due(cfg, _later(now, 5)) == [] and ap._do(cfg, "prices") == "checked recently"      # again straight away
    assert "1 of this week's games" in ap._sims_missing(cfg, _later(now, 50))
    sim.write_text("{}")
    assert ap._sims_missing(cfg, _later(now, 50)) is None                    # simulated: nothing to ask for


def _later(now, minutes):
    return now + dt.timedelta(minutes=minutes)


def test_the_check_up_is_for_the_last_complete_week_also_while_the_next_one_is_being_played(cfg, monkeypatch):
    done = [game("2026_09_DAL_PHI", "2026-11-08", score=20), game("2026_09_SF_SEA", "2026-11-09", score=17)]
    half = [done[0], game("2026_09_SF_SEA", "2026-11-09")]
    assert ap._checkup_due(cfg, 2026, week(monkeypatch, half)) is None
    assert ap._checkup_due(cfg, 2026, week(monkeypatch, done)) == (6, 9)
    # Friday to Monday the new week already has a result: last week's check-up is still due (weekend-only users)
    started = done + [game("2026_10_WAS_NYG", "2026-11-12", score=24), game("2026_10_NE_DET", "2026-11-15")]
    assert ap._checkup_due(cfg, 2026, week(monkeypatch, started)) == (6, 9)
    (cfg.resolve_path("diagnose") / "2026_wk6-9").mkdir(parents=True)
    assert ap._checkup_due(cfg, 2026, week(monkeypatch, done)) is None
    assert ap._checkup_due(cfg, 2026, week(monkeypatch, started)) is None
    # a span that turned out to have nothing to check (the play-offs) is remembered, not asked again
    ap._write(ap._dir(cfg) / "checkup_none.json", ["2026_wk7-10"])
    over = done + [game("2026_10_WAS_NYG", "2026-11-12", score=24)]
    assert ap._checkup_due(cfg, 2026, week(monkeypatch, over)) is None


def test_data_that_could_not_be_downloaded_is_a_failed_step_not_up_to_date(cfg, monkeypatch):
    import gridsim.availability as av
    import gridsim.data as D
    week(monkeypatch, [game("2026_10_MIN_GB", "2026-11-15")])
    monkeypatch.setattr(ap, "_data_due", lambda cfg_, season, sched_=None: "the data is more than 12 hours old")
    calls = []
    monkeypatch.setattr(D, "refresh_all", lambda cfg_, seasons: calls.append("plays") or (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(av, "refresh_availability", lambda cfg_, season: calls.append("injuries"))
    monkeypatch.setattr(D, "current_season", lambda: 2026)
    with pytest.raises(ap.StepFailed, match="could not be downloaded"):
        ap._do(cfg, "data")
    assert calls == ["plays", "injuries"]                 # the injury report is still tried when the plays failed


def test_before_the_first_game_of_a_season_the_plays_file_is_not_expected(cfg, monkeypatch, tmp_path):
    import gridsim.data as D
    seen = []
    d = tmp_path / "cache"
    d.mkdir(parents=True)
    monkeypatch.setattr(D, "_is_stale", lambda p, season, cfg_: seen.append(p.name) or False)
    monkeypatch.setattr(D, "_cache_dir", lambda cfg_: d)
    for n in ("schedules.parquet", "injuries_2026.parquet", "rosters_weekly_2026.parquet", "depth_charts_2026.parquet"):
        (d / n).write_text("")
    fresh = pl.DataFrame([game("2026_01_DAL_PHI", "2026-09-10")], schema_overrides={"home_score": pl.Int64, "away_score": pl.Int64})
    assert ap._data_due(cfg, 2026, fresh) is None and "pbp_2026.parquet" not in seen
    played = pl.DataFrame([game("2026_01_DAL_PHI", "2026-09-10", score=20)], schema_overrides={"home_score": pl.Int64, "away_score": pl.Int64})
    assert "pbp_2026.parquet has not been downloaded" in ap._data_due(cfg, 2026, played)


def test_the_friday_lock_in_fetches_the_injury_report_first_and_waits_if_it_cannot(cfg, monkeypatch, tmp_path):
    import gridsim.availability as av
    import gridsim.data as D
    import gridsim.live as live
    games = [game("2026_10_MIN_GB", "2099-11-15")]
    week(monkeypatch, games)
    d = tmp_path / "cache"
    d.mkdir(parents=True)
    inj = d / "injuries_2026.parquet"
    inj.write_text("old")
    import os
    os.utime(inj, (1_000_000_000, 1_000_000_000))        # downloaded long before Friday evening
    monkeypatch.setattr(D, "_cache_dir", lambda cfg_: d)
    monkeypatch.setattr(ap, "_lockin_due", lambda cfg_, now: "the final injury reports are out (Friday evening): one more lock-in")
    monkeypatch.setattr(ap, "_friday_cut", lambda now: now - dt.timedelta(hours=1))
    locked = []
    monkeypatch.setattr(live, "predict_week", lambda cfg_, verbose=False: locked.append(1) or ["a.json"])
    monkeypatch.setattr(av, "refresh_availability", lambda cfg_, season: None)            # the download fails quietly
    with pytest.raises(ap.StepFailed, match="injury report could not be downloaded"):
        ap._do(cfg, "lockin")
    assert not locked
    monkeypatch.setattr(av, "refresh_availability", lambda cfg_, season: inj.write_text("new") and os.utime(inj, None))
    assert ap._do(cfg, "lockin") == "1 game(s) locked in" and locked == [1]


def test_a_run_does_every_step_in_order_survives_a_failure_and_reports_it(cfg, monkeypatch):
    week(monkeypatch, [game("2026_10_MIN_GB", "2026-11-15")])
    calls = []

    def do(cfg_, key):
        calls.append(key)
        if key == "lockin":
            raise OSError("no network")
        if key == "prices":
            raise ap.StepFailed("Kalshi's prices could not be fetched")
        return {"data": "downloaded", "grade": "nothing to grade", "checkup": "already done"}[key]
    monkeypatch.setattr(ap, "_do", do)
    assert ap.status(cfg)["state"] == "never"
    st = ap.run(cfg, verbose=False)
    assert calls == ["data", "grade", "lockin", "prices", "checkup"]       # the failure did not stop the rest
    states = {s["key"]: s["state"] for s in st["steps"]}
    assert states == {"data": "done", "grade": "skipped", "lockin": "failed", "prices": "failed", "checkup": "skipped"}
    assert "no network" in next(s for s in st["steps"] if s["key"] == "lockin")["detail"]
    # a step that ran but did not get its job done says so in plain words (no "StepFailed:" in front)
    assert next(s for s in st["steps"] if s["key"] == "prices")["detail"] == "Kalshi's prices could not be fetched"
    assert st["state"] == "done" and st["finished_utc"] and not ap.running(cfg)
    assert ap.status(cfg) == st and not (ap._dir(cfg) / "run.pid").exists()


def test_only_one_run_at_a_time_and_a_dead_run_is_not_running(cfg, monkeypatch):
    import os
    week(monkeypatch, [game("2026_10_MIN_GB", "2026-11-15")])
    d = ap._dir(cfg)
    now = dt.datetime.now(UTC).isoformat(timespec="seconds")
    (d / "run.pid").write_text(json.dumps({"pid": os.getpid(), "started_utc": now}))
    (d / "status.json").write_text(json.dumps({"state": "running", "steps": []}))
    monkeypatch.setattr(ap, "_do", lambda *a: pytest.fail("a second run started"))
    assert ap.running(cfg) and ap.run(cfg, verbose=False)["state"] == "running"
    assert ap.start_if_due(cfg) == "running"
    # the PC was switched off mid-run: the pid is gone (or it is hours old) -> not running, and the page says so
    monkeypatch.setattr(ap, "pid_alive", lambda pid: False)
    assert not ap.running(cfg)
    st = ap.status(cfg)
    assert st["state"] == "done" and st["interrupted"]
    old = (dt.datetime.now(UTC) - dt.timedelta(hours=4)).isoformat(timespec="seconds")
    (d / "run.pid").write_text(json.dumps({"pid": os.getpid(), "started_utc": old}))
    monkeypatch.setattr(ap, "pid_alive", lambda pid: True)
    assert not ap.running(cfg)


def test_the_app_starts_it_only_when_something_is_due_and_not_again_at_once(cfg, monkeypatch):
    started = []
    monkeypatch.setattr(ap.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    # (on Windows "is that process alive?" asks tasklist, which would go through the Popen replaced above)
    monkeypatch.setattr(ap, "pid_alive", lambda pid: True)
    monkeypatch.setattr(ap, "due", lambda cfg_: [])
    assert ap.start_if_due(cfg) == "nothing" and not started
    assert ap.start_if_due(cfg) == "waiting"                              # looked a moment ago
    monkeypatch.setattr(ap, "_LAST_LOOK", 0.0)
    monkeypatch.setattr(ap, "due", lambda cfg_: [{"key": "prices", "what": "x", "why": "y"}])
    assert ap.start_if_due(cfg) == "started" and started[0][1:] == ["-m", "gridsim", "autopilot"]
    # counted as running at once (before the child has said so itself): a second look cannot start another
    assert ap.running(cfg) and ap.start_if_due(cfg) == "running" and len(started) == 1
    monkeypatch.setattr(ap, "_LAST_LOOK", 0.0)
    assert ap.start_if_due(cfg, min_gap_s=0) == "running" and len(started) == 1


def test_a_gridsim_left_running_keeps_looking_on_its_own(cfg, monkeypatch):
    import threading
    looks, three = [], threading.Event()

    def look(cfg_, min_gap_s=600.0):
        looks.append(min_gap_s)
        if len(looks) == 2:
            raise OSError("a file was being replaced")            # one bad moment must not end the timer
        if len(looks) >= 3:
            three.set()
        return "nothing"
    monkeypatch.setattr(ap, "start_if_due", look)
    ap.stop_ticker()
    try:
        assert ap.ensure_ticker(interval_s=0.05) is True
        assert ap.ensure_ticker(interval_s=0.05) is False         # one timer per GridSim, however many pages open
        assert three.wait(5), "the timer stopped looking"
        assert ap._TICKER.daemon and all(g < 0.05 for g in looks)  # (never holds GridSim open; no extra throttle)
        assert sum(t.name == "gridsim-autopilot" for t in threading.enumerate()) == 1
    finally:
        ap.stop_ticker()                                          # (it must not outlive the test and start real runs)
    assert not any(t.name == "gridsim-autopilot" for t in threading.enumerate())
    n = len(looks)
    threading.Event().wait(0.2)
    assert len(looks) == n


def test_the_app_window_uses_edge_or_chrome_and_falls_back_to_the_browser(monkeypatch):
    from gridsim import appwindow as aw
    assert aw.command("C:/Edge/msedge.exe", "http://localhost:8501")[:2] == ["C:/Edge/msedge.exe", "--app=http://localhost:8501"]
    opened, ran = [], []
    monkeypatch.setattr(aw.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(aw, "browser_exe", lambda: None)
    assert aw.open_window("http://localhost:8501") == "browser" and opened == ["http://localhost:8501"]
    monkeypatch.setattr(aw, "browser_exe", lambda: "C:/Edge/msedge.exe")
    monkeypatch.setattr(aw.subprocess, "Popen", lambda cmd, **kw: ran.append(cmd))
    assert aw.open_window() == "app" and ran[0][1] == "--app=http://localhost:8501" and len(opened) == 1

    def boom(cmd, **kw):
        raise OSError("blocked")
    monkeypatch.setattr(aw.subprocess, "Popen", boom)
    assert aw.open_window() == "browser" and len(opened) == 2             # GridSim always opens somewhere
    assert aw.wait_for(9, timeout=0.6) is False
