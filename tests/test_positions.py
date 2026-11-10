"""Position watch (read-only) and the trust gate's counting. No network: prices and sims are passed in."""
from __future__ import annotations

import csv
import datetime as dt
import json

import pytest

from gridsim import positions as pos
from gridsim import scanner
from gridsim.config import load_config
from gridsim.scanner import LEDGER_FIELDS

TICK = "KXNFLGAME-26NOV15GBMIN-MIN"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIDSIM_DATA", str(tmp_path / "GridSim Data"))
    monkeypatch.setenv("HOME", str(tmp_path))
    for v in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        monkeypatch.delenv(v, raising=False)
    sent = []
    import gridsim.notify as notify
    monkeypatch.setattr(notify, "send", lambda cfg, title, message, url=None, priority="default": sent.append(title) or ["test"])
    return {"data": tmp_path / "GridSim Data", "sent": sent}


def game(hours_to_kickoff=48.0, final=None):
    ko = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours_to_kickoff)
    et = ko.astimezone(dt.timezone(dt.timedelta(hours=-5)))
    g = {"game_id": "2026_11_GB_MIN", "season": 2026, "week": 11, "home_team": "MIN", "away_team": "GB",
         "gameday": et.strftime("%Y-%m-%d"), "gametime": et.strftime("%H:%M"), "home_score": None, "away_score": None}
    if final:
        g["home_score"], g["away_score"] = final
    return g


def summ(p_home=0.62, listed=(), qb="J.McCarthy"):
    return {"matchup": {"home": "MIN", "away": "GB"}, "n_sims": 2000,
            "win": {"home": {"p": p_home}, "away": {"p": 1 - p_home}, "tie": {"p": 0.0}},
            "hist": {"margin": {"values": [0], "counts": [1]}, "total": {"values": [44], "counts": [1]}},
            "availability": {"teams": {"MIN": {"qb": [{"name": qb, "p_start": 1.0}], "listed": list(listed)},
                                       "GB": {"qb": [{"name": "J.Love", "p_start": 1.0}], "listed": []}}}}


def mk(bid=0.54, ask=0.56):
    return {"moneyline": {"MIN": {"ticker": TICK, "yes_bid": bid, "yes_ask": ask, "no_bid": 1 - ask, "no_ask": 1 - bid}},
            "spread": [], "total": []}


def setup(env, monkeypatch, g=None, **kw):
    cfg = load_config()
    g = g or game()
    monkeypatch.setattr(pos, "_game", lambda cfg_, p: g)
    p = pos.add_position(cfg, game=g, market="moneyline", ticker=TICK, label="MIN wins", side="YES", price=0.55,
                         contracts=100, **kw)
    return cfg, g, p


def check(cfg, s, m, **kw):
    return pos.watch(cfg, notify_on=True, game_data={"2026_11_GB_MIN": (s, m)}, verbose=False, **kw)


def test_first_look_sets_the_baseline_then_a_price_jump_alerts_once(env, monkeypatch):
    cfg, g, p = setup(env, monkeypatch)
    assert check(cfg, summ(), mk()) == []                         # first look: nothing to compare with
    assert pos.load_positions(cfg)[0]["p_side_entry"] == pytest.approx(0.62)
    assert check(cfg, summ(), mk(0.57, 0.59)) == []               # +3¢: below the 6¢ threshold
    a = check(cfg, summ(), mk(0.61, 0.63))                        # +7¢ since the baseline
    assert [x["kind"] for x in a] == ["price"] and "up 7¢" in a[0]["title"]
    assert "you paid 55¢" in a[0]["message"] and "selling now would bring about 61¢" in a[0]["message"]
    assert check(cfg, summ(), mk(0.61, 0.63)) == []               # no repeat
    assert [x["kind"] for x in check(cfg, summ(), mk(0.52, 0.54))] == ["price"]     # −9¢ from the new baseline
    assert len(env["sent"]) == 2 and len(pos.recent_alerts(cfg)) == 2


def test_injury_change_gives_one_alert_with_the_new_numbers(env, monkeypatch):
    cfg, g, p = setup(env, monkeypatch)
    jj = {"name": "J.Jefferson", "team": "MIN", "pos": "WR", "p_play": 0.75, "status": "Questionable"}
    check(cfg, summ(listed=[jj]), mk())
    a = check(cfg, summ(p_home=0.54, listed=[{**jj, "p_play": 0.0, "status": "Out"}]), mk())
    assert [x["kind"] for x in a] == ["injury"]                   # not a second "GridSim's number" alert as well
    assert "J.Jefferson (WR) Questionable (75% to play) → Out" in a[0]["message"]
    assert "GridSim's chance 54% (was 62%)" in a[0]["message"]
    assert check(cfg, summ(p_home=0.54, listed=[{**jj, "p_play": 0.0, "status": "Out"}]), mk()) == []
    a = check(cfg, summ(p_home=0.50, qb="S.Howell", listed=[{**jj, "p_play": 0.0, "status": "Out"}]), mk())
    assert "expected starting QB is now S.Howell (was J.McCarthy)" in a[0]["message"]
    # positions the watch is not asked about (a lineman) don't alert
    ol = {"name": "C.Darrisaw", "team": "MIN", "pos": "T", "p_play": 0.0, "status": "Out"}
    assert check(cfg, summ(p_home=0.50, qb="S.Howell", listed=[{**jj, "p_play": 0.0, "status": "Out"}, ol]), mk()) == []


def test_gridsim_changing_its_number_alerts(env, monkeypatch):
    cfg, g, p = setup(env, monkeypatch)
    check(cfg, summ(0.62), mk())
    assert check(cfg, summ(0.59), mk()) == []                     # 3 points: below the 5-point threshold
    a = check(cfg, summ(0.53), mk())
    assert [x["kind"] for x in a] == ["sim"] and "now 53% (was 62%)" in a[0]["title"]
    assert "below the 55¢ you paid" in a[0]["message"]


def test_no_side_is_the_mirror_image(env, monkeypatch):
    cfg = load_config()
    g = game()
    monkeypatch.setattr(pos, "_game", lambda cfg_, p: g)
    p = pos.add_position(cfg, game=g, market="moneyline", ticker=TICK, label="MIN wins", side="NO", price=0.45, contracts=10)
    v = pos.view(p, summ(0.62), mk(0.54, 0.56), cfg)
    assert v["mid"] == pytest.approx(0.45) and v["sell"] == pytest.approx(0.44) and v["p_side"] == pytest.approx(0.38)


def test_settlement_and_selling_profit_after_fees(env, monkeypatch):
    cfg, g, p = setup(env, monkeypatch)
    from gridsim.markets import fee
    entry_fee = fee(0.55, cfg, 100)
    monkeypatch.setattr(pos, "_game", lambda cfg_, q: game(final=(27, 20)))
    a = pos.watch(cfg, notify_on=True, verbose=False)
    assert [x["kind"] for x in a] == ["settled"] and "WON" in a[0]["message"]
    done = pos.load_positions(cfg)[0]
    assert done["status"] == "settled" and done["pnl"] == pytest.approx(100 - 55 - entry_fee)
    assert pos.watch(cfg, notify_on=True, verbose=False) == []    # settled once
    # a second position, sold early at 60¢: fees on both trades
    monkeypatch.setattr(pos, "_game", lambda cfg_, q: g)
    p2 = pos.add_position(cfg, game=g, market="moneyline", ticker=TICK, label="MIN wins", side="YES", price=0.55, contracts=100)
    r = pos.close_position(cfg, p2["id"], 0.60)
    assert r["pnl"] == pytest.approx(60 - fee(0.60, cfg, 100) - 55 - entry_fee)
    assert pos.close_position(cfg, p2["id"], 0.70) is None        # already closed


def test_near_kickoff_mode_and_started_games(env, monkeypatch):
    cfg, g, p = setup(env, monkeypatch, g=game(hours_to_kickoff=30))
    check(cfg, summ(), mk())
    assert check(cfg, summ(), mk(0.70, 0.72), near_only=True) == []         # 30 h away: the 10-minute task skips it
    monkeypatch.setattr(pos, "_game", lambda cfg_, q: game(hours_to_kickoff=2))
    cfg2 = load_config()
    ps = pos.load_positions(cfg2)
    ps[0]["kickoff_utc"] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)).isoformat()
    pos._save_positions(cfg2, ps)
    assert [x["kind"] for x in check(cfg2, summ(), mk(0.70, 0.72), near_only=True)] == ["price"]
    ps[0]["kickoff_utc"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)).isoformat()
    pos._save_positions(cfg2, ps)
    assert check(cfg2, summ(), mk(0.20, 0.22)) == []                         # in play: left alone


def test_bad_entries_are_refused_and_delete_removes(env, monkeypatch):
    cfg, g, p = setup(env, monkeypatch)
    for bad in ({"price": 55}, {"contracts": 0}, {"side": "MAYBE"}):
        with pytest.raises(ValueError):
            pos.add_position(cfg, **{**dict(game=g, market="moneyline", ticker=TICK, label="MIN wins", side="YES",
                                            price=0.55, contracts=1), **bad})
    pos.delete_position(cfg, p["id"])
    assert pos.load_positions(cfg) == [] and pos.watch(cfg, verbose=False) == []


def test_positions_survive_backup_loss_and_restore(env, monkeypatch):
    import shutil

    from gridsim import userdata
    cfg, g, p = setup(env, monkeypatch)
    check(cfg, summ(), mk())
    check(cfg, summ(), mk(0.62, 0.64))                            # one alert logged
    z = userdata.backup(cfg, "x")
    shutil.rmtree(env["data"])
    userdata.ensure()
    userdata.restore(z)
    assert [q["id"] for q in pos.load_positions(cfg)] == [p["id"]] and len(pos.recent_alerts(cfg)) == 1
    # bringing in another folder adds its positions and keeps yours
    other = env["data"].parent / "other" / "positions"
    other.mkdir(parents=True)
    (other / "positions.json").write_text(json.dumps({"positions": [{"id": "zzzz", "created_utc": "2026-01-01", "status": "sold"},
                                                                    {**p, "price": 0.99}]}))
    userdata.import_folder(other.parent)
    ps = {q["id"]: q for q in pos.load_positions(cfg)}
    assert set(ps) == {p["id"], "zzzz"} and ps[p["id"]]["price"] == 0.55


def test_trust_counts_games_not_repeated_sightings_or_lines(env):
    cfg = load_config()
    gp = cfg.resolve_path("signals") / "graded.csv"
    gp.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for scan in range(40):                                        # one game, 3 spread lines, seen in 40 price checks
        for line in (2.5, 3.5, 4.5):
            r = {f: "" for f in LEDGER_FIELDS}
            r.update({"ts_utc": f"2026-11-10T{8 + scan // 2:02d}:{30 * (scan % 2):02d}:00+00:00", "season": 2026,
                      "week": 10, "game_id": "2026_10_CLE_NO", "market": "spread",
                      "ticker": f"KXNFLSPREAD-26NOV08CLENO-NO{int(line)}", "label": f"NO wins by {line}+", "side": "YES",
                      "price": 0.5, "fee": 0.02, "confidence": "watchlist", "won": True, "pnl": 0.9, "closing": 0.55,
                      "clv": 0.05})
            rows.append(r)
    with open(gp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LEDGER_FIELDS + ["won", "pnl", "closing", "clv"])
        w.writeheader()
        w.writerows(rows)
    t = scanner.trust_table(cfg)["spread"]
    assert t["n"] == 1 and t["contracts"] == 3 and not t["trusted"]          # 120 rows, but ONE game
    assert t["avg_clv"] == pytest.approx(0.05) and t["pnl"] == pytest.approx(0.9)
    assert " 3 " in scanner.signals_report(cfg, 2026).splitlines()[1]        # the record counts each contract once


def test_refreshing_a_data_file_with_nothing_new_keeps_cached_sims(tmp_path):
    import os
    import time

    from gridsim.api import _content_stamp
    f = tmp_path / "injuries.parquet"
    f.write_bytes(b"same content")
    a = _content_stamp(f)
    time.sleep(0.05)
    f.write_bytes(b"same content")                                # re-downloaded, nothing new
    os.utime(f, None)
    assert _content_stamp(f) == a
    f.write_bytes(b"new report!!")
    assert _content_stamp(f) != a
