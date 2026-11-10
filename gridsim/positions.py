"""Position watch (READ-ONLY): you tell GridSim which Kalshi contracts you hold; it watches them and alerts you.

GridSim never places orders, never sees your Kalshi account and never needs its keys: a position is just a note you
enter (contract, side, price paid, how many), kept in positions/positions.json in your data folder.

At every price check (the scheduled scan every 30 minutes, `gridsim watch` every 10 minutes in the last hours before
kickoff, or "Check now" in the app) each open position is compared with the last look:
  price jump     Kalshi's price moved >= watch.price_jump since the last alert (a sudden move usually means news)
  injury change  a listed player's status / the expected starting QB changed for either team (the injury report is
                 re-downloaded every watch.injury_refresh_minutes while positions are open)
  sim move       GridSim's chance for your side moved >= watch.sim_move after re-simulating with the new information
  settled        the game is final: won / lost and the profit or loss after fees
Every alert states the facts (prices, GridSim's number, what selling now would bring); the decision is yours.
Alerts are logged in positions/alerts.csv and pushed through notify (ntfy / Discord / Windows toast).
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
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
    tmp = p.with_name(p.name + ".partial")
    tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, p)


def load_positions(cfg: Config) -> list[dict]:
    return list(_read_json(_dir(cfg) / "positions.json", {}).get("positions", []))


def _save_positions(cfg: Config, positions: list[dict]) -> None:
    _write_json(_dir(cfg) / "positions.json", {"positions": positions})


def _update(cfg: Config, fn) -> list[dict]:
    """Read-modify-write under a lock (the scheduled watch and the app may both be changing positions)."""
    from .userdata import file_lock
    p = _dir(cfg) / "positions.json"
    with file_lock(p):
        positions = load_positions(cfg)
        fn(positions)
        _save_positions(cfg, positions)
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
    """Entered by mistake: remove it entirely."""
    _update(cfg, lambda ps: ps.__setitem__(slice(None), [p for p in ps if p["id"] != pos_id]))
    st_p = _dir(cfg) / "watch_state.json"
    st = _read_json(st_p, {})
    if st.get("positions", {}).pop(pos_id, None) is not None:
        _write_json(st_p, st)


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
    return out


# ---- injuries -----------------------------------------------------------------------------------------------------------

def avail_snapshot(summ: dict, cfg: Config) -> dict:
    """Who is listed (and how likely to play) and who starts at QB, for the two teams — from the sim's own inputs."""
    keep = set(cfg.watch.get("positions") or [])
    out = {}
    for team, t in ((summ.get("availability") or {}).get("teams") or {}).items():
        qb = max(t.get("qb") or [], key=lambda q: q.get("p_start", 0), default=None)
        out[team] = {"qb": qb["name"] if qb else None,
                     "players": {p["name"]: {"pos": p.get("pos"), "p_play": round(float(p.get("p_play", 1.0)), 2),
                                             "status": p.get("status") or ""}
                                 for p in t.get("listed") or [] if not keep or p.get("pos") in keep}}
    return out


def _status_text(e: dict | None) -> str:
    if e is None:
        return "not listed"
    s = (e.get("status") or "").strip()
    if e["p_play"] <= 0:
        return s or "Out"
    return f"{s or 'listed'} ({100 * e['p_play']:.0f}% to play)" if e["p_play"] < 1 else (s or "expected to play")


def avail_changes(old: dict | None, new: dict) -> list[str]:
    """Plain-English differences between two snapshots (nothing on the first look)."""
    if not old:
        return []
    out = []
    for team in sorted(set(old) | set(new)):
        o, n = old.get(team) or {"qb": None, "players": {}}, new.get(team) or {"qb": None, "players": {}}
        if o.get("qb") and n.get("qb") and o["qb"] != n["qb"]:
            out.append(f"{team}: expected starting QB is now {n['qb']} (was {o['qb']})")
        for name in sorted(set(o["players"]) | set(n["players"])):
            a, b = o["players"].get(name), n["players"].get(name)
            pa, pb = (a or {}).get("p_play", 1.0), (b or {}).get("p_play", 1.0)
            if abs(pa - pb) >= 0.15 or (pa > 0) != (pb > 0):
                pos = (b or a).get("pos") or "?"
                out.append(f"{team}: {name} ({pos}) {_status_text(a)} → {_status_text(b)}")
    return out


def refresh_injuries_if_due(cfg: Config, season: int, verbose: bool = False) -> bool:
    """Re-download rosters / depth charts / injury report if the last watch refresh is older than
    watch.injury_refresh_minutes. Cached sims stay valid unless the content really changed (see api.cache_path)."""
    st_p = _dir(cfg) / "watch_state.json"
    st = _read_json(st_p, {})
    last = st.get("injury_checked_utc")
    due = float(cfg.watch.get("injury_refresh_minutes", 45)) * 60
    if last and (_now() - dt.datetime.fromisoformat(last)).total_seconds() < due:
        return False
    try:
        from .availability import SOURCES, _load_source
        for name in SOURCES:
            _load_source(name, season, cfg, force=True, verbose=verbose)
    except Exception as e:  # network trouble: keep watching with what we have
        if verbose:
            print(f"  injury refresh failed: {e}")
    st = _read_json(st_p, {})
    st["injury_checked_utc"] = _now().isoformat(timespec="seconds")
    _write_json(st_p, st)
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
    won = yes if pos["side"] == "YES" else (not yes)     # (a tie: "X wins" is No for both teams)
    value = 1.0 if won else 0.0
    return {"status": "settled", "closed_utc": _now().isoformat(timespec="seconds"), "result": "won" if won else "lost",
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


def watch(cfg: Config, notify_on: bool = False, near_only: bool = False, game_data: dict | None = None,
          verbose: bool = True, refresh_injuries: bool = True) -> list[dict]:
    """Check every open position; return the alerts raised (also logged, and pushed when notify_on).

    near_only: only games kicking off within watch.near_kickoff_hours (the 10-minute task; the 30-minute scan covers
    the rest). game_data: {game_id: (summary, markets)} already fetched by the caller (the scan), to avoid re-doing it."""
    from .userdata import file_lock
    if not any(p.get("status") == "open" for p in load_positions(cfg)):
        return []
    with file_lock(_dir(cfg) / "watch_state.json", timeout=5) as lk:
        if lk.fd is None:               # another watch is running right now: let it finish
            return []
        return _watch(cfg, notify_on, near_only, game_data or {}, verbose, refresh_injuries)


def _watch(cfg, notify_on, near_only, game_data, verbose, refresh_injuries) -> list[dict]:
    from .api import RunRequest, cached_summary, run_game
    from .markets import Kalshi, game_markets
    now = _now()
    positions = [p for p in load_positions(cfg) if p.get("status") == "open"]
    st_p = _dir(cfg) / "watch_state.json"
    state = _read_json(st_p, {})
    pst, gst = state.setdefault("positions", {}), state.setdefault("games", {})
    near_h = float(cfg.watch.get("near_kickoff_hours", 3))
    by_game: dict[str, list[dict]] = {}
    for p in positions:
        by_game.setdefault(p["game_id"], []).append(p)
    alerts, updates = [], {}
    k = None
    refreshed = False
    for gid, plist in by_game.items():
        g = _game(cfg, plist[0])
        if g is None:
            continue
        # -- final: settle
        if g.get("home_score") is not None:
            lines = []
            score = f"{g['away_team']} {g['away_score']} – {g['home_team']} {g['home_score']}"
            for p in plist:
                res = _settle(p, g, cfg)
                if res:
                    updates[p["id"]] = res
                    lines.append(f"{p['label']} ({p['side'].title()}) ×{p['contracts']} {res['result'].upper()}: "
                                 f"{_usd(res['pnl'])} after fees.")
            if lines:
                alerts.append({"kind": "settled", "game_id": gid, "ids": [p["id"] for p in plist], "priority": "default",
                               "title": f"GridSim watch: {plist[0]['matchup']} is final",
                               "message": f"Final score {score}. " + " ".join(lines), "url": plist[0].get("url")})
            continue
        ko = dt.datetime.fromisoformat(plist[0]["kickoff_utc"]) if plist[0].get("kickoff_utc") else None
        if ko and ko <= now:
            continue                                    # in play: nothing to compare the pre-game sim with
        if near_only and (ko is None or (ko - now).total_seconds() > near_h * 3600):
            continue
        # -- fresh inputs: injury report, sim, prices
        if gid in game_data:
            summ, mk = game_data[gid]
        else:
            if refresh_injuries and not refreshed:
                refresh_injuries_if_due(cfg, int(plist[0]["season"]), verbose=False)
                refreshed = True
            req = RunRequest(home=g["home_team"], away=g["away_team"], season=int(plist[0]["season"]),
                             week=int(plist[0]["week"]), market_lines=True)
            summ = cached_summary(req, cfg)
            if summ is None:
                summ = run_game(req, cfg, refresh=True)
                summ["created_utc"] = now.isoformat()
            try:
                k = k or Kalshi(cfg)
                mk = game_markets(k, g)
            except Exception as e:
                if verbose:
                    print(f"  watch {gid}: Kalshi unavailable ({e})")
                mk = None
        snap = avail_snapshot(summ, cfg)
        changes = avail_changes((gst.get(gid) or {}).get("avail"), snap)
        gst[gid] = {"avail": snap, "ts": now.isoformat(timespec="seconds")}
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
                    s["ref_p"] = v["p_side"]
                    if p.get("p_side_entry") is None:
                        updates.setdefault(p["id"], {})["p_side_entry"] = round(v["p_side"], 4)
                elif abs(v["p_side"] - s["ref_p"]) >= sim_t - 1e-9:
                    moved.append((p, v, s["ref_p"]))
        url = plist[0].get("url")
        ids = [p["id"] for p in plist]
        all_lines = " ".join(_line(p, views[p["id"]], pst[p["id"]]) for p in plist)
        if changes:
            alerts.append({"kind": "injury", "game_id": gid, "ids": ids, "priority": "high", "url": url,
                           "title": f"GridSim watch: {plist[0]['matchup']} — lineup news",
                           "message": "; ".join(changes) + ". GridSim re-simulated with it. " + all_lines})
        for p, v, ref in jumped:
            up = v["mid"] > ref
            alerts.append({"kind": "price", "game_id": gid, "ids": [p["id"]], "priority": "high", "url": p.get("url"),
                           "title": f"GridSim watch: {p['matchup']} {p['label']} ({p['side'].title()}) "
                                    f"{'up' if up else 'down'} {abs(100 * (v['mid'] - ref)):.0f}¢",
                           "message": f"Kalshi's price for your side moved {_c(ref)} → {_c(v['mid'])}. "
                                      + _line(p, v, pst[p["id"]])
                                      + ("" if changes else " A sudden move often means news the injury data doesn't "
                                                            "show yet — worth a look.")})
        if not changes:                                  # (an injury alert already reports the new numbers)
            for p, v, ref in moved:
                alerts.append({"kind": "sim", "game_id": gid, "ids": [p["id"]], "priority": "default", "url": p.get("url"),
                               "title": f"GridSim watch: {p['matchup']} {p['label']} ({p['side'].title()}) — "
                                        f"GridSim now {_pct(v['p_side'])} (was {_pct(ref)})",
                               "message": "GridSim's own number changed (new data or lineup). " + _line(p, v, pst[p["id"]])
                                          + (f" That is now below the {_c(p['price'])} you paid."
                                             if v["p_side"] < p["price"] <= ref else "")})
        # after reporting, the new numbers become the reference for the next alert
        for p, v, _ in jumped:
            pst[p["id"]]["ref_mid"] = v["mid"]
        for p in plist:
            v, s = views[p["id"]], pst[p["id"]]
            if v["p_side"] is not None and (changes or any(p is q for q, _, _ in moved)):
                s["ref_p"] = v["p_side"]
            s.update({"mid": v["mid"], "sell": v["sell"], "p_side": v["p_side"], "if_sold_now": v["if_sold_now"],
                      "ts": now.isoformat(timespec="seconds")})
    state["checked_utc"] = now.isoformat(timespec="seconds")
    _write_json(st_p, state)
    if updates:
        def fn(ps):
            for p in ps:
                if p["id"] in updates and p.get("status") == "open":
                    p.update(updates[p["id"]])
        _update(cfg, fn)
    _log_alerts(cfg, alerts, now)
    if verbose:
        for a in alerts:
            print(f"  [watch:{a['kind']}] {a['title']} — {a['message']}")
    if notify_on and alerts:
        from .notify import send
        for a in alerts:
            try:
                send(cfg, a["title"], a["message"], url=a.get("url"), priority=a.get("priority", "default"))
            except Exception as e:  # a failed push must never lose the alert (it is logged above)
                if verbose:
                    print(f"  notify failed: {e}")
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
    """{position id: last numbers seen by the watch} and when it last ran — for the app's table."""
    st = _read_json(_dir(cfg) / "watch_state.json", {})
    return {"positions": st.get("positions", {}), "checked_utc": st.get("checked_utc")}
