"""Kalshi opportunity scanner, signal ledger and signal grading. READ-ONLY: it alerts, it never trades.

A signal fires only when ALL pass (thresholds in config `scanner`):
  1. edge after fees  = sim prob - (ask you'd pay + Kalshi fee) >= min_edge AND > moe_multiple x sim MOE
     (if it clears min_edge but not the MOE test, the game is re-simmed with more sims first)
  2. liquidity: size at the quoted price >= min_depth_dollars, bid/ask spread <= max_spread, volume >= min_volume
  3. fresh inputs: the sim is younger than max_sim_age_hours (else re-sim)
  4. no unresolved key Questionable player: the edge must hold with him playing AND sitting
  5. earned trust: the market type (moneyline / spread / total) has >= trust_min_graded graded GAMES (a game counts
     once per market type, however many lines or price checks) with positive average CLV; until then it is listed
     as "watchlist, not trusted yet"
Confidence: strong (edge >= strong_edge and trusted), moderate (passes all), watchlist.
Re-alerts only when the edge grows by realert_edge_growth, flips side, or disappears.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

import polars as pl

from .api import RunRequest, cached_summary, run_game, week_slate
from .config import Config
from .markets import Kalshi, edges, game_markets, sim_probs

LEDGER_FIELDS = ["ts_utc", "season", "week", "game_id", "kickoff_utc", "market", "ticker", "label", "side", "price",
                 "fee", "p_side", "edge", "moe", "depth_dollars", "spread", "volume", "confidence", "reason",
                 "config_hash", "alerted"]


def _dir(cfg: Config) -> Path:
    d = cfg.resolve_path("signals")
    d.mkdir(parents=True, exist_ok=True)
    return d


def first_sightings(df: pl.DataFrame) -> pl.DataFrame:
    """Graded rows -> one row per contract and side: its FIRST sighting. The ledger logs every signal again at every
    price check (every 30 minutes), so counting raw rows would count one contract dozens of times."""
    if not df.height:
        return df
    return df.sort("ts_utc").unique(subset=["ticker", "side"], keep="first", maintain_order=True)


def trust_table(cfg: Config) -> dict:
    """Per market type: graded GAMES, average CLV, flat-unit P&L -> trusted?

    One game counts once per market type: the many spread (or total) lines of a game are nearly the same bet, and a
    contract seen in many price checks is one signal, so n is the number of distinct games, CLV the average of the
    per-game averages and P&L the sum of the per-game averages (first sighting of each contract)."""
    p = _dir(cfg) / "graded.csv"
    out = {}
    if not p.exists():
        return out
    df = pl.read_csv(p, infer_schema_length=None)
    for mt in df["market"].unique().to_list():
        x = first_sightings(df.filter((pl.col("market") == mt) & pl.col("clv").is_not_null()))
        g = x.group_by("game_id").agg(pl.col("clv").cast(pl.Float64).mean(), pl.col("pnl").cast(pl.Float64).mean(),
                                      pl.len().alias("contracts"))
        n = g.height
        clv = float(g["clv"].mean()) if n else 0.0
        pnl = float(g["pnl"].sum()) if n else 0.0
        trusted = n >= int(cfg.scanner["trust_min_graded"]) and clv > 0 and pnl >= 0
        out[mt] = {"n": n, "contracts": int(g["contracts"].sum()) if n else 0, "avg_clv": clv, "pnl": pnl,
                   "trusted": trusted}
    return out


def _depth(row: dict) -> float | None:
    # size at the YES ask; for NO, the NO ask is the other side of the best YES bid
    size = row.get("yes_ask_size") if row.get("side") == "YES" else row.get("yes_bid_size")
    return size * row["price"] if (size is not None and row.get("price")) else None


def _checks(cfg: Config, r: dict, trust: dict, sim_age_h: float, q_both: dict | None) -> tuple[list[tuple], dict]:
    """The signal rules one side of one contract fails, as (code, text, value) — and its market type's record."""
    sc = cfg.scanner
    fails = []
    e, moe = r["edge"], r["moe"]
    if e <= float(sc["moe_multiple"]) * moe:
        fails.append(("moe", f"edge {100 * e:.1f} within {sc['moe_multiple']}x MOE {100 * moe:.1f}", moe))
    depth = _depth(r)
    if depth is not None and depth < float(sc["min_depth_dollars"]):
        fails.append(("depth", f"depth ${depth:,.0f}", depth))
    # (whole cents: 0.56 - 0.52 is 0.040000000000000036 in floating point, and 4 cents must not fail a 4-cent rule)
    if r.get("spread") is not None and round(100 * r["spread"]) > round(100 * float(sc["max_spread"])):
        fails.append(("spread", f"spread {100 * r['spread']:.0f}c", r["spread"]))
    if (r.get("volume") or 0) < float(sc["min_volume"]):
        fails.append(("volume", f"volume {r.get('volume') or 0:,.0f}", r.get("volume") or 0))
    if sim_age_h > float(sc["max_sim_age_hours"]):
        fails.append(("age", f"sim {sim_age_h:.1f} h old", sim_age_h))
    if q_both is not None:
        alt = q_both.get((r["ticker"], r["side"]))
        if alt is not None and alt < float(sc["min_edge"]):
            fails.append(("flip", f"edge only {100 * alt:.1f} if the questionable player flips", alt))
    tr = trust.get(r["market"], {"n": 0, "avg_clv": 0.0, "trusted": False})
    if not tr["trusted"]:
        fails.append(("trust", f"{r['market']} not trusted yet ({tr['n']} graded games, CLV {tr['avg_clv']:+.3f})", tr["n"]))
    return fails, tr


def evaluate(cfg: Config, summ: dict, rows: list[dict], trust: dict, sim_age_h: float, q_both: dict | None) -> list[dict]:
    sc = cfg.scanner
    out = []
    for r in rows:
        if r.get("edge") is None:
            continue
        e = r["edge"]
        if e < float(sc["min_edge"]):
            continue
        checks, tr = _checks(cfg, r, trust, sim_age_h, q_both)
        fails = [text for _, text, _ in checks]
        depth = _depth(r)
        if fails:
            conf = "watchlist"
        else:
            conf = "strong" if e >= float(sc["strong_edge"]) else "moderate"
        cost = r["price"] + r["fee"]
        reasons = [f"sim {100 * r['p_side']:.0f}% vs {r['side']} ask {100 * r['price']:.0f}c + {100 * r['fee']:.1f}c fee "
                   f"-> edge {100 * e:+.1f} pts, EV ${e / cost:+.2f}/$1"]
        if depth is not None:
            reasons.append(f"${depth:,.0f} at the ask")
        reasons.append(f"{r['market']} record: {tr['n']} graded games, CLV {tr['avg_clv']:+.3f}")
        out.append({**r, "depth_dollars": depth, "confidence": conf, "reason": "; ".join(reasons + fails)})
    out.sort(key=lambda s: -s["edge"])
    return out


# What a failed rule means, in plain words (the Game page, This Week and the auto parlay show these).
def _catch_text(cfg: Config, code: str, value, market: str) -> str:
    """Short, because it sits in a table cell; the page's "How to read this" spells each one out."""
    if code == "moe":
        return "inside the margin of error"
    if code == "depth":
        return f"only ${value:,.0f} offered at this price"
    if code == "spread":
        return f"buy/sell prices {100 * value:.0f}¢ apart"
    if code == "volume":
        return f"hardly traded ({value:,.0f})"
    if code == "age":
        return f"sim is {value:.0f} h old"
    if code == "flip":
        return "depends on a questionable QB"
    return f"no record yet on {MARKET_WORD.get(market, market)} contracts"


MARKET_WORD = {"moneyline": "who-wins", "spread": "spread", "total": "total"}
RATING_RANK = {"signal": 0, "unproven": 1, "catch": 2, "lean": 3, "small": 4, "none": 5}


# An edge this large is more often GridSim missing something the market knows (an injury, a quarterback change, the
# weather) than a mistake by the market: the board says so next to it. (Text only: the rating rules are unchanged.)
BIG_EDGE = 0.10


RATING_TEXT = {"signal": "Signal", "unproven": "Edge, unproven", "catch": "Edge, with a catch",
               "lean": "GridSim leans this way", "small": "Small edge", "none": "No edge"}


def game_board(cfg: Config, summ: dict, mk: dict, trust: dict | None = None, now: dt.datetime | None = None) -> list[dict]:
    """Every side of every Kalshi contract of ONE game: the market's chance, the sim's, the adjusted one
    (markets.both_sides) and a rating — also the sides without an edge, so the page can show the whole board.

    The rating goes by the ADJUSTED edge and the price check's rules: "signal" (passes every rule), "unproven"
    (passes all but the track-record rule), "catch" (fails a liquidity / margin-of-error / freshness rule), "small"
    (an adjusted edge below scanner.min_edge), "none". A side where only the sim ALONE sees an edge of at least
    scanner.min_edge is a "lean": GridSim's opinion, which the adjustment does not credit as an edge.
    row["hard"] says whether a liquidity / margin-of-error / freshness rule fails (for edges and leans alike).
    Not checked here: the questionable-QB test (it needs two more sims; the scheduled price check does it)."""
    from .markets import both_sides
    trust = trust_table(cfg) if trust is None else trust
    now = now or dt.datetime.now(dt.timezone.utc)
    age = 0.0
    try:
        age = (now - dt.datetime.fromisoformat(summ["created_utc"])).total_seconds() / 3600
    except (KeyError, TypeError, ValueError):
        pass
    min_edge = float(cfg.scanner["min_edge"])
    out = []
    for r in both_sides(sim_probs(summ, mk), cfg):
        r["moe"] = r.get("moe") or 0.0
        row = {**r, "depth_dollars": _depth(r), "confidence": None, "catches": [], "hard": False}
        is_edge = r["edge"] >= min_edge
        if is_edge or r["lean"] >= min_edge:
            # the rules are applied to the number the row stands on: the adjusted edge, or the sim's own lean
            checks, _ = _checks(cfg, r if is_edge else {**r, "edge": r["lean"]}, trust, age, None)
            hard = [c for c in checks if c[0] != "trust"]
            row["hard"] = bool(hard)
            row["catches"] = [_catch_text(cfg, code, val, r["market"]) for code, _, val in hard]
            if is_edge:
                row["catches"] += [_catch_text(cfg, code, val, r["market"]) for code, _, val in checks if code == "trust"]
                if r["edge"] >= BIG_EDGE:
                    row["catches"].insert(0, "unusually large — check the news")
                if not checks:
                    row["rating"] = "signal"
                    row["confidence"] = "strong" if r["edge"] >= float(cfg.scanner["strong_edge"]) else "moderate"
                else:
                    row["rating"] = "catch" if hard else "unproven"
                    row["confidence"] = "watchlist"
            else:
                row["rating"] = "lean"
                if r["lean"] >= BIG_EDGE:
                    row["catches"].insert(0, "far from the market — check the news")
        else:
            row["rating"] = "small" if r["edge"] > 0 else "none"
        out.append(row)
    # cleanest first: by rating, leans whose price can really be had before the ones with a catch, then the biggest
    # adjusted edge, then the strongest lean
    out.sort(key=lambda x: (RATING_RANK[x["rating"]], x["hard"] if x["rating"] == "lean" else False,
                            -round(x["edge"], 6) if x["rating"] != "lean" else 0, -x["lean"]))
    return out


def _snapshots(cfg: Config, season: int, week: int) -> list[Path]:
    """A week's saved price snapshots, oldest first (not the one-game files a closing-price backfill writes)."""
    return sorted(p for p in (_dir(cfg) / "snapshots").glob(f"{season}_wk{int(week):02d}_*.json")
                  if "backfilled" not in p.name)


def latest_snapshot(cfg: Config, season: int, week: int) -> Path | None:
    """The newest saved price snapshot of a week (every price check writes one)."""
    files = _snapshots(cfg, season, week)
    return files[-1] if files else None


def _read_snapshot(cfg: Config, season: int, week: int) -> dict:
    """The newest snapshot of the week that can be read (a half-written newest file must not blank the week)."""
    for p in reversed(_snapshots(cfg, season, week)[-5:]):
        try:
            snap = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(snap, dict) and isinstance(snap.get("games"), dict):
            return snap
    return {}


def week_board(cfg: Config, season: int | None = None, week: int | None = None, snapshot: Path | None = None) -> dict:
    """The boards of every upcoming game of the week from the LAST price check (no network, no simulation): Kalshi's
    prices as saved then, against the sims the scheduled check uses (default sim count). Returns
    {"ts_utc", "season", "week", "games": {game_id: {"matchup", "home", "away", "kickoff_utc", "rows" | None,
    "note"}}}; rows is None when the game has no cached sim or no saved prices (note says which)."""
    from .live import kickoff_utc
    season, week, games = week_slate(cfg, season, week)
    out = {"ts_utc": None, "season": season, "week": week, "games": {}}
    if snapshot is not None:
        try:
            snap = json.loads(snapshot.read_text(encoding="utf-8"))
            snap = snap if isinstance(snap, dict) and isinstance(snap.get("games"), dict) else {}
        except (OSError, ValueError):
            snap = {}
    else:
        snap = _read_snapshot(cfg, season, week)
    out["ts_utc"] = snap.get("ts_utc")
    trust = trust_table(cfg)
    now = dt.datetime.now(dt.timezone.utc)
    for g in games:
        ko = kickoff_utc(g)
        if g.get("home_score") is not None or (ko and ko <= now):
            continue
        entry = {"matchup": f"{g['away_team']} @ {g['home_team']}", "home": g["home_team"], "away": g["away_team"],
                 "kickoff_utc": ko.isoformat() if ko else None, "rows": None, "note": ""}
        mk = (snap.get("games") or {}).get(g["game_id"])
        summ = cached_summary(RunRequest(home=g["home_team"], away=g["away_team"], season=season, week=week,
                                         market_lines=True), cfg)
        if not isinstance(mk, dict) or mk.get("error") or not (mk.get("moneyline") or mk.get("spread") or mk.get("total")):
            entry["note"] = "no Kalshi prices saved for this game yet"
        elif summ is None:
            entry["note"] = "not simulated yet at the standard settings"
        else:
            # like the price check: when it made a bigger sim for this game (a borderline edge) and that is still
            # fresh, the bigger one is the better number
            try:
                big = cached_summary(RunRequest(home=g["home_team"], away=g["away_team"], season=season, week=week,
                                                market_lines=True, sims=min(int(cfg.scanner["max_resim_sims"]),
                                                                            int(summ["n_sims"]) * 4)), cfg)
                made = dt.datetime.fromisoformat(big["created_utc"]) if big else None
                if made and (now - made).total_seconds() / 3600 <= float(cfg.scanner["max_sim_age_hours"]):
                    summ = big
            except (KeyError, TypeError, ValueError):
                pass
            try:
                entry["rows"] = game_board(cfg, summ, mk, trust, now)
                for r in entry["rows"]:
                    r.update(game_id=g["game_id"], matchup=entry["matchup"], kickoff_utc=entry["kickoff_utc"])
            except Exception as e:           # one odd game must not blank the week
                entry["note"] = f"could not be priced ({type(e).__name__})"
        out["games"][g["game_id"]] = entry
    return out


PRICE_BANDS = [("under 25¢", 0.0, 0.25), ("25–49¢", 0.25, 0.50), ("50–74¢", 0.50, 0.75), ("75¢ and up", 0.75, 1.01)]


def record_by_price(cfg: Config, season: int | None = None) -> list[dict]:
    """How graded signals did by the price paid: long shots (under 50¢) against favourites. Each contract counts
    once (its first sighting). "needed %" is the average cost with fees, i.e. the share that has to win to break
    even. All confidence levels and model versions are counted together."""
    gp = _dir(cfg) / "graded.csv"
    if not gp.exists():
        return []
    df = pl.read_csv(gp, infer_schema_length=None)
    if season:
        df = df.filter(pl.col("season") == season)
    df = first_sightings(df)
    if not df.height:
        return []
    df = df.with_columns(pl.col("price").cast(pl.Float64), pl.col("fee").cast(pl.Float64), pl.col("pnl").cast(pl.Float64),
                         pl.col("clv").cast(pl.Float64, strict=False),
                         pl.col("won").cast(pl.Utf8).str.to_lowercase().is_in(["true", "1"]).alias("_won"))
    out = []
    for label, lo, hi in PRICE_BANDS + [("all under 50¢", 0.0, 0.50), ("all 50¢ and up", 0.50, 1.01)]:
        x = df.filter((pl.col("price") >= lo) & (pl.col("price") < hi))
        if not x.height:
            continue
        clv = x["clv"].drop_nulls()
        out.append({"price paid": label, "contracts": x.height, "games": x["game_id"].n_unique(),
                    "won %": 100 * float(x["_won"].mean()), "needed %": 100 * float((x["price"] + x["fee"]).mean()),
                    "avg CLV": float(clv.mean()) if clv.len() else None, "P&L (units)": float(x["pnl"].sum())})
    return out


def scan(cfg: Config, season: int | None = None, week: int | None = None, notify_on: bool = False,
         sims: int | None = None, verbose: bool = True, progress=None) -> list[dict]:
    from .live import kickoff_utc
    season, week, games = week_slate(cfg, season, week)
    k = Kalshi(cfg)
    trust = trust_table(cfg)
    state_p = _dir(cfg) / "alert_state.json"
    state = json.loads(state_p.read_text()) if state_p.exists() else {}
    now = dt.datetime.now(dt.timezone.utc)
    all_sigs = []
    snap_games: dict = {}
    watch_data: dict = {}
    from . import positions as _pos
    if sims is not None and int(sims) == int(cfg.sim["n_sims"]):
        sims = None                      # the default, spelled out: same cached sims as the scheduled price check
    # Positions you hold are checked as part of the SCHEDULED price check (which can reach your phone). A scan
    # started in the app sends no notifications, so it must not use up an alert: "Check now" on My positions does that.
    watching = bool(notify_on and _pos.open_positions(cfg))
    if watching:                         # make sure the sims below use the latest injury report
        _pos.refresh_injuries_if_due(cfg, season)
    for gi, g in enumerate(games):
        if progress:
            progress(gi / max(len(games), 1), f"{g['away_team']} @ {g['home_team']}")
        if g.get("home_score") is not None:
            continue
        ko = kickoff_utc(g)
        if ko and ko <= now:
            continue
        req = RunRequest(home=g["home_team"], away=g["away_team"], season=season, week=week, sims=sims, market_lines=True)
        summ = cached_summary(req, cfg)
        age = None
        if summ is not None:
            created = summ.get("created_utc")
            age = (now - dt.datetime.fromisoformat(created)).total_seconds() / 3600 if created else None
        if summ is None or age is None or age > float(cfg.scanner["max_sim_age_hours"]):
            try:
                summ = run_game(req, cfg, refresh=True)
            except Exception as e:       # e.g. a data file being replaced at that moment: skip this game, keep the rest
                print(f"  {g['game_id']}: could not simulate ({type(e).__name__}: {e}); skipped this time")
                continue
            summ["created_utc"] = now.isoformat()
            age = 0.0
        try:
            mk = game_markets(k, g)
        except Exception as e:
            if verbose:
                print(f"  {g['game_id']}: Kalshi unavailable ({e})")
            continue
        snap_games[g["game_id"]] = mk
        if sims is None:                 # (a scan with its own sim count is not comparable with the watch's sims)
            watch_data[g["game_id"]] = (summ, mk)
        rows = edges(sim_probs(summ, mk), cfg)
        # MOE test: re-sim with more sims if an edge is big enough but within the noise
        need = [r for r in rows if r.get("edge") is not None and r["edge"] >= float(cfg.scanner["min_edge"])
                and r["edge"] <= float(cfg.scanner["moe_multiple"]) * r["moe"]]
        if need and summ["n_sims"] < int(cfg.scanner["max_resim_sims"]):
            req.sims = min(int(cfg.scanner["max_resim_sims"]), summ["n_sims"] * 4)
            # the bigger sim of an earlier check is as good as a new one while the data has not changed (a data
            # change gives it a different cache file) and it is not too old: no need to redo it every 30 minutes
            big = cached_summary(req, cfg)
            made = (big or {}).get("created_utc")
            if not made or (now - dt.datetime.fromisoformat(made)).total_seconds() / 3600 > float(cfg.scanner["max_sim_age_hours"]):
                big = run_game(req, cfg, refresh=True)
                big["created_utc"] = now.isoformat()
            summ = big
            rows = edges(sim_probs(summ, mk), cfg)
        # questionable key players: edge must hold in both versions
        q_both = None
        keypos = set(cfg.scanner["key_questionable_positions"])
        av = summ.get("availability") or {}
        qs = [p for t in av.get("teams", {}).values() for p in t.get("listed", []) if 0 < p["p_play"] < 1 and p["pos"] in keypos]
        if qs and any(r.get("edge") and r["edge"] >= float(cfg.scanner["min_edge"]) for r in rows):
            q_both = {}
            for p in qs:
                for flag in ("outs", "ins"):
                    alt_req = RunRequest(**{**req.__dict__, flag: [f"{p['team']}:{p['name']}"]})
                    # like the bigger sim: kept while the data is unchanged and it is not too old (only the
                    # prices change between two checks, and those are applied below)
                    alt = cached_summary(alt_req, cfg)
                    made = (alt or {}).get("created_utc")
                    if not made or ((now - dt.datetime.fromisoformat(made)).total_seconds() / 3600
                                    > float(cfg.scanner["max_sim_age_hours"])):
                        alt = run_game(alt_req, cfg, refresh=True)
                    for ar in edges(sim_probs(alt, mk), cfg):
                        key = (ar["ticker"], ar["side"])
                        q_both[key] = min(q_both.get(key, 1.0), ar["edge"] if ar.get("edge") is not None else -1.0)
        sigs = evaluate(cfg, summ, rows, trust, age or 0.0, q_both)
        for s in sigs:
            s.update({"game_id": g["game_id"], "season": season, "week": week, "kickoff_utc": ko.isoformat() if ko else None,
                      "matchup": f"{g['away_team']} @ {g['home_team']}"})
        all_sigs += sigs
        if verbose:
            for s in sigs[:6]:
                print(f"  [{s['confidence']:<9}] {s['matchup']:<12} {s['label']:<24} {s['side']:<3} {s['reason']}")
    # every scan also saves the prices it fetched: the last snapshot before kickoff is the closing price
    # used to grade signals (closing-line value), so a scheduled scan alone keeps the record going
    if snap_games:
        sd = _dir(cfg) / "snapshots"
        sd.mkdir(exist_ok=True)
        snap_p = sd / f"{season}_wk{week:02d}_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
        tmp = snap_p.with_name(snap_p.name + ".partial")       # (never a half-written snapshot under the real name)
        tmp.write_text(json.dumps({"ts_utc": now.isoformat(), "season": season, "week": week, "games": snap_games}))
        tmp.replace(snap_p)
    # ledger + re-alert logic
    led = _dir(cfg) / "ledger.csv"
    alerts = []
    from .userdata import file_lock, healed as _healed
    with file_lock(led), _healed(led), open(led, "a", newline="", encoding="utf-8") as fh:
        new = fh.tell() == 0
        w = csv.DictWriter(fh, fieldnames=LEDGER_FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        for s in all_sigs:
            key = f"{s['ticker']}|{s['side']}"
            prev = state.get(key)
            alert = s["confidence"] in ("strong", "moderate") and (
                prev is None or s["edge"] - prev.get("edge", 0) >= float(cfg.scanner["realert_edge_growth"]))
            if alert:
                alerts.append(s)
                state[key] = {"edge": s["edge"], "ts": now.isoformat()}
            w.writerow({**s, "ts_utc": now.isoformat(), "config_hash": cfg.hash, "alerted": alert})
    live_keys = {f"{s['ticker']}|{s['side']}" for s in all_sigs if s["confidence"] in ("strong", "moderate")}
    gone = [k_ for k_ in list(state) if k_ not in live_keys]
    for k_ in gone:
        alerts.append({"gone": k_})
        state.pop(k_)
    state_p.write_text(json.dumps(state, indent=1))
    if watching:
        try:
            _pos.watch(cfg, notify_on=notify_on, game_data=watch_data, verbose=verbose)
        except Exception as e:           # the position watch must never break the price check
            print(f"  position watch failed: {type(e).__name__}: {e}")
    from .userdata import auto_backup
    auto_backup(cfg, "daily", min_interval_h=20)        # scans run every 30 min: back up at most once a day
    if progress:
        progress(1.0, "done")
    if notify_on and alerts:
        from .notify import send
        for a in alerts:
            if a.get("gone"):
                send(cfg, "GridSim: edge gone", f"{a['gone']} no longer passes the signal rules")
            else:
                send(cfg, f"GridSim {a['confidence']}: {a['matchup']} {a['label']} {a['side']}", a["reason"], url=a.get("url"),
                     priority="high" if a["confidence"] == "strong" else "default")
    return all_sigs


def snapshot_markets(cfg: Config, season: int | None = None, week: int | None = None) -> Path:
    """Save current Kalshi prices for every game of the week (used for closing-line value)."""
    season, week, games = week_slate(cfg, season, week)
    k = Kalshi(cfg)
    snap = {"ts_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "season": season, "week": week, "games": {}}
    for g in games:
        try:
            snap["games"][g["game_id"]] = game_markets(k, g)
        except Exception as e:
            snap["games"][g["game_id"]] = {"error": str(e)}
    d = _dir(cfg) / "snapshots"
    d.mkdir(exist_ok=True)
    p = d / f"{season}_wk{week:02d}_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    p.write_text(json.dumps(snap))
    return p


def backfill_closing_prices(cfg: Config, season: int | None = None, week: int | None = None,
                            verbose: bool = True, progress=None) -> dict:
    """After a data loss: rebuild missing pre-kickoff Kalshi prices from Kalshi's public price history.

    Only for signals in the ledger whose closing price is missing (that is what grading needs). For each, the last
    hourly candle that ended at or before kickoff gives the bid/ask; it is saved as a snapshot marked
    "backfilled": true, signals/snapshots/<season>_wk<NN>_<time>_backfilled_<game>.json. Kalshi's history is the real
    exchange record, so the closing-line value it yields is honest; the file is still marked so you can tell."""
    led = _dir(cfg) / "ledger.csv"
    rep = {"needed": 0, "filled": 0, "no_history": 0}
    if not led.exists():
        return rep
    df = pl.read_csv(led, infer_schema_length=None)
    if season:
        df = df.filter(pl.col("season") == season)
    if week:
        df = df.filter(pl.col("week") == week)
    need: dict[tuple, set] = {}
    index = None
    for r in df.iter_rows(named=True):
        ko = r.get("kickoff_utc")
        if not ko or not r.get("ticker") or dt.datetime.fromisoformat(str(ko)) > dt.datetime.now(dt.timezone.utc):
            continue
        if index is None:
            index = _closing_index(cfg)
        if _closing_price(cfg, r["game_id"], r["ticker"], r["side"], ko, index) is None:
            need.setdefault((r["game_id"], str(ko), int(r["season"]), int(r["week"])), set()).add(r["ticker"])
    rep["needed"] = sum(len(v) for v in need.values())
    if not need:
        return rep
    k = Kalshi(cfg)
    kinds = {v: kd for kd, v in k.nfl_series().items()}
    sd = _dir(cfg) / "snapshots"
    sd.mkdir(exist_ok=True)
    for gi, ((gid, ko, s_, w_), tickers) in enumerate(sorted(need.items())):
        if progress:
            progress(gi / len(need), gid)
        ko_ts = int(dt.datetime.fromisoformat(ko).timestamp())
        game = {"moneyline": {}, "spread": [], "total": []}
        last_end = None
        for t in sorted(tickers):
            series = t.split("-")[0]
            try:
                candles = k.candlesticks(series, t, ko_ts - 12 * 3600, ko_ts)
            except Exception as e:
                if verbose:
                    print(f"  {t}: no price history ({e})")
                rep["no_history"] += 1
                continue
            c = [x for x in candles if int(x.get("end_period_ts", 0)) <= ko_ts
                 and (x.get("yes_bid") or {}).get("close_dollars") is not None
                 and (x.get("yes_ask") or {}).get("close_dollars") is not None]
            if not c:
                rep["no_history"] += 1
                continue
            last = c[-1]
            row = {"ticker": t, "yes_bid": float(last["yes_bid"]["close_dollars"]),
                   "yes_ask": float(last["yes_ask"]["close_dollars"]), "backfilled": True,
                   "candle_end_utc": dt.datetime.fromtimestamp(int(last["end_period_ts"]), dt.timezone.utc).isoformat()}
            kind = kinds.get(series, "total")
            if kind == "moneyline":
                game["moneyline"][t] = row
            else:
                game[kind].append(row)
            last_end = max(last_end or 0, int(last["end_period_ts"]))
            rep["filled"] += 1
        if last_end:
            ts = dt.datetime.fromtimestamp(last_end, dt.timezone.utc)
            (sd / f"{s_}_wk{w_:02d}_{ts.strftime('%Y%m%dT%H%M%SZ')}_backfilled_{gid}.json").write_text(json.dumps(
                {"ts_utc": ts.isoformat(), "season": s_, "week": w_, "backfilled": True,
                 "source": "Kalshi candlesticks (hourly, last close at or before kickoff)", "games": {gid: game}}))
    if verbose:
        print(f"backfilled {rep['filled']} of {rep['needed']} missing closing prices"
              + (f" ({rep['no_history']} had no history)" if rep["no_history"] else ""))
    return rep


def _closing_index(cfg: Config, weeks: set[tuple[int, int]] | None = None) -> dict:
    """{(game_id, ticker): [(snapshot time, mid price), ...]} in file order, from one pass over the saved price
    snapshots. weeks: only read the files of these (season, week) pairs (the file name starts with them).

    Every price check saves a snapshot (dozens a day), so the files are read once per grading run, never once per
    signal; only the two numbers needed are kept, not the parsed files."""
    idx: dict[tuple, list] = {}
    prefixes = None if weeks is None else tuple(f"{s}_wk{w:02d}_" for s, w in weeks)
    for p in sorted((_dir(cfg) / "snapshots").glob("*.json")):
        if prefixes is not None and not p.name.startswith(prefixes):
            continue
        try:
            snap = json.loads(p.read_text())
            ts = str(snap["ts_utc"])
            games = snap["games"]
        except (OSError, ValueError, KeyError, TypeError):
            continue                     # a half-written or damaged snapshot must not stop the grading
        for gid, mk in games.items():
            if not isinstance(mk, dict):
                continue
            for row in list((mk.get("moneyline") or {}).values()) + (mk.get("spread") or []) + (mk.get("total") or []):
                if row.get("ticker") and row.get("yes_bid") is not None and row.get("yes_ask") is not None:
                    idx.setdefault((gid, row["ticker"]), []).append((ts, (row["yes_bid"] + row["yes_ask"]) / 2))
    return idx


def _closing_price(cfg: Config, game_id: str, ticker: str, side: str, kickoff: str | None,
                   index: dict | None = None) -> float | None:
    """The last saved Kalshi mid price for this side at or before kickoff. index: from _closing_index (pass it when
    pricing many signals)."""
    if index is None:
        index = _closing_index(cfg)
    best = None
    for ts, mid in index.get((game_id, ticker), ()):
        if kickoff and ts > str(kickoff):
            continue
        best = mid if side == "YES" else 1 - mid
    return best


def grade_signals(cfg: Config) -> int:
    """Score ledger signals of finished games: result, CLV (closing mid vs signal price), flat 1-unit P&L after fees."""
    from .data import load_schedules
    led = _dir(cfg) / "ledger.csv"
    if not led.exists():
        return 0
    df = pl.read_csv(led, infer_schema_length=None)
    sched = load_schedules(cfg)
    res = {r["game_id"]: r for r in sched.filter(pl.col("home_score").is_not_null()).iter_rows(named=True)}
    gp = _dir(cfg) / "graded.csv"
    done = set()
    if gp.exists():
        done = {(r["ts_utc"], r["ticker"], r["side"]) for r in pl.read_csv(gp, infer_schema_length=None).iter_rows(named=True)}
    rows = []
    todo = [r for r in df.iter_rows(named=True)
            if r["game_id"] in res and (r["ts_utc"], r["ticker"], r["side"]) not in done]
    index = None            # built on the first signal that needs a closing price: nothing new = no file read
    for r in todo:
        g = res[r["game_id"]]
        margin, total = g["home_score"] - g["away_score"], g["home_score"] + g["away_score"]
        yes = _resolve(r, g, margin, total)
        if yes is None:
            continue
        cost = float(r["price"]) + float(r["fee"])
        if r["market"] == "moneyline" and margin == 0:
            # a tie: Kalshi pays 50 cents per contract to each side of a "who wins" market
            pnl = 0.5 / cost - 1.0
            win = pnl > 0
        else:
            win = yes if r["side"] == "YES" else (not yes)
            pnl = (1.0 / cost - 1.0) if win else -1.0
        if index is None:
            try:
                weeks = {(int(x["season"]), int(x["week"])) for x in todo}
            except (KeyError, TypeError, ValueError):
                weeks = None
            index = _closing_index(cfg, weeks)
        close = _closing_price(cfg, r["game_id"], r["ticker"], r["side"], r.get("kickoff_utc"), index)
        rows.append({**r, "won": win, "pnl": pnl, "closing": close, "clv": (close - float(r["price"])) if close is not None else None})
    if rows:
        from .userdata import file_lock, healed as _healed
        with file_lock(gp), _healed(gp), open(gp, "a", newline="", encoding="utf-8") as fh:
            new = fh.tell() == 0
            w = csv.DictWriter(fh, fieldnames=LEDGER_FIELDS + ["won", "pnl", "closing", "clv"], extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerows(rows)
    return len(rows)


def _resolve(r: dict, g: dict, margin: float, total: float) -> bool | None:
    import re
    lab = r["label"]
    if r["market"] == "moneyline":
        team = lab.split()[0]
        return (margin > 0) if team == g["home_team"] else (margin < 0)
    if r["market"] == "spread":
        m = re.match(r"^(\w+) wins by ([\d.]+)\+", lab)
        if not m:
            return None
        team, line = m.group(1), float(m.group(2))
        tm = margin if team == g["home_team"] else -margin
        return tm > line
    if r["market"] == "total":
        m = re.match(r"^over ([\d.]+)", lab)
        return total > float(m.group(1)) if m else None
    return None


def signals_by_day(cfg: Config, season: int | None = None) -> list[dict]:
    """Graded signals grouped by the weekday they were seen (US Eastern), e.g. Tuesday vs Friday. A signal seen in
    many scans on one day counts once for that day (its first sighting that day)."""
    from .live import ET, WEEKDAYS
    gp = _dir(cfg) / "graded.csv"
    if not gp.exists():
        return []
    df = pl.read_csv(gp, infer_schema_length=None)
    if season:
        df = df.filter(pl.col("season") == season)
    if not df.height:
        return []
    rows = []
    for r in df.iter_rows(named=True):
        try:
            t = dt.datetime.fromisoformat(str(r["ts_utc"])).astimezone(ET)
        except ValueError:
            continue
        rows.append({**r, "_day": WEEKDAYS[t.weekday()], "_date": t.date().isoformat()})
    first = {}
    for r in sorted(rows, key=lambda r: str(r["ts_utc"])):
        first.setdefault((r["ticker"], r["side"], r["_date"]), r)
    out = []
    for day in WEEKDAYS:
        for mt in sorted({r["market"] for r in first.values()}):
            x = [r for r in first.values() if r["_day"] == day and r["market"] == mt]
            if not x:
                continue
            clv = [float(r["clv"]) for r in x if r.get("clv") not in (None, "")]
            out.append({"day": day, "market": mt, "signals": len(x),
                        "hit %": 100 * sum(str(r["won"]).lower() in ("true", "1") for r in x) / len(x),
                        "avg CLV": sum(clv) / len(clv) if clv else None,
                        "P&L (units)": sum(float(r["pnl"]) for r in x if r.get("pnl") not in (None, ""))})
    return out


def signals_report(cfg: Config, season: int | None = None) -> str:
    gp = _dir(cfg) / "graded.csv"
    if not gp.exists():
        return "no graded signals yet"
    df = pl.read_csv(gp, infer_schema_length=None)
    if season:
        df = df.filter(pl.col("season") == season)
    df = first_sightings(df)                 # each contract once (not once per 30-minute price check)
    L = [f"{'market':<10}{'confidence':<11}{'n':>5}{'hit%':>7}{'avg CLV':>9}{'P&L (units)':>13}"]
    for (mt, conf), x in sorted(df.group_by(["market", "confidence"]), key=lambda kv: kv[0]):
        clv = x["clv"].drop_nulls()
        L.append(f"{mt:<10}{conf:<11}{x.height:>5}{100 * x['won'].cast(pl.Float64).mean():>7.1f}"
                 f"{(clv.mean() if clv.len() else float('nan')):>9.3f}{x['pnl'].sum():>13.2f}")
    tr = trust_table(cfg)
    L.append("trust: " + ", ".join(f"{k}: {'trusted' if v['trusted'] else 'watchlist'} ({v['n']} games, CLV {v['avg_clv']:+.3f})"
                                   for k, v in tr.items()))
    return "\n".join(L)
