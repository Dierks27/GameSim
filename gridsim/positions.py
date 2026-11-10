"""Position watch (READ-ONLY): you tell GridSim which Kalshi contracts you hold; it watches them and alerts you.

GridSim never places orders, never sees your Kalshi account and never needs its keys: a position is just a note you
enter (contract, side, price paid, how many), kept in positions/positions.json in your data folder.

At every price check (the scheduled scan every 30 minutes, `gridsim watch` every 10 minutes in the last hours before
kickoff, or "Check now" in the app) each open position is compared with the last look:
  price jump     Kalshi's price moved >= watch.price_jump since the last alert (a sudden move usually means news)
  injury change  the expected starting QB changed, a player on the injury report changed status, or a player the sim
                 expected to touch the ball went to a reserve list / came back (the injury report is re-downloaded
                 every watch.injury_refresh_minutes while positions are open; practice-squad shuffles are ignored)
  sim move       GridSim re-simulated and its chance for your side moved >= watch.sim_move beyond its margin of error
  settled        the game is final: won / lost (a tie pays 50 cents on "who wins") and the profit or loss after fees
Every alert states the facts (prices, GridSim's number, what selling now would bring); the decision is yours.
Alerts are logged in positions/alerts.csv and pushed through notify (ntfy / Discord / Windows toast).
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import time
import uuid
from pathlib import Path

from .config import Config

ALERT_FIELDS = ["ts_utc", "game_id", "position_ids", "kind", "title", "message", "url"]


# ---- storage ----------------------------------------------------------------------------------------------------------

def _dir(cfg: Config) -> Path:
    d = cfg.resolve_path("positions")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    except (OSError, ValueError):
        return default


def _write_json(p: Path, data) -> None:
    tmp = p.with_name(f"{p.name}.{os.getpid()}.partial")
    tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    for wait in (0, 0.2, 0.5, 1, 2):       # Windows: the app / a sync tool may have the file open for a moment
        time.sleep(wait)
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            if wait == 2:
                tmp.unlink(missing_ok=True)
                raise


def load_positions(cfg: Config) -> list[dict]:
    return list(_read_json(_dir(cfg) / "positions.json", {}).get("positions", []))


def _update(cfg: Config, fn, removed: list[str] | None = None) -> list[dict]:
    """Read-modify-write under a lock (the scheduled watch and the app may both be changing positions).
    `removed`: ids to remember as removed, so bringing in an old backup never brings them back."""
    from .userdata import file_lock
    p = _dir(cfg) / "positions.json"
    with file_lock(p):
        doc = _read_json(p, {})
        positions = list(doc.get("positions", []))
        fn(positions)
        gone = sorted(set(doc.get("removed", [])) | set(removed or []))
        _write_json(p, {"positions": positions, **({"removed": gone} if gone else {})})
    return positions


def open_positions(cfg: Config) -> list[dict]:
    return [p for p in load_positions(cfg) if p.get("status") == "open"]


def add_position(cfg: Config, *, game: dict, market: str, ticker: str, label: str, side: str, price: float,
                 contracts: int, p_side_entry: float | None = None, url: str | None = None, note: str = "") -> dict:
    from .live import kickoff_utc
    side = side.upper()
    if side not in ("YES", "NO"):
        raise ValueError("side must be YES or NO")
    if not 0 < float(price) < 1:
        raise ValueError("the price is in dollars per contract, between 0.01 and 0.99 (55 cents = 0.55)")
    if int(contracts) < 1:
        raise ValueError("at least 1 contract")
    ko = kickoff_utc(game)
    pos = {"id": uuid.uuid4().hex[:8], "created_utc": _now().isoformat(timespec="seconds"),
           "season": int(game["season"]), "week": int(game["week"]), "game_id": game["game_id"],
           "home": game["home_team"], "away": game["away_team"],
           "matchup": f"{game['away_team']} @ {game['home_team']}", "kickoff_utc": ko.isoformat() if ko else None,
           "market": market, "ticker": ticker, "label": label, "side": side, "price": round(float(price), 4),
           "contracts": int(contracts), "p_side_entry": p_side_entry, "url": url, "note": note, "status": "open"}
    _update(cfg, lambda ps: ps.append(pos))
    from .userdata import auto_backup
    auto_backup(cfg, "position", min_interval_h=1)
    return pos


def close_position(cfg: Config, pos_id: str, exit_price: float) -> dict | None:
    """You sold it: record the price you got. P&L = what you got - what you paid - Kalshi's fees on both trades."""
    if not 0 <= float(exit_price) <= 1:
        raise ValueError("the price is in dollars per contract, between 0 and 1")
    hit = {}

    def fn(ps):
        for p in ps:
            if p["id"] == pos_id and p.get("status") == "open":
                p.update({"status": "sold", "closed_utc": _now().isoformat(timespec="seconds"),
                          "exit_price": round(float(exit_price), 4), "pnl": pnl_total(p, float(exit_price), cfg, sold=True)})
                hit.update(p)
    _update(cfg, fn)
    return hit or None


def delete_position(cfg: Config, pos_id: str) -> None:
    """Entered by mistake: remove it entirely (its id is remembered, so an old backup can't bring it back)."""
    _update(cfg, lambda ps: ps.__setitem__(slice(None), [p for p in ps if p["id"] != pos_id]), removed=[pos_id])


# ---- money ------------------------------------------------------------------------------------------------------------

def pnl_total(pos: dict, exit_value: float, cfg: Config, sold: bool) -> float:
    """Profit or loss in dollars. Kalshi charges its fee on each trade (buying, and selling early), not at settlement."""
    from .markets import fee
    n = int(pos["contracts"])
    cost = n * float(pos["price"]) + fee(float(pos["price"]), cfg, n)
    got = n * float(exit_value) - (fee(float(exit_value), cfg, n) if sold and 0 < exit_value < 1 else 0.0)
    return round(got - cost, 2)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _c(x: float | None) -> str:
    return "?" if x is None else f"{100 * x:.1f}".rstrip("0").rstrip(".") + "¢"       # 68.5¢, 55¢


def _pct(x: float | None) -> str:
    return "?" if x is None else f"{100 * x:.0f}%"


def _usd(x: float) -> str:
    return f"{'+' if x >= 0 else '−'}${abs(x):,.2f}"


# ---- what a position looks like right now -------------------------------------------------------------------------------

def find_contract(mk: dict, ticker: str) -> dict | None:
    for row in list((mk.get("moneyline") or {}).values()) + (mk.get("spread") or []) + (mk.get("total") or []):
        if row.get("ticker") == ticker:
            return row
    return None


def view(pos: dict, summ: dict | None, mk: dict | None, cfg: Config) -> dict:
    """Current numbers for one position: Kalshi's price for your side, what selling now would bring, GridSim's chance."""
    from .markets import sim_probs
    out = {"mid": None, "sell": None, "p_side": None, "if_sold_now": None}
    q = find_contract(mk or {}, pos["ticker"])
    yes = pos["side"] == "YES"
    if q:
        yb, ya = q.get("yes_bid"), q.get("yes_ask")
        if yb is not None and ya is not None and ya > 0:
            mid_yes = (yb + ya) / 2
            out["mid"] = mid_yes if yes else 1 - mid_yes
            sell = yb if yes else 1 - ya            # selling means hitting the bid on your side
            if sell is not None and 0 < sell < 1:
                out["sell"] = sell
                out["if_sold_now"] = pnl_total(pos, sell, cfg, sold=True)
    if summ is not None and mk:
        for r in sim_probs(summ, mk):
            if r.get("ticker") == pos["ticker"]:
                out["p_side"] = r["p_yes"] if yes else 1 - r["p_yes"]
                break
    out["n_sims"] = (summ or {}).get("n_sims")
    return out


# ---- injuries -----------------------------------------------------------------------------------------------------------

# what the roster file says about a player who is not on the active roster — a status, not a claim about when it changed
ROSTER_WORDS = {"RES": "is on a reserve list (injured reserve or similar)", "DEV": "is on the practice squad",
                "CUT": "has been released", "RET": "has retired", "SUS": "is suspended", "EXE": "is on the exempt list",
                "TRD": "has been traded away", "TRC": "is off the roster", "TRT": "is off the roster"}
ROLE_MIN_VOLUME = 2.0       # passes + carries + targets per game the sim expects before a player counts as "in the plan"


def avail_snapshot(summ: dict, cfg: Config) -> dict:
    """Who starts at QB, who is on the injury report (and how likely to play), who is on a reserve list, and who the
    sim expects to touch the ball ("roles") — for the two teams, from the sim's own inputs."""
    keep = set(cfg.watch.get("positions") or [])
    out = {}
    plays = summ.get("players") or {}
    for team, t in ((summ.get("availability") or {}).get("teams") or {}).items():
        qb = max(t.get("qb") or [], key=lambda q: q.get("p_start", 0), default=None)
        players = {}
        for p in t.get("listed") or []:
            if keep and p.get("pos") not in keep:
                continue
            players[p["name"]] = {"pos": p.get("pos"), "p_play": round(float(p.get("p_play", 1.0)), 2),
                                  "status": p.get("status") or "",
                                  "roster": str(p.get("reason") or "").startswith("roster status")}
        roles = {p["name"]: round(float(p.get("volume") or 0), 1) for p in plays.get(team) or []
                 if float(p.get("volume") or 0) >= ROLE_MIN_VOLUME and (not keep or p.get("pos") in keep)}
        out[team] = {"qb": qb["name"] if qb else None, "players": players, "roles": roles}
    return out


def _report_text(e: dict) -> str:
    s = (e.get("status") or "").strip()
    if e["p_play"] <= 0:
        return s or "Out"
    return f"{s or 'listed'} ({100 * e['p_play']:.0f}% to play)" if e["p_play"] < 1 else (s or "expected to play")


def avail_changes(old: dict | None, new: dict) -> tuple[list[str], bool]:
    """Plain-English differences between two snapshots that can matter for the game (nothing on the first look), and
    whether any of them involves a quarterback or a player the sim had in its plan.

    Reported: a different expected starting QB; a change for a player on the injury report; a player the sim expected
    to touch the ball going to a reserve list / being released; a player coming back from one into the sim's plan.
    Ignored: practice-squad signings and releases and other roster bookkeeping for players who were not going to play."""
    if not old:
        return [], False
    out, big = [], False
    empty = {"qb": None, "players": {}, "roles": {}}
    for team in sorted(set(old) | set(new)):
        o, n = {**empty, **(old.get(team) or {})}, {**empty, **(new.get(team) or {})}
        if o.get("qb") and n.get("qb") and o["qb"] != n["qb"]:
            def mine(snap, name):       # the user's own Game-page change (out / force in / starting QB) for this QB
                return ((snap.get("players") or {}).get(name) or {}).get("status") == "override"
            if mine(n, n["qb"]) or mine(n, o["qb"]):
                out.append(f"{team}: the starting QB in GridSim's sims is now {n['qb']} (was {o['qb']}) because of "
                           "your own lineup change on the Game page (not the injury report)")
            elif mine(o, o["qb"]) or mine(o, n["qb"]):
                out.append(f"{team}: the starting QB in GridSim's sims is now {n['qb']} (was {o['qb']}): your own "
                           "lineup change on the Game page is no longer in place")
            else:
                out.append(f"{team}: expected starting QB is now {n['qb']} (was {o['qb']})")
                big = True
        if "roles" not in (old.get(team) or {}):
            continue        # a snapshot saved by an earlier version (no roster flags): only the QB can be compared
        for name in sorted(set(o["players"]) | set(n["players"])):
            a, b = o["players"].get(name), n["players"].get(name)
            pa, pb = (a or {}).get("p_play", 1.0), (b or {}).get("p_play", 1.0)
            if not (abs(pa - pb) >= 0.15 or (pa > 0) != (pb > 0)):
                continue
            pos = (b or a).get("pos") or "?"
            a_roster, b_roster = bool(a and a.get("roster")), bool(b and b.get("roster"))
            had_role, has_role = name in (o.get("roles") or {}), name in (n.get("roles") or {})
            who = f"{team}: {name} ({pos})"
            a_mine, b_mine = (a or {}).get("status") == "override", (b or {}).get("status") == "override"
            if a_mine or b_mine:                      # the user's own lineup change (Game page): say so; not news
                if b_mine:
                    out.append(f"{who} is {'out' if pb <= 0 else 'playing'} in GridSim's sims because of your own "
                               "lineup change on the Game page (not the injury report)")
                else:
                    out.append(f"{who}: your own lineup change for him is no longer in place"
                               + (f"; he {ROSTER_WORDS.get(b.get('status'), 'is off the active roster')}" if b_roster
                                  else f"; the injury report has him {_report_text(b)}" if b is not None else ""))
                continue
            if b_roster:                              # now on a reserve list / released / practice squad
                if not (had_role or (a is not None and not a_roster)):
                    continue                          # was never going to play: bookkeeping
                out.append(f"{who} {ROSTER_WORDS.get(b.get('status'), 'is off the active roster')}"
                           + (f" (was {_report_text(a)})" if a is not None and not a_roster else ""))
            elif a_roster:                            # was on a reserve list; now active (b is None) or on the report
                if b is None and not has_role:
                    continue
                # on the injury report now: he may still be on the reserve list (players practising their way back)
                out.append(f"{who} is back on the active roster" if b is None else
                           f"{who} is now on the injury report: {_report_text(b)} (was off the active roster at "
                           "the last check)")
            elif a is None:
                out.append(f"{who} newly on the injury report: {_report_text(b)}")
            elif b is None:
                out.append(f"{who} is off the injury report (expected to play; was {_report_text(a)})")
            else:
                out.append(f"{who} {_report_text(a)} → {_report_text(b)}")
            big = big or pos == "QB" or had_role or has_role
    return out, big


def refresh_injuries_if_due(cfg: Config, season: int, verbose: bool = False) -> bool:
    """Re-download rosters / depth charts / injury report if the last watch refresh is older than
    watch.injury_refresh_minutes. Cached sims stay valid unless the content really changed (see api.cache_path).
    The time of the last refresh has its own small file, so no one else's write can lose it."""
    stamp = _dir(cfg) / "injury_refresh.json"
    last = _read_json(stamp, {}).get("checked_utc")
    due = float(cfg.watch.get("injury_refresh_minutes", 45)) * 60
    try:
        if last and (_now() - dt.datetime.fromisoformat(last)).total_seconds() < due:
            return False
    except ValueError:
        pass
    _write_json(stamp, {"checked_utc": _now().isoformat(timespec="seconds")})   # first: an offline PC retries in 45 min
    try:
        from .availability import SOURCES, _load_source
        for name in SOURCES:
            _load_source(name, season, cfg, force=True, verbose=verbose)
    except Exception as e:  # network trouble: keep watching with what we have
        print(f"  injury refresh failed: {type(e).__name__}: {str(e)[:120]}")
    return True


# ---- the watch ----------------------------------------------------------------------------------------------------------

def _game(cfg: Config, pos: dict) -> dict | None:
    import polars as pl

    from .data import load_schedules
    g = load_schedules(cfg).filter(pl.col("game_id") == pos["game_id"])
    return g.row(0, named=True) if g.height else None


def _settle(pos: dict, g: dict, cfg: Config) -> dict | None:
    from .scanner import _resolve
    margin, total = g["home_score"] - g["away_score"], g["home_score"] + g["away_score"]
    yes = _resolve({"market": pos["market"], "label": pos["label"]}, g, margin, total)
    if yes is None:
        return None
    if pos["market"] == "moneyline" and margin == 0:
        result, value = "tie", 0.5          # Kalshi's rule for a tied game: 50 cents per contract to each side
    else:
        won = yes if pos["side"] == "YES" else (not yes)
        result, value = ("won" if won else "lost"), (1.0 if won else 0.0)
    return {"status": "settled", "closed_utc": _now().isoformat(timespec="seconds"), "result": result,
            "exit_price": value, "pnl": pnl_total(pos, value, cfg, sold=False),
            "final_score": f"{g['away_team']} {g['away_score']} – {g['home_team']} {g['home_score']}"}


def _line(pos: dict, v: dict, st: dict) -> str:
    """One position in plain words: what you hold, what you paid, where it stands."""
    n = pos["contracts"]
    parts = [f"{pos['label']} ({pos['side'].title()}) ×{n}: you paid {_c(pos['price'])}"]
    if v.get("mid") is not None:
        parts.append(f"Kalshi now {_c(v['mid'])}")
    if v.get("sell") is not None:
        parts.append(f"selling now would bring about {_c(v['sell'])} each ({_usd(v['if_sold_now'])} in total after fees)")
    if v.get("p_side") is not None:
        was = st.get("ref_p")
        parts.append(f"GridSim's chance {_pct(v['p_side'])}" + (f" (was {_pct(was)})" if was is not None and abs(was - v["p_side"]) >= 0.005 else ""))
    return "; ".join(parts) + "."


LAST_PROBLEMS: dict = {}     # what the most recent watch() in this process could not read: {game_id: {matchup, what}}


def _sim_noise(p0: float, n0, p1: float, n1) -> float:
    """How far two simulated chances can differ by simulation luck alone (95%)."""
    n0, n1 = max(int(n0 or n1 or 2000), 1), max(int(n1 or n0 or 2000), 1)
    return 1.96 * (p0 * (1 - p0) / n0 + p1 * (1 - p1) / n1) ** 0.5


def watch(cfg: Config, notify_on: bool = False, near_only: bool = False, game_data: dict | None = None,
          verbose: bool = True, refresh_injuries: bool = True) -> list[dict]:
    """Check every open position; return the alerts raised (also logged, and pushed when notify_on).

    near_only: only games kicking off within watch.near_kickoff_hours (the 10-minute task; the 30-minute scan covers
    the rest). game_data: {game_id: (summary, markets)} already fetched by the caller (the scan), to avoid re-doing it."""
    from .userdata import file_lock
    LAST_PROBLEMS.clear()
    if not any(p.get("status") == "open" for p in load_positions(cfg)):
        return []
    with file_lock(_dir(cfg) / "watch_state.json", timeout=5) as lk:
        if lk.fd is None:               # another watch is running right now: let it finish
            LAST_PROBLEMS["_busy"] = {"matchup": "Check", "what": "another check is running right now; try again "
                                                                  "in a minute"}
            return []
        return _watch(cfg, notify_on, near_only, game_data or {}, verbose, refresh_injuries)


def _watch(cfg, notify_on, near_only, game_data, verbose, refresh_injuries) -> list[dict]:
    from .api import RunRequest, cached_summary, run_game
    from .live import kickoff_utc
    from .markets import Kalshi, game_markets
    now = _now()
    positions = [p for p in load_positions(cfg) if p.get("status") == "open"]
    st_p = _dir(cfg) / "watch_state.json"
    state = _read_json(st_p, {})
    pst, gst = state.setdefault("positions", {}), state.setdefault("games", {})
    for old_id in [i for i in pst if i not in {p["id"] for p in positions}]:
        pst.pop(old_id)                                 # sold, settled or removed: nothing left to remember
    near_h = float(cfg.watch.get("near_kickoff_hours", 3))
    by_game: dict[str, list[dict]] = {}
    for p in positions:
        by_game.setdefault(p["game_id"], []).append(p)
    alerts, updates, problems, carried, looked = [], {}, {}, {}, 0
    k = None
    refreshed = False
    for gid, plist in by_game.items():
        try:
            g = _game(cfg, plist[0])
        except Exception as e:
            problems[gid] = f"the schedule could not be read ({type(e).__name__})"
            continue
        if g is None:
            continue
        # -- final: settle
        if g.get("home_score") is not None:
            looked += 1
            lines = []
            score = f"{g['away_team']} {g['away_score']} – {g['home_team']} {g['home_score']}"
            for p in plist:
                res = _settle(p, g, cfg)
                if res:
                    updates[p["id"]] = res
                    word = "TIE (Kalshi pays 50¢ per contract)" if res["result"] == "tie" else res["result"].upper()
                    lines.append(f"{p['label']} ({p['side'].title()}) ×{p['contracts']} {word}: "
                                 f"{_usd(res['pnl'])} after fees.")
            if lines:
                alerts.append({"kind": "settled", "game_id": gid, "ids": [p["id"] for p in plist], "priority": "default",
                               "title": f"GridSim watch: {plist[0]['matchup']} is final",
                               "message": f"Final score {score}. " + " ".join(lines), "url": plist[0].get("url")})
            continue
        # the kickoff time comes from the schedule as it is NOW (games get moved; week 18 has placeholder times)
        ko = kickoff_utc(g)
        if ko is None and plist[0].get("kickoff_utc"):
            ko = dt.datetime.fromisoformat(plist[0]["kickoff_utc"])
        for p in plist:
            if ko is not None and p.get("kickoff_utc") != ko.isoformat():
                updates.setdefault(p["id"], {})["kickoff_utc"] = ko.isoformat()
        if ko and ko <= now:
            continue                                    # in play: nothing to compare the pre-game sim with
        if near_only and (ko is None or (ko - now).total_seconds() > near_h * 3600):
            if gid in (state.get("problems") or {}):    # not looked at by this run: the last full check's finding stands
                carried[gid] = state["problems"][gid]
            continue
        looked += 1
        # -- fresh inputs: injury report, sim, prices
        if gid in game_data:
            summ, mk = game_data[gid]
        else:
            if refresh_injuries and not refreshed:
                refresh_injuries_if_due(cfg, int(plist[0]["season"]), verbose=False)
                refreshed = True
            req = RunRequest(home=g["home_team"], away=g["away_team"], season=int(plist[0]["season"]),
                             week=int(plist[0]["week"]), market_lines=True)
            try:
                summ = cached_summary(req, cfg)
                if summ is None:
                    summ = run_game(req, cfg, refresh=True)
                    summ["created_utc"] = now.isoformat()
            except Exception as e:     # e.g. a data file being replaced at that moment: try again at the next check
                problems[gid] = f"GridSim could not simulate the game ({type(e).__name__}: {str(e)[:100]})"
                continue
            try:
                k = k or Kalshi(cfg)
                mk = game_markets(k, g)
            except Exception as e:
                problems[gid] = f"Kalshi's prices could not be read ({type(e).__name__}: {str(e)[:100]})"
                mk = None
        made_with = (summ.get("meta") or {}).get("config_hash")     # which model + settings produced this number
        snap = avail_snapshot(summ, cfg)
        changes, big = avail_changes((gst.get(gid) or {}).get("avail"), snap)
        new_gst = {"avail": snap, "ts": now.isoformat(timespec="seconds")}
        jump_t, sim_t = float(cfg.watch.get("price_jump", 0.06)), float(cfg.watch.get("sim_move", 0.05))
        views, jumped, moved = {}, [], []
        for p in plist:
            v = view(p, summ, mk, cfg)
            views[p["id"]] = v
            s = pst.setdefault(p["id"], {})
            if v["mid"] is not None:
                if s.get("ref_mid") is None:
                    s["ref_mid"] = v["mid"]
                elif abs(v["mid"] - s["ref_mid"]) >= jump_t - 1e-9:
                    jumped.append((p, v, s["ref_mid"]))
            if v["p_side"] is not None:
                if s.get("ref_p") is None:
                    s["ref_p"], s["ref_n"], s["ref_hash"] = v["p_side"], v.get("n_sims"), made_with
                    if p.get("p_side_entry") is None:
                        updates.setdefault(p["id"], {})["p_side_entry"] = round(v["p_side"], 4)
                elif abs(v["p_side"] - s["ref_p"]) >= sim_t + _sim_noise(s["ref_p"], s.get("ref_n"), v["p_side"], v.get("n_sims")):
                    # beyond simulation luck: two sims of the same game differ a little even when nothing changed
                    moved.append((p, v, s["ref_p"]))
        url = plist[0].get("url")
        ids = [p["id"] for p in plist]
        all_lines = " ".join(_line(p, views[p["id"]], pst[p["id"]]) for p in plist)
        if changes:
            # a number made with another model / other settings: the difference is not only this news
            remade = any(pst[p["id"]].get("ref_p") is not None and pst[p["id"]].get("ref_hash") != made_with
                         and views[p["id"]]["p_side"] is not None for p in plist)
            alerts.append({"kind": "injury", "game_id": gid, "ids": ids, "priority": "high" if big else "default",
                           "url": url, "title": f"GridSim watch: {plist[0]['matchup']} — lineup news",
                           "message": "; ".join(changes) + ". GridSim re-simulated with it. "
                                      + ("GridSim's settings or model also changed since its last number for this "
                                         "game, so the difference from the earlier number is not only this news. "
                                         if remade else "") + all_lines})
        for p, v, ref in jumped:
            up = v["mid"] > ref
            alerts.append({"kind": "price", "game_id": gid, "ids": [p["id"]], "priority": "high", "url": p.get("url"),
                           "title": f"GridSim watch: {p['matchup']} {p['label']} ({p['side'].title()}) "
                                    f"{'up' if up else 'down'} {_c(round(abs(v['mid'] - ref), 4))}",
                           "message": f"Kalshi's price for your side moved {_c(ref)} → {_c(v['mid'])}. "
                                      + _line(p, v, pst[p["id"]])
                                      + ("" if changes else " GridSim's injury data shows no change for this game.")})
        if not changes:                                  # (an injury alert already reports the new numbers)
            for p, v, ref in moved:
                alerts.append({"kind": "sim", "game_id": gid, "ids": [p["id"]], "priority": "default", "url": p.get("url"),
                               "title": f"GridSim watch: {p['matchup']} {p['label']} ({p['side'].title()}) — "
                                        f"GridSim now {_pct(v['p_side'])} (was {_pct(ref)})",
                               "message": ("GridSim re-simulated this game with the latest data and its chance for "
                                           "your side moved by more than its margin of error. "
                                           if pst[p["id"]].get("ref_hash") == made_with else
                                           "GridSim's settings or model changed since its last number for this "
                                           "game, so the difference may come from that and not from new data. ")
                                          + _line(p, v, pst[p["id"]])
                                          + (f" That is now below the {_c(p['price'])} you paid."
                                             if v["p_side"] < p["price"] <= ref else "")})
        # after reporting, the new numbers become the reference for the next alert
        gst[gid] = new_gst
        for p, v, _ in jumped:
            pst[p["id"]]["ref_mid"] = v["mid"]
        for p in plist:
            v, s = views[p["id"]], pst[p["id"]]
            # a number made with another model / other settings is a new starting point, alert or not
            if v["p_side"] is not None and (changes or any(p is q for q, _, _ in moved)
                                            or s.get("ref_hash") != made_with):
                s["ref_p"], s["ref_n"], s["ref_hash"] = v["p_side"], v.get("n_sims"), made_with
            # what the app shows: keep the last numbers when Kalshi did not answer; when it did, the price, the
            # selling price and the sale value belong together (no bid on your side = no selling price right now)
            if v["mid"] is not None:
                s["mid"], s["sell"], s["if_sold_now"] = v["mid"], v["sell"], v["if_sold_now"]
                s["ts"] = now.isoformat(timespec="seconds")
            if v["p_side"] is not None:
                s["p_side"] = v["p_side"]
    if looked or not near_only or not state.get("checked_utc"):
        state["checked_utc"] = now.isoformat(timespec="seconds")
    found = {gid: {"matchup": by_game[gid][0]["matchup"], "what": what} for gid, what in problems.items()}
    state["problems"] = {**carried, **found}
    LAST_PROBLEMS.update(found)

    # Deliver first, remember second: if an alert can neither be written to the log nor pushed (alerts.csv open in
    # Excel and no phone push), nothing is marked as reported, and the next check raises it again.
    delivered = not alerts
    if alerts:
        try:
            _log_alerts(cfg, alerts, now)
            delivered = True
        except Exception as e:
            print(f"  watch: could not write alerts.csv ({type(e).__name__}: {e})")
    if verbose:
        for a in alerts:
            print(f"  [watch:{a['kind']}] {a['title']} — {a['message']}")
    if notify_on and alerts:
        from .notify import send
        for a in alerts:
            try:
                if send(cfg, a["title"], a["message"], url=a.get("url"), priority=a.get("priority", "default")):
                    delivered = True
            except Exception as e:  # a failed push must never lose the alert (it is in the log)
                print(f"  watch: notify failed ({type(e).__name__}: {e})")
    if not delivered:
        LAST_PROBLEMS["_alerts"] = {"matchup": "Alerts", "what": (
            f"{len(alerts)} new alert(s) could not be saved — if positions/alerts.csv is open in another program "
            "(Excel, for example), close it and check again: " + " | ".join(a["title"] for a in alerts))}
        print("  watch: the alerts above could not be saved or sent; they will be raised again at the next check")
        return []
    try:
        _write_json(st_p, state)
        if updates:
            def fn(ps):
                for p in ps:
                    if p["id"] in updates and p.get("status") == "open":
                        p.update(updates[p["id"]])
            _update(cfg, fn)
    except OSError as e:       # reported, not a crash; the same alerts may be raised once more at the next check
        LAST_PROBLEMS["_state"] = {"matchup": "Saving", "what": f"the watch could not save its notes "
                                                               f"({type(e).__name__}: {str(e)[:100]})"}
        print(f"  watch: could not save the watch state ({type(e).__name__}: {e})")
    if any(a["kind"] == "settled" for a in alerts):
        from .userdata import auto_backup
        auto_backup(cfg, "position", min_interval_h=1)
    return alerts


def _log_alerts(cfg: Config, alerts: list[dict], now: dt.datetime) -> None:
    if not alerts:
        return
    from .userdata import file_lock, healed
    p = _dir(cfg) / "alerts.csv"
    with file_lock(p), healed(p), open(p, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=ALERT_FIELDS)
        if fh.tell() == 0:
            w.writeheader()
        for a in alerts:
            w.writerow({"ts_utc": now.isoformat(timespec="seconds"), "game_id": a["game_id"],
                        "position_ids": " ".join(a["ids"]), "kind": a["kind"], "title": a["title"],
                        "message": a["message"], "url": a.get("url") or ""})


def recent_alerts(cfg: Config, n: int = 50) -> list[dict]:
    p = _dir(cfg) / "alerts.csv"
    if not p.exists():
        return []
    with open(p, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return rows[-n:][::-1]


def last_view(cfg: Config) -> dict:
    """{position id: last numbers seen by the watch}, when it last ran, and what it could not read — for the app."""
    st = _read_json(_dir(cfg) / "watch_state.json", {})
    held = {p["game_id"] for p in load_positions(cfg) if p.get("status") == "open"}
    return {"positions": st.get("positions", {}), "checked_utc": st.get("checked_utc"),
            "problems": {g: v for g, v in (st.get("problems") or {}).items() if g in held}}
