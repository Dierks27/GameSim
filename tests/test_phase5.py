import numpy as np
import pytest

from gridsim.combos import evaluate
from gridsim.engine import NS, S, SimResults
from gridsim.markets import fee, parse_event_teams, quote
from gridsim.season import Standings
from gridsim.state import StateError, parse_state
from gridsim.travel import edge


def test_parse_state_basic():
    s = parse_state("Q3 8:42 MIN 17 GB 13 MIN ball 2nd&6 at GB 34 timeouts 2/3", "MIN", "GB")
    assert s["qtr"] == 3 and s["clock"] == 522 and s["score"] == [17, 13]
    assert s["pos"] == 0 and s["yl"] == 34 and s["down"] == 2 and s["togo"] == 6 and s["timeouts"] == [2, 3]
    s2 = parse_state("Q4 1:55 MIN 20 BUF 24 BUF ball 1st&10 at BUF 25", "MIN", "BUF")
    assert s2["pos"] == 1 and s2["yl"] == 75
    s3 = parse_state("OT 6:10 DET 20 GB 20 GB ball 1st&goal at DET 4 timeouts 1/2", "DET", "GB")
    assert s3["qtr"] == 5 and s3["yl"] == 4 and s3["togo"] == 4 and s3["timeouts"] == [1, 2]
    with pytest.raises(StateError):
        parse_state("8:42 MIN 17 GB 13", "MIN", "GB")


def _res(h, a):
    st = np.zeros((len(h), 2, NS))
    st[:, 0, S["pts"]] = h
    st[:, 1, S["pts"]] = a
    return SimResults("HOM", "AWY", st, {})


def test_combo_joint_vs_naive():
    r = _res(np.array([30, 30, 10, 10]), np.array([20, 20, 20, 20]))
    c = evaluate("HOM:win; total>45", r, None)
    assert c["legs"][0]["p"] == 0.5 and c["legs"][1]["p"] == 0.5
    assert c["joint"]["p"] == 0.5 and abs(c["naive"] - 0.25) < 1e-12 and c["correlation_lift"] == 2.0


def test_travel_edges(cfg):
    g = {"home_team": "SEA", "away_team": "MIA", "home_rest": 14, "away_rest": 6, "location": "Home"}
    e = edge(g, cfg)
    assert e["home_pts"] > 0 and e["epa"][0] == -e["epa"][1]
    assert edge({"home_team": "SEA", "away_team": "SF", "home_rest": 7, "away_rest": 7, "location": "Home"}, cfg)["home_pts"] == 0


def test_kalshi_parsing_and_fees(cfg):
    assert parse_event_teams({"title": "Los Angeles R vs Washington: Spread"}) == ("LA", "WAS")
    assert parse_event_teams({"title": "New York J vs Kansas City"}) == ("NYJ", "KC")
    q = quote({"ticker": "X", "yes_bid_dollars": "0.40", "yes_ask_dollars": "0.42", "volume_fp": "10"})
    assert abs(q["mid"] - 0.41) < 1e-12 and abs(q["spread"] - 0.02) < 1e-12
    assert fee(0.5, cfg) == 0.02      # 0.07 * .25 = 1.75c -> rounds up to 2c
    assert fee(0.1, cfg) == 0.01


def test_standings_tiebreak_h2h():
    # two division rivals 1-1 overall, A beat B -> A ranks first
    games = [("BUF", "MIA", 7), ("NYJ", "BUF", 3), ("MIA", "NYJ", 10)]
    st = Standings(games)
    rng = np.random.default_rng(0)
    order = st.rank(["BUF", "MIA", "NYJ"], rng, division=True)
    assert set(order) == {"BUF", "MIA", "NYJ"}
    st2 = Standings([("BUF", "MIA", 7), ("MIA", "NE", 3), ("NE", "BUF", 3)])
    assert st2.rank(["BUF", "MIA"], rng, True)[0] == "BUF"


def test_scanner_rules_watchlist_until_trusted(cfg):
    from gridsim.scanner import evaluate as ev
    row = {"market": "moneyline", "label": "MIN wins", "ticker": "T", "side": "YES", "price": 0.40, "fee": 0.02,
           "p_side": 0.55, "edge": 0.13, "moe": 0.01, "yes_ask_size": 10000, "spread": 0.01, "volume": 50000}
    out = ev(cfg, {}, [row], trust={}, sim_age_h=1.0, q_both=None)
    assert out[0]["confidence"] == "watchlist"
    trusted = {"moneyline": {"n": 80, "avg_clv": 0.02, "pnl": 3.0, "trusted": True}}
    assert ev(cfg, {}, [row], trusted, 1.0, None)[0]["confidence"] == "strong"
    thin = {**row, "volume": 10}
    assert ev(cfg, {}, [thin], trusted, 1.0, None)[0]["confidence"] == "watchlist"


def test_devig():
    from gridsim.markets import american_to_prob, devig_two_way
    a, b = devig_two_way(american_to_prob(-110), american_to_prob(-110))
    assert abs(a - 0.5) < 1e-12 and abs(b - 0.5) < 1e-12
    h, _ = devig_two_way(american_to_prob(-200), american_to_prob(170))
    assert 0.63 < h < 0.65


def test_kalshi_links_point_at_the_game_event():
    """Links must name the event (one game); kalshi.com/markets/<series> alone opens an arbitrary game."""
    from gridsim.markets import kalshi_url, quote
    want = {
        "KXNFLSPREAD-26NOV08CLENO": "https://kalshi.com/markets/kxnflspread/pro-football-spread/kxnflspread-26nov08cleno",
        "KXNFLTOTAL-26NOV08CLENO": "https://kalshi.com/markets/kxnfltotal/pro-football-total-points/kxnfltotal-26nov08cleno",
        "KXNFLGAME-26NOV08CLENO": "https://kalshi.com/markets/kxnflgame/nfl-game/kxnflgame-26nov08cleno",
    }
    for ev, url in want.items():
        assert kalshi_url(ev) == url
    # older ledger rows only have the market ticker: the event is the ticker minus its last "-strike" part
    assert kalshi_url(None, "KXNFLSPREAD-26NOV08CLENO-NO3") == want["KXNFLSPREAD-26NOV08CLENO"]
    assert kalshi_url(None, "KXNFLGAME-26NOV08CLENO-CLE") == want["KXNFLGAME-26NOV08CLENO"]
    assert kalshi_url(None, None) is None and kalshi_url(None, "KXNFLGAME") is None
    q = quote({"event_ticker": "KXNFLTOTAL-26NOV08CLENO", "ticker": "KXNFLTOTAL-26NOV08CLENO-42"})
    assert q["url"] == want["KXNFLTOTAL-26NOV08CLENO"]
