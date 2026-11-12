"""The per-game Kalshi board (both sides of every contract, rated like the price check), the week board from the
last saved prices, the record by price paid, and parlays across games. No network."""
from __future__ import annotations

import csv
import datetime as dt
import json

import pytest

from gridsim import parlay, scanner
from gridsim.config import load_config
from gridsim.markets import both_sides, edges, sim_probs


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIDSIM_DATA", str(tmp_path / "GridSim Data"))
    monkeypatch.setenv("HOME", str(tmp_path))
    return load_config()


@pytest.fixture()
def raw(cfg):
    """GridSim alone (market_blend.weight 1): the rating rules without the adjustment."""
    cfg.raw["market_blend"]["weight"] = 1.0
    return cfg


def summ(p_home=0.40, home="NYG", away="WAS", n=2000):
    # margins −10 … +9 with the given share of home wins; totals around 44
    wins = round(100 * p_home)
    return {"matchup": {"home": home, "away": away}, "n_sims": n,
            "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "win": {"home": {"p": p_home}, "away": {"p": 1 - p_home}, "tie": {"p": 0.0}},
            "hist": {"margin": {"values": [-7, 3], "counts": [100 - wins, wins]},
                     "total": {"values": [38, 50], "counts": [60, 40]}}}


def q(ticker, yes_ask, vol=5000, size=3000, **kw):
    return {"ticker": ticker, "yes_bid": yes_ask - 0.02, "yes_ask": yes_ask, "mid": yes_ask - 0.01, "no_bid": 1 - yes_ask,
            "no_ask": 1 - yes_ask + 0.02, "spread": 0.02, "volume": vol, "yes_ask_size": size, "yes_bid_size": size,
            "url": f"https://kalshi.com/x/{ticker}", **kw}


def mk(home="NYG", away="WAS", ml_home=0.30):
    return {"moneyline": {home: q(f"G-{home}", ml_home), away: q(f"G-{away}", 1 - ml_home + 0.01)},
            "spread": [{**q(f"S-{away}3", 0.55), "team": away, "line": 3.5}],
            "total": [{**q("T-44", 0.48), "line": 44.5, "strike": 44.5}],
            "fetched_utc": "2026-11-12T15:00:00+00:00"}


def test_both_sides_lists_the_cheap_side_too_and_agrees_with_the_best_side(raw):
    cfg = raw
    rows = sim_probs(summ(), mk())
    sides = both_sides(rows, cfg)
    assert len(sides) == 2 * len(rows)                                   # every contract: YES and NO
    best = {r["ticker"]: r for r in edges(rows, cfg)}
    for t, b in best.items():
        top = max((s for s in sides if s["ticker"] == t), key=lambda s: s["edge"])
        assert top["side"] == b["side"] and top["edge"] == pytest.approx(b["edge"])
    nyg = next(s for s in sides if s["ticker"] == "G-NYG" and s["side"] == "YES")
    assert nyg["what"] == "NYG wins" and nyg["price"] == 0.30
    assert nyg["cost"] == pytest.approx(0.30 + nyg["fee"]) and nyg["pays"] == pytest.approx(1 / nyg["cost"])
    assert nyg["edge"] == pytest.approx(0.40 - nyg["cost"])
    under = next(s for s in sides if s["ticker"] == "T-44" and s["side"] == "NO")
    assert under["what"] == "under 44.5" and under["p_side"] == pytest.approx(0.60)
    assert next(s for s in sides if s["ticker"] == "S-WAS3" and s["side"] == "NO")["what"] == "NYG wins or loses by 3 or fewer"
    assert next(s for s in sides if s["ticker"] == "G-NYG" and s["side"] == "NO")["what"] == "NYG does not win"


def test_the_board_rates_each_side_by_the_price_checks_rules(raw):
    cfg = raw
    b = scanner.game_board(cfg, summ(), mk(), trust={})
    by = {(r["ticker"], r["side"]): r for r in b}
    nyg = by[("G-NYG", "YES")]                       # 40% for about 31.5¢: an edge, but no record yet
    assert nyg["rating"] == "unproven" and nyg["confidence"] == "watchlist"
    assert nyg["catches"] == ["no record yet on who-wins contracts"]
    assert by[("G-NYG", "NO")]["rating"] == "none" and by[("G-NYG", "NO")]["catches"] == []
    assert not any("unusually large" in c for c in nyg["catches"])       # 8.5 points: big, not suspicious
    huge = next(r for r in scanner.game_board(cfg, summ(0.55), mk(), trust={}) if r["ticker"] == "G-NYG" and r["side"] == "YES")
    assert huge["rating"] == "unproven" and "unusually large" in huge["catches"][0]
    assert nyg["p_adj"] == pytest.approx(nyg["p_side"]) and nyg["edge"] == pytest.approx(nyg["lean"])
    order = [(scanner.RATING_RANK[r["rating"]], -round(r["edge"], 6)) for r in b]
    assert order == sorted(order)                                         # cleanest first, then the biggest edge
    trusted = {m: {"n": 60, "avg_clv": 0.01, "trusted": True} for m in ("moneyline", "spread", "total")}
    b = scanner.game_board(cfg, summ(), mk(), trust=trusted)
    assert next(r for r in b if r["ticker"] == "G-NYG" and r["side"] == "YES")["rating"] == "signal"
    thin = mk()
    thin["moneyline"]["NYG"]["volume"] = 10
    r = next(r for r in scanner.game_board(cfg, summ(), thin, trust=trusted) if r["ticker"] == "G-NYG" and r["side"] == "YES")
    assert r["rating"] == "catch" and any("hardly traded" in c for c in r["catches"])
    # the same answer as the price check's own evaluate() for the side it would pick
    sig = {(s["ticker"], s["side"]): s for s in scanner.evaluate(cfg, summ(), edges(sim_probs(summ(), mk()), cfg), {}, 0.0, None)}
    for key, s in sig.items():
        row = next(r for r in scanner.game_board(cfg, summ(), mk(), trust={}) if (r["ticker"], r["side"]) == key)
        assert row["confidence"] == s["confidence"] and row["edge"] == pytest.approx(s["edge"])


def test_the_adjusted_chance_is_the_markets_moved_toward_gridsim_by_the_weight(cfg):
    rows = sim_probs(summ(), mk())                       # NYG: GridSim 40%, Kalshi 28¢ bid / 30¢ ask
    side = lambda w: next(x for x in both_sides(rows, _w(cfg, w)) if x["ticker"] == "G-NYG" and x["side"] == "YES")
    s0, s5, s1 = side(0.0), side(0.5), side(1.0)
    assert s0["p_market"] == pytest.approx(0.29) and s0["p_side"] == pytest.approx(0.40)
    assert s0["p_adj"] == pytest.approx(0.29) and s5["p_adj"] == pytest.approx(0.345) and s1["p_adj"] == pytest.approx(0.40)
    assert s0["edge"] == pytest.approx(0.29 - s0["cost"]) and s0["edge"] < 0          # the market's price: no edge
    assert s0["lean"] == s5["lean"] == pytest.approx(0.40 - s0["cost"])                # GridSim's own view: unchanged
    assert s1["edge"] == pytest.approx(s1["lean"])
    no = next(x for x in both_sides(rows, _w(cfg, 0.0)) if x["ticker"] == "G-NYG" and x["side"] == "NO")
    assert no["p_market"] == pytest.approx(0.71) and no["p_market"] <= no["price"]
    # nobody bidding: the ask stands in for the market's chance (and a last-trade price is not used)
    lone = mk()
    lone["moneyline"]["NYG"].update(yes_bid=None, mid=0.99)
    x = next(x for x in both_sides(sim_probs(summ(), lone), _w(cfg, 0.0)) if x["ticker"] == "G-NYG" and x["side"] == "YES")
    assert x["p_market"] == pytest.approx(0.30)
    assert both_sides(rows, _w(cfg, 7))[0]["p_adj"] == both_sides(rows, _w(cfg, 1.0))[0]["p_adj"]   # kept within 0..1


def _w(cfg, w):
    cfg.raw["market_blend"]["weight"] = w
    return cfg


def test_with_the_measured_weight_gridsims_disagreement_is_a_lean_not_an_edge(cfg):
    assert cfg.raw["market_blend"]["weight"] == 0.0                       # what replayed seasons support
    b = scanner.game_board(cfg, summ(), mk(), trust={})
    nyg = next(r for r in b if r["ticker"] == "G-NYG" and r["side"] == "YES")
    assert nyg["rating"] == "lean" and nyg["confidence"] is None and nyg["edge"] < 0 < nyg["lean"]
    assert nyg["catches"] == [] and nyg["hard"] is False                  # a price you can really get
    assert not any(r["rating"] in ("signal", "unproven", "catch") for r in b)
    assert b[0]["rating"] == "lean" and [r["lean"] for r in b if r["rating"] == "lean"] == sorted(
        (r["lean"] for r in b if r["rating"] == "lean"), reverse=True)
    thin = mk()
    thin["moneyline"]["NYG"]["volume"] = 10
    r = next(r for r in scanner.game_board(cfg, summ(), thin, trust={}) if r["ticker"] == "G-NYG" and r["side"] == "YES")
    assert r["rating"] == "lean" and r["hard"] and any("hardly traded" in c for c in r["catches"])
    far = next(r for r in scanner.game_board(cfg, summ(0.55), mk(), trust={}) if r["ticker"] == "G-NYG" and r["side"] == "YES")
    assert far["catches"][0] == "far from the market — check the news"
    # half weight: 29% + half of 11 points = 34.5% for a cost of about 31.5¢ -> a small adjusted edge, still a lean
    half = next(r for r in scanner.game_board(_w(cfg, 0.5), summ(), mk(), trust={}) if r["ticker"] == "G-NYG" and r["side"] == "YES")
    assert half["rating"] == "lean" and 0 < half["edge"] < 0.04


def _week(monkeypatch, cfg, games):
    monkeypatch.setattr(scanner, "week_slate", lambda cfg_, s, w: (2026, 10, games))
    sims = {g["game_id"]: summ(home=g["home_team"], away=g["away_team"]) for g in games}
    monkeypatch.setattr(scanner, "cached_summary", lambda req, cfg_: next(
        (s for s in sims.values() if s["matchup"]["home"] == req.home), None))
    return sims


def _game(home, away, hours=30.0, final=None):
    from gridsim.live import ET
    ko = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours)).astimezone(ET)
    return {"game_id": f"2026_10_{away}_{home}", "home_team": home, "away_team": away, "gameday": ko.strftime("%Y-%m-%d"),
            "gametime": ko.strftime("%H:%M"), "home_score": final, "away_score": final}


def test_the_week_board_uses_the_last_saved_prices_and_skips_started_games(cfg, monkeypatch):
    games = [_game("NYG", "WAS"), _game("GB", "MIN"), _game("DAL", "SF", hours=-2), _game("CIN", "PIT")]
    sims = _week(monkeypatch, cfg, games)
    sims.pop("2026_10_PIT_CIN")                                           # one game was never simulated
    d = cfg.resolve_path("signals") / "snapshots"
    d.mkdir(parents=True)
    old = {"ts_utc": "2026-11-12T10:00:00+00:00", "games": {}}
    new = {"ts_utc": "2026-11-12T15:00:00+00:00",
           "games": {"2026_10_WAS_NYG": mk(), "2026_10_MIN_GB": {"error": "boom"}, "2026_10_PIT_CIN": mk("CIN", "PIT")}}
    (d / "2026_wk10_20261112T100000Z.json").write_text(json.dumps(old))
    (d / "2026_wk10_20261112T150000Z.json").write_text(json.dumps(new))
    (d / "2026_wk09_20261113T150000Z.json").write_text(json.dumps(old))   # another week: not this one's
    wb = scanner.week_board(cfg, 2026, 10)
    assert wb["ts_utc"] == "2026-11-12T15:00:00+00:00" and set(wb["games"]) == {
        "2026_10_WAS_NYG", "2026_10_MIN_GB", "2026_10_PIT_CIN"}           # the started game is not listed
    rows = wb["games"]["2026_10_WAS_NYG"]["rows"]
    assert rows and rows[0]["matchup"] == "WAS @ NYG" and rows[0]["game_id"] == "2026_10_WAS_NYG"
    assert wb["games"]["2026_10_MIN_GB"]["rows"] is None and "no Kalshi prices" in wb["games"]["2026_10_MIN_GB"]["note"]
    assert "not simulated" in wb["games"]["2026_10_PIT_CIN"]["note"]


def test_no_saved_prices_at_all_is_an_empty_board_not_an_error(cfg, monkeypatch):
    _week(monkeypatch, cfg, [_game("NYG", "WAS")])
    wb = scanner.week_board(cfg, 2026, 10)
    assert wb["ts_utc"] is None and wb["games"]["2026_10_WAS_NYG"]["rows"] is None


def test_the_record_by_price_counts_each_contract_once_and_shows_what_was_needed(cfg):
    assert scanner.record_by_price(cfg) == []
    gp = cfg.resolve_path("signals") / "graded.csv"
    gp.parent.mkdir(parents=True, exist_ok=True)
    fields = scanner.LEDGER_FIELDS + ["won", "pnl", "closing", "clv"]
    base = {f: "" for f in fields}
    rows = [dict(base, ts_utc="2026-11-01T10:00:00+00:00", season=2026, game_id="g1", market="moneyline", ticker="A",
                 side="YES", price=0.30, fee=0.02, won=True, pnl=1 / 0.32 - 1, clv=0.03),
            dict(base, ts_utc="2026-11-01T10:30:00+00:00", season=2026, game_id="g1", market="moneyline", ticker="A",
                 side="YES", price=0.33, fee=0.02, won=True, pnl=1 / 0.35 - 1, clv=0.0),       # same contract again
            dict(base, ts_utc="2026-11-01T10:00:00+00:00", season=2026, game_id="g2", market="total", ticker="B",
                 side="NO", price=0.20, fee=0.01, won=False, pnl=-1.0, clv=-0.02),
            dict(base, ts_utc="2026-11-01T10:00:00+00:00", season=2026, game_id="g2", market="spread", ticker="C",
                 side="YES", price=0.70, fee=0.02, won=True, pnl=1 / 0.72 - 1, clv="")]
    with open(gp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    rec = {r["price paid"]: r for r in scanner.record_by_price(cfg, 2026)}
    low = rec["all under 50¢"]
    assert low["contracts"] == 2 and low["games"] == 2 and low["won %"] == pytest.approx(50.0)
    assert low["needed %"] == pytest.approx(100 * (0.32 + 0.21) / 2) and low["avg CLV"] == pytest.approx(0.005)
    assert low["P&L (units)"] == pytest.approx(1 / 0.32 - 2)
    assert rec["all 50¢ and up"]["contracts"] == 1 and rec["all 50¢ and up"]["avg CLV"] is None
    assert set(rec) == {"under 25¢", "25–49¢", "50–74¢", "all under 50¢", "all 50¢ and up"}


def test_legs_from_different_games_multiply(cfg):
    c = parlay.combine([{"p": 0.5, "p_sim": 0.6, "moe": 0.02, "cost": 0.4}, {"p": 0.4, "p_sim": 0.5, "moe": 0.02, "cost": 0.3}])
    assert c["p"] == pytest.approx(0.2) and c["cost"] == pytest.approx(0.12) and c["pays"] == pytest.approx(1 / 0.12)
    assert c["ev_per_dollar"] == pytest.approx(0.2 / 0.12 - 1) and c["edge"] == pytest.approx(0.08)
    assert c["moe"] == pytest.approx(0.2 * ((0.02 / 0.5) ** 2 + (0.02 / 0.4) ** 2) ** 0.5)
    assert c["p_sim"] == pytest.approx(0.3) and c["ev_sim"] == pytest.approx(0.3 / 0.12 - 1)
    assert "cost" not in parlay.combine([{"p": 0.5}, {"p": 0.5}])          # no prices: just the chance


def _board(n_games=5):
    """Per game: a long shot and a favourite GridSim leans to (the favourite in a tighter market), a side it does
    not rate, and a lean in a thin market."""
    games = {}
    for i in range(n_games):
        rows = []
        for j, (price, fee, mkt, sim, rating, hard) in enumerate([
                (0.30, 0.02, 0.29, 0.40, "lean", False), (0.72, 0.01, 0.715, 0.80, "lean", False),
                (0.20, 0.02, 0.19, 0.21, "none", False), (0.40, 0.02, 0.35, 0.55, "lean", True)]):
            cost = price + fee
            rows.append({"ticker": f"g{i}-{j}", "side": "YES", "market": ("moneyline", "spread", "total", "total")[j],
                         "team": f"T{i}", "other": f"O{i}", "price": price, "cost": cost, "p_side": sim, "p_market": mkt,
                         "p_adj": mkt, "moe": 0.02, "edge": mkt - cost, "lean": sim - cost, "rating": rating,
                         "hard": hard, "what": f"leg {i}-{j}", "matchup": f"A{i} @ H{i}", "game_id": f"g{i}"})
        games[f"g{i}"] = {"matchup": f"A{i} @ H{i}", "rows": rows}
    return {"games": games}


def test_auto_parlays_take_one_leg_per_game_from_what_gridsim_likes_and_sort_by_risk(cfg):
    res = parlay.auto_parlays(_board())
    assert res["games_with_edges"] == 5 and res["legs_considered"] == 10    # the two tradeable leans per game
    seen = 0
    for key, _, lo, hi in parlay.TIERS:
        used = {}
        for par in res["tiers"][key]:
            seen += 1
            assert lo <= par["p"] < hi                                       # grouped by the ADJUSTED chance
            assert par["p_sim"] > par["p"] and par["ev_sim"] > par["ev_per_dollar"]
            assert len({x["game_id"] for x in par["legs"]}) == len(par["legs"]) >= 2      # never two legs of one game
            assert all(x["rating"] == "lean" and not x["hard"] for x in par["legs"])
            for x in par["legs"]:
                used[x["ticker"]] = used.get(x["ticker"], 0) + 1
        assert len(res["tiers"][key]) <= 5 and all(v <= 2 for v in used.values())
        assert all(par["can_roll"] is False for par in res["tiers"][key])      # (no kickoff times in this board)
        evs = [round(par["ev_per_dollar"], 4) for par in res["tiers"][key]]
        assert evs == sorted(evs, reverse=True)                              # least costly to play first
    assert seen and res["tiers"]["safer"] and res["tiers"]["long"]
    safer = res["tiers"]["safer"][0]
    assert all(x["price"] >= 0.5 for x in safer["legs"]) and safer["ev_per_dollar"] < 0   # fees: a little below zero
    # fewer than two games with something to combine: nothing
    assert not any(parlay.auto_parlays(_board(1))["tiers"].values())
    assert not any(parlay.auto_parlays({"games": {"g": {"rows": None}}})["tiers"].values())


def test_x_wins_and_y_does_not_win_are_one_leg_not_two(cfg):
    base = {"market": "moneyline", "price": 0.30, "cost": 0.32, "p_side": 0.40, "moe": 0.02, "edge": 0.08,
            "lean": 0.08, "rating": "unproven", "matchup": "SEA @ LV", "game_id": "g"}
    rows = [{**base, "ticker": "LV", "side": "YES", "team": "LV", "other": "SEA", "what": "LV wins"},
            {**base, "ticker": "SEA", "side": "NO", "team": "SEA", "other": "LV", "what": "SEA does not win",
             "cost": 0.33, "edge": 0.07, "lean": 0.07}]
    got = parlay.candidates({"games": {"g": {"rows": rows}}})["g"]
    assert [r["what"] for r in got] == ["LV wins"]
    # ... and so is the small spread line on the same team: "LV wins or loses by 2 or fewer" is the same idea
    rows.append({**base, "market": "spread", "ticker": "S", "side": "NO", "team": "SEA", "other": "LV", "line": 2.5,
                 "what": "LV wins or loses by 2 or fewer", "cost": 0.34, "edge": 0.05, "lean": 0.05})
    assert [r["what"] for r in parlay.candidates({"games": {"g": {"rows": rows}}})["g"]] == ["LV wins"]


def test_legs_can_only_be_rolled_when_the_games_are_hours_apart():
    a = {"kickoff_utc": "2026-11-15T18:00:00+00:00"}
    assert parlay.can_roll([a, {"kickoff_utc": "2026-11-15T22:05:00+00:00"}]) is True
    assert parlay.can_roll([a, {"kickoff_utc": "2026-11-15T21:25:00+00:00"}]) is False     # the late window
    assert parlay.can_roll([a, dict(a)]) is False and parlay.can_roll([a, {}]) is False


def test_the_search_says_when_it_was_cut_short(cfg):
    res = parlay.auto_parlays(_board(), max_combos=7)
    assert res["partial"] is True and res["checked"] == 7
    assert parlay.auto_parlays(_board())["partial"] is False


def test_a_four_cent_gap_passes_the_four_cent_rule_whatever_the_prices(cfg):
    for bid in (0.52, 0.31, 0.07, 0.88):
        r = {"edge": 0.2, "moe": 0.01, "spread": (bid + 0.04) - bid, "volume": 5000, "market": "total", "price": 0.5,
             "side": "YES", "yes_ask_size": 5000, "ticker": "T"}
        codes = [c[0] for c in scanner._checks(cfg, r, {}, 0.0, None)[0]]
        assert "spread" not in codes, bid
        assert "spread" in [c[0] for c in scanner._checks(cfg, {**r, "spread": (bid + 0.05) - bid}, {}, 0.0, None)[0]]


def test_a_half_written_newest_snapshot_falls_back_to_the_one_before(cfg, monkeypatch):
    games = [_game("NYG", "WAS")]
    _week(monkeypatch, cfg, games)
    d = cfg.resolve_path("signals") / "snapshots"
    d.mkdir(parents=True)
    (d / "2026_wk10_20261112T100000Z.json").write_text(json.dumps(
        {"ts_utc": "2026-11-12T10:00:00+00:00", "games": {"2026_10_WAS_NYG": mk()}}))
    (d / "2026_wk10_20261112T103000Z.json").write_text('{"ts_utc": "2026-11-12T10:30')       # cut off mid-write
    (d / "2026_wk10_backfilled_x_20261112T110000Z.json").write_text(json.dumps({"ts_utc": "x", "games": {}}))
    wb = scanner.week_board(cfg, 2026, 10)
    assert wb["ts_utc"] == "2026-11-12T10:00:00+00:00" and wb["games"]["2026_10_WAS_NYG"]["rows"]
    assert scanner.latest_snapshot(cfg, 2026, 10).name == "2026_wk10_20261112T103000Z.json"   # (not the backfill file)
