"""Data protection: the data folder, backups, restore, bringing in an old GridSim folder, reconstructed predictions
and backfilled prices. Replays the real-world case: an old install (with history) + a fresh install (with a few new
records), merged; then the data folder is deleted and restored from a backup."""
from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest
import yaml

from gridsim import config as gconfig
from gridsim import userdata
from gridsim.config import load_config
from gridsim.scanner import LEDGER_FIELDS


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data = tmp_path / "GridSim Data"
    monkeypatch.setenv("GRIDSIM_DATA", str(data))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    for v in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        monkeypatch.delenv(v, raising=False)
    # a fake program folder, so the automatic move from "the code folder" can be exercised without the real repo
    code = tmp_path / "C_GridSim" / "GameSim-claude-friendly-turing-wk7xhb"
    code.mkdir(parents=True)
    monkeypatch.setattr(userdata, "REPO_ROOT", code)
    return {"data": data, "code": code, "tmp": tmp_path, "backups": tmp_path / "GridSim Backups"}


def ledger_row(ts, ticker="KXNFLSPREAD-26NOV08CLENO-NO3", **kw):
    r = {f: "" for f in LEDGER_FIELDS}
    r.update({"ts_utc": ts, "season": 2026, "week": 10, "game_id": "2026_10_CLE_NO", "market": "spread",
              "ticker": ticker, "label": "NO wins by 3.5+", "side": "YES", "price": 0.5, "fee": 0.02,
              "confidence": "watchlist"}, **kw)
    return r


def write_ledger(p: Path, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LEDGER_FIELDS)
        w.writeheader()
        w.writerows(rows)


def read_rows(p: Path):
    with open(p, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def make_install(root: Path, ledger_ts, preds, extra=None):
    write_ledger(root / "signals" / "ledger.csv", [ledger_row(t) for t in ledger_ts])
    for name in preds:
        p = root / "predictions" / "2026" / "wk09" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"frozen": {"created_utc": "2026-11-06T00:00:00+00:00"}, "name": name}))
    for rel, text in (extra or {}).items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")


def test_paths_go_to_the_data_folder(env):
    cfg = load_config()
    assert cfg.resolve_path("predictions") == env["data"] / "predictions"
    assert cfg.resolve_path("signals/ledger.csv") == env["data"] / "signals" / "ledger.csv"
    assert cfg.resolve_path("config.tuned.yaml") == env["data"] / "config.tuned.yaml"
    assert cfg.resolve_path("data/cache") == gconfig.REPO_ROOT / "data" / "cache"     # re-downloadable: code folder
    assert gconfig.LOCAL_CONFIG_PATH == env["data"] / "config.local.yaml"


def test_backup_and_resources_sections_do_not_change_the_config_hash(env):
    cfg = load_config()
    raw = {k: v for k, v in cfg.raw.items() if k not in ("backup", "resources")}
    assert gconfig.config_hash(raw) == cfg.hash
    assert cfg.copy_with({"backup.dir": "E:/x", "resources.free_cores": 5}).hash == cfg.hash


def test_old_folder_plus_fresh_install_then_delete_and_restore(env):
    old = env["tmp"] / "OneDrive" / "Desktop" / "GameSim-claude-friendly-turing-wk7xhb"
    # old install: two weeks of history, a lineup file, settings, a tuning run, the shipped example run and the
    # developer's shipped test prediction (both must NOT be imported)
    make_install(old, ["2026-11-01T15:00:00+00:00", "2026-11-02T15:00:00+00:00"],
                 ["2026_09_BUF_MIN__20261106T010000Z__aaaa.json", "2026_09_TB_CHI__20261105T080054Z__9ce081a6ac.json"],
                 {"overrides/2026_wk09.yaml": "MIN: {out: [J.Jefferson]}\n",
                  "config.local.yaml": "sim:\n  n_sims: 3000\nplayers:\n  recent_games: 8\n",
                  "tuning/20261107-220000/summary.json": "{}",
                  "tuning/20261105-074738/summary.json": "{}",
                  "signals/snapshots/2026_wk09_20261101T150000Z.json": "{}",
                  ".env": "NTFY_TOPIC=old-topic\n"})
    # fresh install (code folder): one overlapping ledger row + one new; its own setting for n_sims
    make_install(env["code"], ["2026-11-02T15:00:00+00:00", "2026-11-08T15:00:00+00:00"],
                 ["2026_10_CLE_NO__20261107T010000Z__bbbb.json"],
                 {"config.local.yaml": "sim:\n  n_sims: 4000\n"})

    # 1) first start of the update: the program folder's data moves into the data folder
    rep = userdata.ensure()
    assert rep["added"] >= 2
    led = env["data"] / "signals" / "ledger.csv"
    assert len(read_rows(led)) == 2

    # 2) bring in the old folder: user pastes the PARENT folder with quotes (Explorer "Copy as path")
    rep = userdata.import_folder(f'"{old.parent}"')
    rows = read_rows(led)
    assert [r["ts_utc"] for r in rows] == ["2026-11-01T15:00:00+00:00", "2026-11-02T15:00:00+00:00",
                                         "2026-11-08T15:00:00+00:00"]               # union, sorted, no duplicate
    preds = sorted(p.name for p in (env["data"] / "predictions").rglob("*.json"))
    assert preds == ["2026_09_BUF_MIN__20261106T010000Z__aaaa.json", "2026_10_CLE_NO__20261107T010000Z__bbbb.json"]
    assert (env["data"] / "overrides" / "2026_wk09.yaml").exists()
    assert (env["data"] / "tuning" / "20261107-220000" / "summary.json").exists()
    assert not (env["data"] / "tuning" / "20261105-074738").exists()                 # shipped example skipped
    local = yaml.safe_load((env["data"] / "config.local.yaml").read_text())
    assert local == {"sim": {"n_sims": 4000}}                                           # your settings untouched
    assert "recent_games" in (env["data"] / "config.local.yaml.from-import").read_text()   # the old ones set aside
    assert (env["data"] / ".env").read_text() == "NTFY_TOPIC=old-topic\n"
    assert any("before-import" in b.name for b in userdata.list_backups())             # backed up first

    # 3) importing again changes nothing
    before = {p: p.read_bytes() for p in env["data"].rglob("*") if p.is_file()}
    rep2 = userdata.import_folder(old)
    assert rep2["added"] == 0 and rep2["records added"] == 0
    assert {p: p.read_bytes() for p in env["data"].rglob("*") if p.is_file()} == before

    # 4) back up, lose the data folder, get warned, restore -> identical
    z = userdata.backup(None, "manual")
    assert z.exists() and z.parent == env["backups"]
    shutil.rmtree(env["data"])
    shutil.rmtree(env["code"] / "signals")
    shutil.rmtree(env["code"] / "predictions")
    (env["code"] / "config.local.yaml").unlink()
    userdata.ensure()
    st = userdata.status()
    assert st["empty"] and st["warn_empty"]
    userdata.restore(st["backups"][0])
    def mine(p):        # GridSim's own bookkeeping files (folder ID etc.) differ by design after a loss
        return p.name not in ("regime_changes.yaml", userdata.ID_FILE, userdata.KNOWN_FILE, userdata.LEGACY_FILE)
    after = {p: p.read_bytes() for p in env["data"].rglob("*") if p.is_file() and mine(p)}
    assert after == {p: b for p, b in before.items() if mine(p)}
    assert not userdata.status()["warn_empty"]


def test_conflicting_files_keep_yours(env):
    src = env["tmp"] / "old"
    make_install(src, [], ["g__1__h.json"], {"overrides/2026_wk10.yaml": "MIN: {out: [A]}\n"})
    make_install(env["data"], [], [], {"overrides/2026_wk10.yaml": "MIN: {out: [B]}\n"})
    p = env["data"] / "predictions" / "2026" / "wk09" / "g__1__h.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"frozen": {"created_utc": "x"}, "mine": true}')
    rep = userdata.import_folder(src)
    assert json.loads(p.read_text())["mine"] is True                                  # frozen prediction untouched
    assert (env["data"] / "overrides" / "2026_wk10.yaml").read_text() == "MIN: {out: [B]}\n"
    assert (env["data"] / "overrides" / "2026_wk10.yaml.from-import").read_text() == "MIN: {out: [A]}\n"
    assert len(rep["notes"]) == 2
    assert not list((env["data"] / "predictions").rglob("*.from-import"))             # never a stray prediction


def test_backups_are_pruned_and_never_half_written(env):
    make_install(env["data"], ["2026-11-01T15:00:00+00:00"], [])
    cfg = load_config().copy_with({"backup.keep": 3})
    made = [userdata.backup(cfg, f"r{i}") for i in range(5)]
    left = userdata.list_backups(cfg)
    assert len(left) == 3 and left[0] == made[-1]
    assert not list(env["backups"].glob("*.partial"))
    assert userdata.auto_backup(cfg, "daily", min_interval_h=24) is None               # one was just made


def test_backup_folder_inside_the_data_folder_is_not_zipped_into_itself(env):
    make_install(env["data"], ["2026-11-01T15:00:00+00:00"], [])
    cfg = load_config().copy_with({"backup.dir": str(env["data"] / "backups")})
    a = userdata.backup(cfg, "a")
    b = userdata.backup(cfg, "b")
    import zipfile
    names = zipfile.ZipFile(b).namelist()
    assert not any(n.startswith("backups/") for n in names) and a.name not in " ".join(names)


def test_bad_import_paths_explain_themselves(env):
    with pytest.raises(ValueError, match="Can't find"):
        userdata.import_folder(env["tmp"] / "nope")
    (env["tmp"] / "empty").mkdir()
    with pytest.raises(ValueError, match="No GridSim data"):
        userdata.import_folder(env["tmp"] / "empty")
    env["data"].mkdir(parents=True, exist_ok=True)
    (env["data"] / "predictions").mkdir()
    with pytest.raises(ValueError, match="own data folder"):
        userdata.import_folder(env["data"])


def test_worker_count_respects_memory_and_cores(monkeypatch):
    from gridsim import resources
    monkeypatch.setattr(resources.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(resources, "memory_gb", lambda: (32.0, 22.0))
    cfg = load_config()
    # whole PC at most 80%: 22 GB free - 6.4 GB headroom = 15.6 GB for GridSim, minus 1.5 for the main process.
    # Tuning on 4 seasons, one season per worker (~2.05 GB each): 6 workers (7 would need 15.85 GB).
    assert resources.tune_worker_count(cfg, 4) == 6
    assert resources.worker_count(cfg, per_worker_gb=resources.season_worker_gb(4)) == 3
    monkeypatch.setattr(resources, "memory_gb", lambda: (256.0, 250.0))
    assert resources.tune_worker_count(cfg, 4) == 30                                   # 32 cores - 2 kept free
    monkeypatch.setattr(resources, "memory_gb", lambda: (16.0, 2.0))
    assert resources.tune_worker_count(cfg, 4) == 1                                    # never zero
    monkeypatch.setattr(resources, "memory_gb", lambda: (32.0, None))                  # e.g. macOS: no crash
    assert 1 <= resources.tune_worker_count(cfg, 4, verbose=True) <= 30
    monkeypatch.setattr(resources, "memory_gb", lambda: (None, None))
    assert resources.tune_worker_count(cfg, 4) == 4
    assert cfg.copy_with({"resources.free_cores": 40}) and resources.worker_count(cfg.copy_with({"resources.free_cores": 40})) == 1


def test_tuning_workers_each_hold_one_season():
    from gridsim.backtest import PinnedPool

    class P(PinnedPool):
        def __init__(self, n, seasons=None):
            self.ex, self.seasons = [None] * n, list(dict.fromkeys(seasons or []))
    for n in (4, 6, 7, 9):
        p, held = P(n, [2022, 2023, 2024, 2025]), {}
        for s_ in (2022, 2023, 2024, 2025):
            for w in range(3, 19):
                held.setdefault(p.slot(s_, w), set()).add(s_)
        assert all(len(v) == 1 for v in held.values()) and len(held) == n


# ---- review findings ----------------------------------------------------------------------------------------------

def test_code_folder_data_is_taken_once_so_resets_stick(env):
    make_install(env["code"], [], [], {"config.local.yaml": "sim:\n  n_sims: 5000\nscanner:\n  min_edge: 0.07\n",
                                       "overrides/2026_wk09.yaml": "MIN: {out: [X]}\n",
                                       "signals/alert_state.json": '{"T|YES": {"edge": 0.1, "ts": "2026"}}'})
    userdata.ensure()
    from gridsim.config import load_local, save_local
    loc = load_local()
    loc["sim"].pop("n_sims")                          # user sets it back to the default in Settings
    save_local(loc)
    (env["data"] / "overrides" / "2026_wk09.yaml").unlink()      # user clears the lineup change
    (env["data"] / "signals" / "alert_state.json").write_text("{}")   # scan dropped a stale alert key
    rep = userdata.ensure()
    assert "n_sims" not in (load_local().get("sim") or {})
    assert not (env["data"] / "overrides" / "2026_wk09.yaml").exists()
    assert json.loads((env["data"] / "signals" / "alert_state.json").read_text()) == {}
    assert not rep.get("added") and not rep.get("records added")
    (env["data"] / "config.local.yaml").unlink()      # Reset to defaults
    userdata.ensure()
    assert not (env["data"] / "config.local.yaml").exists()
    # an older GridSim copy writes something NEW into the code folder later: that is still picked up
    make_install(env["code"], [], ["2026_10_X_Y__20261112T010000Z__cccc.json"])
    userdata.ensure()
    assert list((env["data"] / "predictions").rglob("2026_10_X_Y__*.json"))


def test_lost_data_folder_keeps_warning_and_keeps_the_good_backups(env):
    cfg = load_config().copy_with({"backup.keep": 3})
    make_install(env["data"], ["2026-11-01T15:00:00+00:00"], [f"g{i}__1__h.json" for i in range(20)])
    good = [userdata.backup(cfg, f"good{i}") for i in range(3)]
    shutil.rmtree(env["data"])                        # the data folder is lost
    userdata.ensure()
    write_ledger(env["data"] / "signals" / "ledger.csv", [])     # a scheduled scan writes a header-only ledger
    (env["data"] / "predictions").mkdir()
    (env["data"] / "predictions" / "new__1__h.json").write_text("{}")   # ... and a Friday lock-in happens
    st = userdata.status(cfg)
    assert st["warn_empty"] and st["restore_candidate"] == good[-1]
    for _ in range(6):                                # automatic backups pause; nothing is rotated out
        assert userdata.auto_backup(cfg, "daily") is None
    userdata.backup(cfg, "manual")                    # even manual backups never prune the lost folder's newest 3
    assert all(g.exists() for g in good)
    userdata.restore(st["restore_candidate"], cfg)
    assert not userdata.status(cfg)["warn_empty"]
    assert len(list((env["data"] / "predictions").rglob("*.json"))) == 21


def test_start_fresh_stops_the_warning(env):
    make_install(env["data"], ["2026-11-01T15:00:00+00:00"], ["a__1__h.json"])
    userdata.backup(None, "x")
    shutil.rmtree(env["data"])
    userdata.ensure()
    assert userdata.status()["warn_empty"]
    userdata.start_fresh()
    assert not userdata.status()["warn_empty"]


def test_zip_made_by_explorer_is_found_inside_its_folder(env):
    import zipfile
    old = env["tmp"] / "x" / "GameSim-claude-friendly-turing-wk7xhb"
    make_install(old, ["2026-11-01T15:00:00+00:00"], ["a__1__h.json"])
    z = env["tmp"] / "GameSim.zip"
    with zipfile.ZipFile(z, "w") as zz:
        for f in old.rglob("*"):
            if f.is_file():
                zz.write(f, f.relative_to(old.parent).as_posix())
    rep = userdata.import_folder(z)
    assert rep["added"] == 2 and rep["source"] == "GameSim.zip"
    empty = env["tmp"] / "empty.zip"
    with zipfile.ZipFile(empty, "w") as zz:
        zz.writestr("readme.txt", "hi")
    with pytest.raises(ValueError, match="No GridSim data in empty.zip"):
        userdata.import_folder(empty)


def test_backup_folder_that_contains_the_data_folder_still_backs_up_everything(env):
    make_install(env["data"], ["2026-11-01T15:00:00+00:00"], ["a__1__h.json"])
    import zipfile
    for where in (env["tmp"], env["data"]):           # the user folder itself / the data folder itself
        z = userdata.backup(load_config().copy_with({"backup.dir": str(where)}), "x")
        names = zipfile.ZipFile(z).namelist()
        assert "signals/ledger.csv" in names and any(n.startswith("predictions/") for n in names)
        assert not any(n.startswith(userdata.BACKUP_PREFIX) for n in names)


def test_backup_dir_text_is_expanded_or_ignored(env, monkeypatch):
    monkeypatch.setenv("OneDrive", str(env["tmp"] / "OD"))
    assert userdata.normalize_dir('"%OneDrive%/GridSim Backups"') == env["tmp"] / "OD" / "GridSim Backups"
    assert userdata.normalize_dir("relative/folder") is None
    assert userdata.normalize_dir("%NOPE_NOT_SET%/x") is None
    cfg = load_config().copy_with({"backup.dir": "relative/folder"})
    assert userdata.backup_dir(cfg).is_absolute()


def test_same_game_graded_in_two_installs_counts_once(env):
    from gridsim import live
    cols = live.SCORE_FIELDS

    def card(root, rows):
        p = root / "predictions" / "scorecard.csv"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, 0.5) for c in cols})
    base = {"season": 2026, "week": 9, "home": "MIN", "away": "BUF", "home_score": 24, "away_score": 20,
            "config_hash": "h", "home_win": 1.0, "p_tie": 0.0}
    card(env["data"], [{**base, "game_id": "G1", "pred_file": "A", "created_utc": "2026-11-04T10:00:00", "p_home": 0.6}])
    old = env["tmp"] / "old"
    card(old, [{**base, "game_id": "G1", "pred_file": "B", "created_utc": "2026-11-06T23:00:00", "p_home": 0.7},
               {**base, "game_id": "G2", "pred_file": "C", "created_utc": "2026-11-06T23:00:00", "p_home": 0.5}])
    rep = userdata.import_folder(old)
    rows = read_rows(env["data"] / "predictions" / "scorecard.csv")
    assert sorted((r["game_id"], r["pred_file"]) for r in rows) == [("G1", "B"), ("G2", "C")]
    assert any("graded in both" in n for n in rep["notes"])
    assert live.scorecard(load_config())["overall"]["games"] == 2


def test_unreadable_files_are_reported_and_the_rest_is_still_merged(env, monkeypatch):
    old = env["tmp"] / "old"
    make_install(old, ["2026-11-01T15:00:00+00:00"], ["a__1__h.json", "b__1__h.json"])
    real = shutil.copy2

    def flaky(src, dst, *a, **k):
        if "a__1__h" in str(src):
            raise OSError(362, "The cloud file provider is not running")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(userdata.shutil, "copy2", flaky)
    rep = userdata.import_folder(old)
    assert len(rep["failed"]) == 1 and "cloud file provider" in rep["failed"][0]
    assert (env["data"] / "signals" / "ledger.csv").exists()
    assert list((env["data"] / "predictions").rglob("b__1__h.json"))
    assert not list((env["data"] / "predictions").rglob("*.partial"))
    assert "could not be read" in userdata.format_report(rep)


def test_reconstructed_predictions_are_kept_apart_and_never_counted(env):
    from gridsim import live
    cfg = load_config()
    season, week, games = live.week_slate(cfg, 2026, 9)
    g = next(x for x in games if x.get("home_score") is not None)
    live_slate = live.week_slate
    paths = []
    try:
        live.week_slate = lambda c, s, w: (season, week, [g])
        paths = live.reconstruct_week(cfg, 2026, 9, sims=200, verbose=False)
    finally:
        live.week_slate = live_slate
    assert len(paths) == 1 and paths[0].parent.name == "reconstructed"
    frozen = json.loads(paths[0].read_text())["frozen"]
    assert frozen["provenance"] == "reconstructed" and frozen["data_through_week"] == 8
    rec = env["data"] / "predictions" / "scorecard_reconstructed.csv"
    assert len(read_rows(rec)) == 1
    assert not (env["data"] / "predictions" / "scorecard.csv").exists()               # live record untouched
    assert live.scorecard(cfg, 2026) == {"games": 0}
    # the real grader never picks a reconstructed file
    live.grade(cfg, 2026, 9, verbose=False)
    sc = env["data"] / "predictions" / "scorecard.csv"
    assert not sc.exists() or all("reconstructed" not in r["pred_file"] for r in read_rows(sc))


def test_backfilled_closing_prices(env, monkeypatch):
    from gridsim import scanner
    ko = "2026-11-08T18:00:00+00:00"
    write_ledger(env["data"] / "signals" / "ledger.csv",
                 [ledger_row("2026-11-08T12:00:00+00:00", kickoff_utc=ko)])
    import datetime as dt
    ko_ts = int(dt.datetime.fromisoformat(ko).timestamp())

    class FakeKalshi:
        def __init__(self, cfg):
            pass

        def nfl_series(self):
            return {"moneyline": "KXNFLGAME", "spread": "KXNFLSPREAD", "total": "KXNFLTOTAL"}

        def candlesticks(self, series, ticker, start, end, period_minutes=60):
            mk = lambda t, b, a: {"end_period_ts": t, "yes_bid": {"close_dollars": b}, "yes_ask": {"close_dollars": a}}
            return [mk(ko_ts - 7200, "0.40", "0.42"), mk(ko_ts - 3600, "0.55", "0.57"), mk(ko_ts + 3600, "0.9", "0.95")]

    monkeypatch.setattr(scanner, "Kalshi", FakeKalshi)
    cfg = load_config()
    rep = scanner.backfill_closing_prices(cfg, 2026, 10, verbose=False)
    assert rep == {"needed": 1, "filled": 1, "no_history": 0}
    snap = next((env["data"] / "signals" / "snapshots").glob("*_backfilled_*.json"))
    assert json.loads(snap.read_text())["backfilled"] is True
    # the last candle at or before kickoff is the closing price (not the one after kickoff)
    assert scanner._closing_price(cfg, "2026_10_CLE_NO", "KXNFLSPREAD-26NOV08CLENO-NO3", "YES", ko) == pytest.approx(0.56)
    assert scanner.backfill_closing_prices(cfg, 2026, 10, verbose=False)["needed"] == 0   # nothing left to do


# ---- second review round -------------------------------------------------------------------------------------------

def fake_backup(where: Path, name: str, info: dict, files: dict | None = None) -> Path:
    import zipfile
    where.mkdir(parents=True, exist_ok=True)
    z = where / f"{userdata.BACKUP_PREFIX}{name}.zip"
    with zipfile.ZipFile(z, "w") as zz:
        for rel, text in (files or {}).items():
            zz.writestr(rel, text)
        zz.writestr("gridsim_backup.json", json.dumps(info))
    return z


def test_two_program_folders_sharing_one_data_folder_dont_undo_resets(env, monkeypatch):
    code2 = env["tmp"] / "OneDrive" / "Desktop" / "GameSim"
    for c in (env["code"], code2):
        make_install(c, [], [], {"config.local.yaml": "sim:\n  n_sims: 5000\n", "overrides/2026_wk09.yaml": "MIN: {}\n"})
    for _ in range(3):
        for c in (env["code"], code2):
            monkeypatch.setattr(userdata, "REPO_ROOT", c)
            userdata.ensure()
        (env["data"] / "config.local.yaml").unlink(missing_ok=True)          # Reset to defaults
        (env["data"] / "overrides" / "2026_wk09.yaml").unlink(missing_ok=True)
    for c in (env["code"], code2):
        monkeypatch.setattr(userdata, "REPO_ROOT", c)
        rep = userdata.ensure()
        assert not rep.get("added")
    assert not (env["data"] / "config.local.yaml").exists()
    assert not (env["data"] / "overrides" / "2026_wk09.yaml").exists()


def test_a_bad_code_folder_file_does_not_stop_the_once_only_rule(env):
    make_install(env["code"], [], [], {"config.local.yaml": "sim.n_sims=5000\n", "overrides/2026_wk09.yaml": "MIN: {}\n"})
    rep = userdata.ensure()
    assert "error" not in rep and any("not a list of settings" in n for n in rep["notes"])
    (env["data"] / "overrides" / "2026_wk09.yaml").unlink()
    userdata.ensure()
    assert not (env["data"] / "overrides" / "2026_wk09.yaml").exists()


def test_backups_from_before_ids_existed(env):
    old_info = {"gridsim_backup": 1, "created": "2026-11-09T06:00:00+00:00", "reason": "grade",
                "counts": {"predictions": 20, "signal records": 100}}
    ub = [fake_backup(env["backups"], f"20261109-06000{i}-grade", old_info) for i in range(3)]
    # an existing folder getting its first ID claims them: no false alarm
    make_install(env["data"], ["2026-11-01T15:00:00+00:00"], [f"g{i}__1__h.json" for i in range(20)])
    (env["data"] / "signals" / "ledger.csv").write_text("x\n" * 101)
    assert not userdata.status()["warn_empty"]
    # ...but if the folder is lost, they count as lost data, are never pruned, and auto backups pause
    shutil.rmtree(env["data"])
    userdata.ensure()
    write_ledger(env["data"] / "signals" / "ledger.csv", [])
    st = userdata.status()
    assert st["warn_empty"] and st["restore_candidate"] in ub
    assert userdata.auto_backup(None, "daily") is None
    cfg = load_config().copy_with({"backup.keep": 1})
    for i in range(3):
        userdata.backup(cfg, f"m{i}")
    assert all(z.exists() for z in ub)


def test_data_gone_but_id_file_kept_still_warns(env):
    make_install(env["data"], ["2026-11-01T15:00:00+00:00"], [f"g{i}__1__h.json" for i in range(5)])
    userdata.backup(None, "grade")
    shutil.rmtree(env["data"] / "predictions")
    write_ledger(env["data"] / "signals" / "ledger.csv", [])
    st = userdata.status()
    assert st["warn_empty"]
    userdata.restore(st["restore_candidate"])
    assert not userdata.status()["warn_empty"]


def test_another_pc_sharing_the_backup_folder_is_not_a_false_alarm(env):
    make_install(env["data"], ["2026-11-01T15:00:00+00:00"], ["a__1__h.json"])
    userdata.data_id()
    import datetime as dt
    later = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat(timespec="seconds")
    other = {"gridsim_backup": 1, "created": later, "reason": "daily", "host": "LAPTOP",
             "data_id": "f" * 32, "counts": {"predictions": 50, "signal records": 900}}
    fake_backup(env["backups"], "20991109-070000-daily", other)       # the laptop keeps backing up its own folder
    assert not userdata.status()["warn_empty"]
    userdata.backup(load_config().copy_with({"backup.keep": 1}), "x")
    assert (env["backups"] / f"{userdata.BACKUP_PREFIX}20991109-070000-daily.zip").exists()   # never pruned
    # a brand-new PC: another computer's backups made before this folder existed are offered for restore
    shutil.rmtree(env["data"])
    old_pc = {**other, "created": "2026-01-01T00:00:00+00:00", "data_id": "e" * 32, "host": "OLDPC"}
    fake_backup(env["backups"], "20260101-000000-daily", old_pc)
    userdata.ensure()
    st = userdata.status()
    assert st["warn_empty"] and any(z.name.endswith("20260101-000000-daily.zip") for z in st["lost_backups"])
    assert not any("20991109" in z.name for z in st["lost_backups"])   # the laptop's later backups are its own


def test_deleted_id_file_with_data_present_is_not_lost(env):
    make_install(env["data"], ["2026-11-01T15:00:00+00:00"], ["a__1__h.json"])
    old_id = userdata.data_id()
    userdata.backup(None, "grade")
    (env["data"] / userdata.ID_FILE).unlink()
    assert not userdata.status()["warn_empty"] and userdata.data_id() == old_id
    (env["data"] / userdata.ID_FILE).write_bytes(b"\xff\xfe garbage")
    assert not userdata.status()["warn_empty"] and userdata.data_id() == old_id


def test_second_loss_restore_clears_the_warning(env):
    make_install(env["data"], ["2026-11-01T15:00:00+00:00"], ["a__1__h.json"])
    userdata.backup(None, "x")
    for _ in range(2):                                 # lose it, restore, lose it again, restore again
        shutil.rmtree(env["data"])
        userdata.ensure()
        st = userdata.status()
        assert st["warn_empty"]
        userdata.restore(st["restore_candidate"])
        assert not userdata.status()["warn_empty"]
        import time
        time.sleep(1.1)                                # backups a second apart, as in real life
        userdata.backup(None, "after")


def test_custom_backup_folder_is_still_found_after_the_data_folder_is_lost(env):
    usb = env["tmp"] / "E" / "GridSim Backups"
    make_install(env["data"], ["2026-11-01T15:00:00+00:00"], ["a__1__h.json"])
    userdata.backup(load_config().copy_with({"backup.dir": str(usb)}), "x")
    shutil.rmtree(env["data"])                         # the backup.dir setting is gone with it
    userdata.ensure()
    st = userdata.status(load_config())
    assert st["warn_empty"] and st["restore_candidate"].parent == usb


def test_program_folder_copies_wait_until_after_the_restore(env):
    # code folder still holds the old version's settings; the user later changed them in the data folder
    make_install(env["code"], [], [], {"config.local.yaml": "sim:\n  n_sims: 3000\n"})
    userdata.ensure()
    from gridsim.config import load_local, save_local
    save_local({"sim": {"n_sims": 10000}})
    make_install(env["data"], ["2026-11-01T15:00:00+00:00"], ["a__1__h.json"])
    userdata.backup(None, "x")
    shutil.rmtree(env["data"])
    rep = userdata.ensure()                            # data lost: the move from the program folder waits
    assert rep.get("deferred") and not (env["data"] / "config.local.yaml").exists()
    userdata.restore(userdata.status()["restore_candidate"])
    assert load_local()["sim"]["n_sims"] == 10000       # the backup's (newer) settings, not the stale copy
    assert not userdata.ensure().get("added")


def test_fewer_workers_than_seasons_hold_whole_seasons():
    from gridsim.backtest import PinnedPool
    from gridsim.resources import season_worker_gb  # noqa: F401

    class P(PinnedPool):
        def __init__(self, n, seasons=None):
            self.ex, self.seasons = [None] * n, list(dict.fromkeys(seasons or []))
    for n in (1, 2, 3):
        p, held = P(n, [2022, 2023, 2024, 2025]), {}
        for s_ in (2022, 2023, 2024, 2025):
            for w in range(3, 19):
                held.setdefault(p.slot(s_, w), set()).add(s_)
        assert max(len(v) for v in held.values()) == -(-4 // n)


# ---- final review round: content, not counts -------------------------------------------------------------------------

def test_partial_loss_keeps_warning_however_much_is_added_later(env):
    cfg = load_config().copy_with({"backup.keep": 2})
    make_install(env["data"], [f"2026-11-0{i}T15:00:00+00:00" for i in range(1, 6)],
                 [f"2026_09_G{i}__1__h.json" for i in range(5)])
    good = userdata.backup(cfg, "grade")
    import time
    time.sleep(1.1)
    shutil.rmtree(env["data"] / "predictions")       # one folder goes missing; the ledger and the ID file stay
    # many scans and lock-ins later: far MORE predictions and rows than the backup had
    write_ledger(env["data"] / "signals" / "ledger.csv",
                 [ledger_row(f"2026-11-0{i}T15:00:00+00:00") for i in range(1, 6)]
                 + [ledger_row(f"2026-11-2{i % 10}T15:00:00+00:00", ticker=f"T{i}") for i in range(200)])
    make_install(env["data"], [], [f"2026_10_N{i}__1__h.json" for i in range(40)])
    write_ledger(env["data"] / "signals" / "ledger.csv",
                 [ledger_row(f"2026-11-0{i}T15:00:00+00:00") for i in range(1, 6)]
                 + [ledger_row(f"2026-11-2{i % 10}T15:00:00+00:00", ticker=f"T{i}") for i in range(200)])
    st = userdata.status(cfg)
    assert st["warn_empty"] and st["restore_candidate"] == good
    assert "5 predictions" in st["missing"][good.name]
    for i in range(4):                                # manual backups never prune the one holding the lost files
        time.sleep(1.1)
        userdata.backup(cfg, f"m{i}")
    assert good.exists()
    userdata.restore(good, cfg)
    assert not userdata.status(cfg)["warn_empty"]


def test_lost_ledger_rows_are_found_even_after_new_scans(env):
    make_install(env["data"], [f"2026-11-0{i}T15:00:00+00:00" for i in range(1, 9)], ["a__1__h.json"])
    good = userdata.backup(None, "grade")
    write_ledger(env["data"] / "signals" / "ledger.csv",       # older rows lost; 50 newer rows appended since
                 [ledger_row(f"2026-11-2{i % 10}T1{i % 10}:00:00+00:00", ticker=f"T{i}") for i in range(50)])
    st = userdata.status()
    assert st["warn_empty"] and "8 signal records" in st["missing"][good.name]


def test_renamed_pc_still_checks_its_own_backups(env, monkeypatch):
    make_install(env["data"], ["2026-11-01T15:00:00+00:00"], ["a__1__h.json", "b__1__h.json"])
    monkeypatch.setenv("COMPUTERNAME", "DESKTOP-7H2K9QF")
    userdata.backup(None, "grade")
    monkeypatch.setenv("COMPUTERNAME", "JOHNS-PC")
    assert not userdata.status()["warn_empty"]
    shutil.rmtree(env["data"] / "predictions")
    assert userdata.status()["warn_empty"]


def test_start_fresh_never_lets_pruning_delete_what_was_set_aside(env):
    cfg = load_config().copy_with({"backup.keep": 1})
    make_install(env["data"], ["2026-11-01T15:00:00+00:00"], ["a__1__h.json"])
    good = userdata.backup(cfg, "grade")
    shutil.rmtree(env["data"] / "predictions")
    assert userdata.status(cfg)["warn_empty"]
    userdata.start_fresh(cfg)
    assert not userdata.status(cfg)["warn_empty"]
    import time
    for i in range(3):
        time.sleep(1.1)
        assert userdata.auto_backup(cfg, f"d{i}") is not None
    assert good.exists()


def test_upgrade_with_pre_id_backups_is_not_a_false_alarm(env):
    make_install(env["data"], ["2026-11-01T15:00:00+00:00"], [f"g{i}__1__h.json" for i in range(14)])
    userdata.data_id()                                # an ID already exists (made by an earlier build)
    info = {"gridsim_backup": 1, "created": "2026-11-09T06:00:00+00:00", "reason": "lock-in",
            "counts": {"predictions": 14, "signal records": 1}}
    fake_backup(env["backups"], "20261109-060000-lock-in", info)
    assert not userdata.status()["warn_empty"]
    assert userdata.auto_backup(None, "daily") is not None


def test_kept_aside_copies_come_back_on_restore(env):
    make_install(env["data"], ["2026-11-01T15:00:00+00:00"], ["a__1__h.json"],
                 {".env": "NTFY_TOPIC=new\n", ".env.from-import": "NTFY_TOPIC=old\n"})
    z = userdata.backup(None, "x")
    shutil.rmtree(env["data"])
    userdata.ensure()
    userdata.restore(z)
    assert (env["data"] / ".env.from-import").read_text() == "NTFY_TOPIC=old\n"


def test_backup_inventory_matches_what_was_zipped(env):
    import zipfile
    make_install(env["data"], ["2026-11-01T15:00:00+00:00", "2026-11-02T15:00:00+00:00"], ["a__1__h.json"])
    z = userdata.backup(None, "x")
    info = userdata.backup_info(z)
    names = set(zipfile.ZipFile(z).namelist())
    assert set(info["inventory"]["files"]) <= names
    assert info["inventory"]["rows"]["signals/ledger.csv"] == {"by_ts": {"2026-11-01T15:00:00+00:00": 1,
                                                                         "2026-11-02T15:00:00+00:00": 1}}
    assert info["counts"]["signal records"] == 2
    assert info["counts"]["predictions"] == 1 and info["host"]


def test_file_lock_serialises_and_recovers(env):
    import threading
    import time
    f = env["tmp"] / "x.csv"
    order = []

    def hold():
        with userdata.file_lock(f):
            order.append("a-in")
            time.sleep(0.6)
            order.append("a-out")
    th = threading.Thread(target=hold)
    th.start()
    time.sleep(0.1)
    with userdata.file_lock(f):
        order.append("b-in")
    th.join()
    assert order == ["a-in", "a-out", "b-in"]
    lk = env["tmp"] / "x.csv.lock"
    lk.write_text("")
    os_old = time.time() - 3600
    import os
    os.utime(lk, (os_old, os_old))                    # left over from a crash: taken over
    with userdata.file_lock(f, timeout=2):
        pass
    assert not lk.exists()


# ---- last recheck -----------------------------------------------------------------------------------------------------

def test_older_rows_brought_in_cannot_hide_lost_rows(env):
    make_install(env["data"], [f"2026-11-0{i}T15:00:00+00:00" for i in range(1, 9)], ["a__1__h.json"])
    good = userdata.backup(None, "grade")
    write_ledger(env["data"] / "signals" / "ledger.csv", [])                 # the ledger loses its rows
    old = env["tmp"] / "old"
    make_install(old, [f"2026-10-{i:02d}T15:00:00+00:00" for i in range(1, 11)], [])   # 10 OLDER rows elsewhere
    userdata.import_folder(old)
    st = userdata.status()
    assert st["warn_empty"] and "8 signal records" in st["missing"][good.name]


def test_old_pc_keeps_backing_up_after_moving_to_a_new_pc(env, monkeypatch):
    monkeypatch.setenv("COMPUTERNAME", "OLDPC")
    make_install(env["data"], ["2026-11-01T15:00:00+00:00"], ["a__1__h.json"])
    old_zip = userdata.backup(None, "daily")
    old_id = userdata.data_id()
    # new PC: empty folder, restores the old PC's backup
    shutil.rmtree(env["data"])
    monkeypatch.setenv("COMPUTERNAME", "NEWPC")
    userdata.ensure()
    assert userdata.status()["warn_empty"]
    userdata.restore(old_zip)
    assert not userdata.status()["warn_empty"]
    import time
    time.sleep(1.1)
    # the old PC is still on and makes a later backup of ITS folder with a new snapshot
    later = {"gridsim_backup": 2, "created": userdata._utcnow(), "reason": "daily", "host": "OLDPC",
             "data_id": old_id, "counts": {"predictions": 1, "signal records": 1},
             "inventory": {"files": ["signals/snapshots/new.json"], "rows": {}}}
    fake_backup(env["backups"], "20991231-000000-daily", later)
    assert not userdata.status()["warn_empty"]
    assert userdata.auto_backup(None, "lock-in") is not None


def test_lock_left_by_a_crashed_process_is_taken_over_at_once(env):
    import subprocess
    import sys
    import time
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    f = env["tmp"] / "ledger.csv"
    (env["tmp"] / "ledger.csv.lock").write_text(str(p.pid))
    t0 = time.time()
    with userdata.file_lock(f, timeout=30):
        pass
    assert time.time() - t0 < 2


def test_half_written_last_line_is_dropped_before_appending(env):
    f = env["tmp"] / "x.csv"
    f.write_bytes(b"a,b\n1,2\n3,")
    userdata.heal_partial_line(f)
    assert f.read_bytes() == b"a,b\n1,2\n"
    f.write_bytes(b"a,b")
    userdata.heal_partial_line(f)
    assert f.read_bytes() == b""


def test_restore_never_brings_settings_back_after_a_reset(env):
    from gridsim.config import save_local
    make_install(env["data"], ["2026-11-01T15:00:00+00:00"], ["a__1__h.json"])
    save_local({"sim": {"n_sims": 9000}})
    z = userdata.backup(None, "x")
    import time
    time.sleep(1.1)
    (env["data"] / "config.local.yaml").unlink()      # Settings -> Reset to defaults
    userdata.note_settings_reset()
    rep = userdata.restore(z)
    assert not (env["data"] / "config.local.yaml").exists()
    assert any("reset them to the defaults" in n for n in rep["notes"])


def test_first_start_after_an_update_makes_a_full_baseline_backup(env):
    make_install(env["data"], ["2026-11-01T15:00:00+00:00"], [f"g{i}__1__h.json" for i in range(3)])
    did = userdata.data_id()
    info = {"gridsim_backup": 1, "created": userdata._utcnow(), "reason": "daily", "data_id": did,
            "data_dir": str(env["data"]), "counts": {"predictions": 3, "signal records": 1}}
    fake_backup(env["backups"], "20991231-000000-daily", info)     # made an hour ago by an older build
    z = userdata.auto_backup(None, "daily", min_interval_h=24)
    assert z is not None and "inventory" in userdata.backup_info(z)

