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
    assert local["sim"]["n_sims"] == 4000                                               # current setting wins
    assert local["players"]["recent_games"] == 8                                        # missing one filled in
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
    after = {p: p.read_bytes() for p in env["data"].rglob("*") if p.is_file() and p.name != "regime_changes.yaml"}
    assert after == {p: b for p, b in before.items() if p.name != "regime_changes.yaml"}
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
    # whole PC at most 80%: 22 GB free - 6.4 GB headroom = 15.6 GB for GridSim; minus 1.5 main -> 4 workers of 3 GB
    assert resources.worker_count(cfg) == 4
    monkeypatch.setattr(resources, "memory_gb", lambda: (256.0, 250.0))
    assert resources.worker_count(cfg) == 30                                           # 32 cores - 2 kept free
    monkeypatch.setattr(resources, "memory_gb", lambda: (16.0, 2.0))
    assert resources.worker_count(cfg) == 1                                            # never zero


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
