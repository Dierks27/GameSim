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
    from gridsim.live import ET
    et = ko.astimezone(ET)
    g = {"game_id": "2026_11_GB_MIN", "season": 2026, "week": 11, "home_team": "MIN", "away_team": "GB",
         "gameday": et.strftime("%Y-%m-%d"), "gametime": et.strftime("%H:%M"), "home_score": None, "away_score": None}
    if final:
        g["home_score"], g["away_score"] = final
    return g


def summ(p_home=0.62, listed=(), qb="J.McCarthy", roles=()):
    return {"matchup": {"home": "MIN", "away": "GB"}, "n_sims": 2000,
            "players": {"MIN": [{"name": n, "pos": "WR", "volume": 8.0} for n in roles], "GB": []},
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
    assert check(cfg, summ(0.56), mk()) == []                     # 6 points: two 2000-sim runs can differ by 3 on luck
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
    # the game is moved (flexed): the watch follows the schedule as it is now, not the time stored with the position
    monkeypatch.setattr(pos, "_game", lambda cfg_, q: game(hours_to_kickoff=2))
    assert [x["kind"] for x in check(cfg, summ(), mk(0.70, 0.72), near_only=True)] == ["price"]
    stored = dt.datetime.fromisoformat(pos.load_positions(cfg)[0]["kickoff_utc"])
    assert abs((stored - dt.datetime.now(dt.timezone.utc)).total_seconds() - 2 * 3600) < 120
    monkeypatch.setattr(pos, "_game", lambda cfg_, q: game(hours_to_kickoff=-0.1))
    assert check(cfg, summ(), mk(0.20, 0.22)) == []                         # in play: left alone


def test_a_tie_pays_half_on_who_wins(env, monkeypatch):
    cfg, g, p = setup(env, monkeypatch)
    from gridsim.markets import fee
    p2 = pos.add_position(cfg, game=g, market="moneyline", ticker=TICK, label="MIN wins", side="NO", price=0.57, contracts=100)
    monkeypatch.setattr(pos, "_game", lambda cfg_, q: game(final=(40, 40)))
    a = pos.watch(cfg, notify_on=True, verbose=False)
    assert a[0]["kind"] == "settled" and a[0]["message"].count("TIE") == 2 and "LOST" not in a[0]["message"]
    done = {q["id"]: q for q in pos.load_positions(cfg)}
    assert done[p["id"]]["result"] == "tie" and done[p["id"]]["pnl"] == pytest.approx(50 - 55 - fee(0.55, cfg, 100))
    assert done[p2["id"]]["pnl"] == pytest.approx(50 - 57 - fee(0.57, cfg, 100))


def test_roster_bookkeeping_is_not_lineup_news(env, monkeypatch):
    cfg, g, p = setup(env, monkeypatch)
    def r(name, status, pos_="WR"):
        return {"name": name, "team": "MIN", "pos": pos_, "p_play": 0.0, "status": status, "reason": f"roster status {status}"}
    check(cfg, summ(listed=[r("P.Squad", "DEV")], roles=["J.Jefferson", "J.Addison"]), mk())
    # practice-squad signing and release, a cut: players the sim never had in its plan -> no alert
    assert check(cfg, summ(listed=[r("N.Signing", "DEV"), r("O.Cut", "CUT", "TE")], roles=["J.Jefferson", "J.Addison"]), mk()) == []
    # a player the sim expected to play goes on a reserve list: that is news, in words
    a = check(cfg, summ(p_home=0.57, listed=[r("N.Signing", "DEV"), r("J.Addison", "RES")], roles=["J.Jefferson"]), mk())
    assert [x["kind"] for x in a] == ["injury"] and a[0]["priority"] == "high"
    assert "J.Addison (WR) is on a reserve list" in a[0]["message"] and "DEV" not in a[0]["message"]
    assert "O.Cut" not in a[0]["message"] and "N.Signing" not in a[0]["message"]
    # ... and comes back into the plan
    a = check(cfg, summ(listed=[r("N.Signing", "DEV")], roles=["J.Jefferson", "J.Addison"]), mk())
    assert "J.Addison (WR) is back on the active roster" in a[0]["message"]
    # coming off the injury report reads as good news, not as "not listed"
    q = {"name": "J.Jefferson", "team": "MIN", "pos": "WR", "p_play": 0.75, "status": "Questionable", "reason": "Questionable"}
    check(cfg, summ(listed=[q], roles=["J.Jefferson", "J.Addison"]), mk())
    a = check(cfg, summ(listed=[], roles=["J.Jefferson", "J.Addison"]), mk())
    assert "J.Jefferson (WR) is off the injury report (expected to play; was Questionable (75% to play))" in a[0]["message"]


def test_an_alert_that_could_not_be_saved_or_sent_is_raised_again(env, monkeypatch):
    cfg, g, p = setup(env, monkeypatch)
    check(cfg, summ(), mk())
    real = pos._log_alerts
    def locked(*a, **k):
        raise PermissionError("alerts.csv is open in Excel")
    monkeypatch.setattr(pos, "_log_alerts", locked)
    # logged nowhere and (no notify) pushed nowhere: not marked as reported
    assert pos.watch(cfg, notify_on=False, game_data={"2026_11_GB_MIN": (summ(), mk(0.40, 0.42))}, verbose=False) == []
    monkeypatch.setattr(pos, "_log_alerts", real)
    a = check(cfg, summ(), mk(0.40, 0.42))
    assert [x["kind"] for x in a] == ["price"] and len(pos.recent_alerts(cfg)) == 1
    # the log fails but the push goes out: delivered, so not repeated
    monkeypatch.setattr(pos, "_log_alerts", locked)
    assert len(check(cfg, summ(), mk(0.60, 0.62))) == 1
    monkeypatch.setattr(pos, "_log_alerts", real)
    assert check(cfg, summ(), mk(0.60, 0.62)) == []


def test_kalshi_not_answering_is_reported_and_keeps_the_last_numbers(env, monkeypatch):
    cfg, g, p = setup(env, monkeypatch)
    check(cfg, summ(), mk())
    import gridsim.api as api
    import gridsim.markets as markets
    monkeypatch.setattr(api, "cached_summary", lambda req, cfg_: summ())
    def down(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(markets, "game_markets", down)
    monkeypatch.setattr(markets, "Kalshi", lambda cfg_: object())
    assert pos.watch(cfg, verbose=False, refresh_injuries=False) == []
    lv = pos.last_view(cfg)
    assert "Kalshi's prices could not be read" in lv["problems"]["2026_11_GB_MIN"]["what"] and pos.LAST_PROBLEMS
    assert lv["positions"][p["id"]]["mid"] == pytest.approx(0.55)            # still the last price that was read


def test_injury_refresh_is_throttled_across_watch_runs(env, monkeypatch):
    cfg, g, p = setup(env, monkeypatch)
    import gridsim.availability as av
    calls = []
    monkeypatch.setattr(av, "_load_source", lambda name, season, cfg_, **k: calls.append(name))
    import gridsim.api as api
    import gridsim.markets as markets
    monkeypatch.setattr(api, "cached_summary", lambda req, cfg_: summ())
    monkeypatch.setattr(markets, "game_markets", lambda k, g_: mk())
    monkeypatch.setattr(markets, "Kalshi", lambda cfg_: object())
    for _ in range(4):
        pos.watch(cfg, verbose=False)
    assert len(calls) == 3                                        # one refresh (three files), not one per run


def test_bad_entries_are_refused_and_delete_removes(env, monkeypatch):
    cfg, g, p = setup(env, monkeypatch)
    for bad in ({"price": 55}, {"contracts": 0}, {"side": "MAYBE"}):
        with pytest.raises(ValueError):
            pos.add_position(cfg, **{**dict(game=g, market="moneyline", ticker=TICK, label="MIN wins", side="YES",
                                            price=0.55, contracts=1), **bad})
    from gridsim import userdata
    z = userdata.backup(cfg, "x")                                 # a backup that still holds the mistaken entry
    pos.delete_position(cfg, p["id"])
    assert pos.load_positions(cfg) == [] and pos.watch(cfg, verbose=False) == []
    userdata.restore(z)
    assert pos.load_positions(cfg) == []                          # removed stays removed


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


def test_phone_and_toast_text_keep_cents_arrows_and_dollars(monkeypatch):
    import gridsim.notify as notify
    sent = {}
    class R:
        def read(self):
            return b""
    def fake(req, timeout=0):
        sent["url"], sent["body"], sent["headers"] = req.full_url, json.loads(req.data.decode("utf-8")), dict(req.header_items())
        return R()
    monkeypatch.setattr(notify.urllib.request, "urlopen", fake)
    monkeypatch.setattr(notify, "load_env", lambda: {"NTFY_TOPIC": "my-topic"})
    cfg = load_config()
    monkeypatch.setitem(cfg.notify, "ntfy_topic_env", "NTFY_TOPIC")
    monkeypatch.setitem(cfg.notify, "discord_webhook_env", "NOPE")
    notify.send(cfg, "GridSim watch: KC @ ATL — ATL wins (Yes) down 7¢", "moved 47¢ → 40¢ (−$2.25 in total)", url="https://kalshi.com/x", priority="high")
    assert sent["url"] == "https://ntfy.sh/" and sent["body"]["topic"] == "my-topic"
    assert sent["body"]["title"].endswith("down 7¢") and "−$2.25" in sent["body"]["message"] and sent["body"]["priority"] == 4
    assert sent["body"]["click"] == "https://kalshi.com/x"
    # the toast script never contains the text (PowerShell would expand $2 and not decode \\u escapes)
    assert "$env:GRIDSIM_TOAST_TITLE" in notify.TOAST_PS and "$env:GRIDSIM_TOAST_TEXT" in notify.TOAST_PS
    long = "word " * 100
    assert len(notify.toast_text(long)) < 290 and notify.toast_text(long).endswith("(open GridSim for the rest)")
    assert notify.toast_text("short") == "short"


def test_data_files_are_swapped_in_whole_and_the_library_cache_is_off(tmp_path):
    import polars as pl

    from gridsim import data as D
    f = tmp_path / "injuries_2026.parquet"
    assert D.write_parquet_atomic(pl.DataFrame({"a": [1, 2]}), f) is True
    before = f.read_bytes()
    assert D.write_parquet_atomic(pl.DataFrame({"a": [1, 2]}), f) is False and f.read_bytes() == before
    assert D.write_parquet_atomic(pl.DataFrame({"a": [1, 3]}), f) is True and not list(tmp_path.glob("*.partial"))
    D.nfl_client()
    from nflreadpy.config import get_config
    assert str(getattr(get_config().cache_mode, "value", get_config().cache_mode)).lower() == "off"


def test_the_same_seed_gives_the_same_player_order():
    from gridsim.players import TeamPlayers
    a = TeamPlayers("X", {"p2": 0.3, "p1": 0.5, "p3": 0.2}, {"r2": 0.4, "r1": 0.6}, {}, {}, {}, {})
    b = TeamPlayers("X", {"p3": 0.2, "p1": 0.5, "p2": 0.3}, {"r1": 0.6, "r2": 0.4}, {}, {}, {}, {})
    assert a.dists(frozenset())[0][0] == b.dists(frozenset())[0][0] == ["p1", "p2", "p3"]
    assert a.dists(frozenset())[1][0] == b.dists(frozenset())[1][0] == ["r1", "r2"]


def test_a_tied_moneyline_signal_is_graded_at_half(env, monkeypatch):
    import polars as pl
    cfg = load_config()
    led = cfg.resolve_path("signals") / "ledger.csv"
    led.parent.mkdir(parents=True, exist_ok=True)
    r = {f: "" for f in LEDGER_FIELDS}
    r.update({"ts_utc": "2026-11-10T08:00:00+00:00", "season": 2026, "week": 11, "game_id": "2026_11_GB_MIN",
              "market": "moneyline", "ticker": TICK, "label": "MIN wins", "side": "NO", "price": 0.40, "fee": 0.02,
              "confidence": "watchlist"})
    with open(led, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LEDGER_FIELDS)
        w.writeheader()
        w.writerow(r)
    import gridsim.data as D
    monkeypatch.setattr(D, "load_schedules", lambda cfg_: pl.DataFrame([game(final=(40, 40))]))
    monkeypatch.setattr(scanner, "_closing_price", lambda *a, **k: None)
    assert scanner.grade_signals(cfg) == 1
    g = pl.read_csv(cfg.resolve_path("signals") / "graded.csv")
    assert g["pnl"][0] == pytest.approx(0.5 / 0.42 - 1)           # paid 42¢ all-in, got 50¢: not a full win


# ---- second review round ---------------------------------------------------------------------------------------------

def _made(s, h):
    return {**s, "meta": {"config_hash": h}}


def test_a_number_made_with_another_model_or_settings_is_not_called_new_data(env, monkeypatch):
    cfg, g, p = setup(env, monkeypatch)
    check(cfg, _made(summ(0.52), "old"), mk())
    a = check(cfg, _made(summ(0.36), "new"), mk())                # e.g. the first check after an update
    assert [x["kind"] for x in a] == ["sim"] and "now 36% (was 52%)" in a[0]["title"]
    assert "settings or model changed" in a[0]["message"] and "with the latest data" not in a[0]["message"]
    st = pos.last_view(cfg)["positions"][p["id"]]
    assert st["ref_hash"] == "new" and st["ref_p"] == pytest.approx(0.36)
    # a small difference between two versions is no alert, but the new number is the reference from then on
    assert check(cfg, _made(summ(0.39), "newer"), mk()) == []
    st = pos.last_view(cfg)["positions"][p["id"]]
    assert st["ref_hash"] == "newer" and st["ref_p"] == pytest.approx(0.39)
    a = check(cfg, _made(summ(0.29), "newer"), mk())              # same version, the data moved it
    assert "with the latest data" in a[0]["message"]


def test_a_reserve_list_player_on_the_injury_report_is_not_called_activated(env, monkeypatch):
    cfg, g, p = setup(env, monkeypatch)
    res = {"name": "C.Kirk", "team": "MIN", "pos": "WR", "p_play": 0.0, "status": "RES", "reason": "roster status RES"}
    rep = {"name": "C.Kirk", "team": "MIN", "pos": "WR", "p_play": 0.75, "status": "Questionable", "reason": "Questionable"}
    check(cfg, summ(listed=[res]), mk())
    a = check(cfg, summ(listed=[rep]), mk())                      # practising his way back: still on the reserve list
    assert "C.Kirk (WR) is now on the injury report: Questionable (75% to play)" in a[0]["message"]
    assert "back on the active roster" not in a[0]["message"]
    a = check(cfg, summ(listed=[res]), mk())                      # off the report again: he never left the list
    assert "C.Kirk (WR) is on a reserve list" in a[0]["message"] and "moved" not in a[0]["message"]


def test_a_lineup_snapshot_from_the_first_watch_version_compares_the_qb_only(env, monkeypatch):
    old = {"MIN": {"qb": "J.McCarthy", "players": {"L.Bond": {"pos": "WR", "p_play": 0.0, "status": "DEV"},
                                                   "J.Jefferson": {"pos": "WR", "p_play": 0.75, "status": "Questionable"}}}}
    new = {"MIN": {"qb": "S.Howell", "players": {}, "roles": {"J.Jefferson": 9.0}}}
    lines, big = pos.avail_changes(old, new)
    assert lines == ["MIN: expected starting QB is now S.Howell (was J.McCarthy)"] and big


def test_a_read_problem_survives_the_ten_minute_task_and_goes_with_the_position(env, monkeypatch):
    cfg, g, p = setup(env, monkeypatch)                           # kickoff in 48 h
    check(cfg, summ(), mk())
    import gridsim.api as api
    import gridsim.markets as markets
    monkeypatch.setattr(api, "cached_summary", lambda req, cfg_: summ())
    def down(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(markets, "game_markets", down)
    monkeypatch.setattr(markets, "Kalshi", lambda cfg_: object())
    pos.watch(cfg, verbose=False, refresh_injuries=False)
    seen = pos.last_view(cfg)["checked_utc"]
    assert "2026_11_GB_MIN" in pos.last_view(cfg)["problems"]
    # the 10-minute task does not look at a game this far from kickoff: the finding of the full check stands
    assert pos.watch(cfg, near_only=True, verbose=False, refresh_injuries=False) == [] and not pos.LAST_PROBLEMS
    lv = pos.last_view(cfg)
    assert "2026_11_GB_MIN" in lv["problems"] and lv["checked_utc"] == seen
    pos.close_position(cfg, p["id"], 0.50)                        # nothing held any more: nothing to warn about
    assert pos.last_view(cfg)["problems"] == {}


def test_check_now_says_when_it_did_not_check_or_could_not_save(env, monkeypatch):
    cfg, g, p = setup(env, monkeypatch)
    check(cfg, summ(), mk())
    def locked(*a, **k):
        raise PermissionError("alerts.csv is open in Excel")
    monkeypatch.setattr(pos, "_log_alerts", locked)
    assert pos.watch(cfg, notify_on=False, game_data={"2026_11_GB_MIN": (summ(), mk(0.40, 0.42))}, verbose=False) == []
    assert "could not be saved" in pos.LAST_PROBLEMS["_alerts"]["what"] and "down 14¢" in pos.LAST_PROBLEMS["_alerts"]["what"]
    from gridsim.userdata import file_lock
    with file_lock(cfg.resolve_path("positions") / "watch_state.json"):        # a scheduled check is running
        assert pos.watch(cfg, verbose=False) == [] and "_busy" in pos.LAST_PROBLEMS
    assert pos.last_view(cfg)["problems"] == {}                   # neither is a stored "could not be read" report


def test_no_bid_on_your_side_blanks_the_selling_price_and_half_cents_show(env, monkeypatch):
    cfg, g, p = setup(env, monkeypatch)
    check(cfg, summ(), mk())
    a = check(cfg, summ(), mk(0.61, 0.62))                        # mid 55¢ -> 61.5¢
    assert "up 6.5¢" in a[0]["title"] and "55¢ → 61.5¢" in a[0]["message"]
    check(cfg, summ(), mk(0.0, 0.03))                             # still a price, but nobody bids for Yes
    v = pos.last_view(cfg)["positions"][p["id"]]
    assert v["mid"] == pytest.approx(0.015) and v["sell"] is None and v["if_sold_now"] is None


def test_grading_reads_each_price_snapshot_once(env, monkeypatch):
    import polars as pl

    import gridsim.data as data
    cfg = load_config()
    sig = cfg.resolve_path("signals")
    (sig / "snapshots").mkdir(parents=True)
    ko = "2026-11-08T18:00:00+00:00"
    games = [f"2026_10_A{i}_B{i}" for i in range(4)]
    rows = []
    for n in range(30):                                           # 30 price checks; the last 6 are after kickoff
        ts = f"2026-11-0{7 + n // 20}T{(8 + n) % 24:02d}:00:00+00:00" if n < 24 else f"2026-11-08T{n - 5:02d}:30:00+00:00"
        snap = {"ts_utc": ts, "season": 2026, "week": 10, "games": {
            gid: {"moneyline": {f"B{gi}": {"ticker": f"T-{gi}", "yes_bid": 0.40 + n / 100, "yes_ask": 0.42 + n / 100}},
                  "spread": [], "total": []} for gi, gid in enumerate(games)}}
        (sig / "snapshots" / f"2026_wk10_{n:02d}.json").write_text(json.dumps(snap))
        for gi, gid in enumerate(games):
            if ts < ko:
                r = {f: "" for f in LEDGER_FIELDS}
                r.update({"ts_utc": ts, "season": 2026, "week": 10, "game_id": gid, "kickoff_utc": ko, "market": "moneyline",
                          "ticker": f"T-{gi}", "label": f"B{gi} wins", "side": "YES" if n % 2 else "NO", "price": 0.5,
                          "fee": 0.02, "confidence": "watchlist"})
                rows.append(r)
    (sig / "snapshots" / "2026_wk09_x.json").write_text("{ not json")          # another week, and damaged: never opened
    with open(sig / "ledger.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LEDGER_FIELDS)
        w.writeheader()
        w.writerows(rows)
    sched = pl.DataFrame({"game_id": games, "home_team": [f"B{i}" for i in range(4)], "away_team": [f"A{i}" for i in range(4)],
                          "home_score": [24, 10, 17, 30], "away_score": [20, 13, 17, 3]})
    monkeypatch.setattr(data, "load_schedules", lambda cfg_, **k: sched)
    opened = []
    real = scanner.json.loads
    monkeypatch.setattr(scanner.json, "loads", lambda s, *a, **k: (opened.append(1), real(s, *a, **k))[1])
    assert scanner.grade_signals(cfg) == len(rows) == 96
    assert len(opened) == 30                                      # one read per snapshot of that week, not 96 x 31
    monkeypatch.setattr(scanner.json, "loads", real)
    graded = list(csv.DictReader(open(sig / "graded.csv", newline="")))
    last_before = 0.41 + 23 / 100                                 # mid of the last snapshot before kickoff
    for r in graded:
        want = last_before if r["side"] == "YES" else 1 - last_before
        assert float(r["closing"]) == pytest.approx(want) and float(r["clv"]) == pytest.approx(want - 0.5)
    assert scanner.grade_signals(cfg) == 0                        # nothing new: nothing graded twice


def test_the_default_seed_spelled_out_is_the_same_cached_sim(env):
    from gridsim.api import RunRequest, cache_path
    cfg = load_config()
    a = cache_path(RunRequest(home="MIN", away="GB", season=2026, week=11, market_lines=True), cfg)
    b = cache_path(RunRequest(home="MIN", away="GB", season=2026, week=11, market_lines=True,
                              sims=int(cfg.sim["n_sims"]), seed=int(cfg.sim["seed"])), cfg)
    c = cache_path(RunRequest(home="MIN", away="GB", season=2026, week=11, market_lines=True, seed=int(cfg.sim["seed"]) + 1), cfg)
    assert a == b and a != c


# ---- third review round ----------------------------------------------------------------------------------------------

def test_the_users_own_lineup_change_is_not_called_injury_news(env, monkeypatch):
    cfg, g, p = setup(env, monkeypatch)
    out = {"name": "J.Addison", "team": "MIN", "pos": "WR", "p_play": 0.0, "status": "override",
           "reason": "OUT (override file 2026_wk11.yaml)"}
    forced = {**out, "p_play": 1.0, "reason": "IN (override file 2026_wk11.yaml)"}
    q = {"name": "J.Addison", "team": "MIN", "pos": "WR", "p_play": 0.75, "status": "Questionable", "reason": "Questionable"}
    check(cfg, summ(roles=["J.Addison"]), mk())
    a = check(cfg, summ(listed=[out], roles=["J.Addison"]), mk())  # marked out on the Game page
    assert a[0]["kind"] == "injury" and a[0]["priority"] == "default"
    assert "J.Addison (WR) is out in GridSim's sims because of your own lineup change" in a[0]["message"]
    assert "newly on the injury report" not in a[0]["message"] and "override" not in a[0]["message"]
    a = check(cfg, summ(listed=[forced], roles=["J.Addison"]), mk())
    assert "is playing in GridSim's sims because of your own lineup change" in a[0]["message"]
    a = check(cfg, summ(listed=[q], roles=["J.Addison"]), mk())    # toggle removed; he is Questionable for real
    assert ("your own lineup change for him is no longer in place; the injury report has him Questionable "
            "(75% to play)") in a[0]["message"] and a[0]["priority"] == "default"
    check(cfg, summ(listed=[out], roles=["J.Addison"]), mk())
    a = check(cfg, summ(roles=["J.Addison"]), mk())                # toggle removed; not on the report
    assert a[0]["message"].startswith("MIN: J.Addison (WR): your own lineup change for him is no longer in place. ")


def test_lineup_news_in_the_same_check_as_a_model_change_says_both(env, monkeypatch):
    cfg, g, p = setup(env, monkeypatch)
    check(cfg, _made(summ(0.52), "old"), mk())
    a = check(cfg, _made(summ(0.36, qb="S.Howell"), "new"), mk())
    assert [x["kind"] for x in a] == ["injury"] and "expected starting QB is now S.Howell" in a[0]["message"]
    assert "settings or model also changed" in a[0]["message"] and "not only this news" in a[0]["message"]
    a = check(cfg, _made(summ(0.30, qb="J.McCarthy"), "new"), mk())            # same version: the news alone
    assert "also changed" not in a[0]["message"]


def test_the_users_own_starting_qb_choice_is_not_called_lineup_news(env, monkeypatch):
    cfg, g, p = setup(env, monkeypatch)
    pick = {"name": "S.Howell", "team": "MIN", "pos": "QB", "p_play": 1.0, "status": "override",
            "reason": "override: starting QB"}
    sat = {"name": "J.McCarthy", "team": "MIN", "pos": "QB", "p_play": 0.0, "status": "override",
           "reason": "OUT (override file 2026_wk11.yaml)"}
    hurt = {"name": "J.McCarthy", "team": "MIN", "pos": "QB", "p_play": 0.0, "status": "Out", "reason": "Out"}
    check(cfg, summ(), mk())
    a = check(cfg, summ(qb="S.Howell", listed=[pick]), mk())      # picked on the Game page
    assert a[0]["priority"] == "default" and "expected starting QB" not in a[0]["message"]
    assert ("the starting QB in GridSim's sims is now S.Howell (was J.McCarthy) because of your own lineup change"
            in a[0]["message"])
    a = check(cfg, summ(), mk())                                  # pick removed
    assert a[0]["priority"] == "default" and "is no longer in place" in a[0]["message"]
    a = check(cfg, summ(qb="S.Howell", listed=[sat]), mk())       # starter marked out on the Game page
    assert a[0]["priority"] == "default" and "expected starting QB" not in a[0]["message"]
    check(cfg, summ(), mk())
    a = check(cfg, summ(qb="S.Howell", listed=[hurt]), mk())      # the real thing
    assert a[0]["priority"] == "high" and "expected starting QB is now S.Howell (was J.McCarthy)" in a[0]["message"]


def test_a_removed_force_in_for_a_reserve_list_player_says_where_he_is():
    old = {"MIN": {"qb": "J.McCarthy", "roles": {}, "players": {
        "J.Nailor": {"pos": "WR", "p_play": 1.0, "status": "override", "roster": False}}}}
    new = {"MIN": {"qb": "J.McCarthy", "roles": {}, "players": {
        "J.Nailor": {"pos": "WR", "p_play": 0.0, "status": "RES", "roster": True}}}}
    lines, big = pos.avail_changes(old, new)
    assert lines == ["MIN: J.Nailor (WR): your own lineup change for him is no longer in place; he is on a reserve "
                     "list (injured reserve or similar)"] and not big
