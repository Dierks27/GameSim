"""Kalshi opportunity scanner, signal ledger and signal grading. READ-ONLY: it alerts, it never trades.

A signal fires only when ALL pass (thresholds in config `scanner`):
  1. edge after fees  = sim prob - (ask you'd pay + Kalshi fee) >= min_edge AND > moe_multiple x sim MOE
     (if it clears min_edge but not the MOE test, the game is re-simmed with more sims first)
  2. liquidity: size at the quoted price >= min_depth_dollars, bid/ask spread <= max_spread, volume >= min_volume
  3. fresh inputs: the sim is younger than max_sim_age_hours (else re-sim)
  4. no unresolved key Questionable player: the edge must hold with him playing AND sitting
  5. earned trust: the market type (moneyline / spread / total) has >= trust_min_graded graded signals with
     positive average CLV; until then it is listed as "watchlist, not trusted yet"
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


def trust_table(cfg: Config) -> dict:
    """Per market type: graded count, average CLV, flat-unit P&L -> trusted?"""
    p = _dir(cfg) / "graded.csv"
    out = {}
    if not p.exists():
        return out
    df = pl.read_csv(p, infer_schema_length=None)
    for mt in df["market"].unique().to_list():
        x = df.filter((pl.col("market") == mt) & pl.col("clv").is_not_null())
        n = x.height
        clv = float(x["clv"].mean()) if n else 0.0
        pnl = float(x["pnl"].sum()) if n else 0.0
        trusted = n >= int(cfg.scanner["trust_min_graded"]) and clv > 0 and pnl >= 0
        out[mt] = {"n": n, "avg_clv": clv, "pnl": pnl, "trusted": trusted}
    return out


def _depth(row: dict) -> float | None:
    # size at the YES ask; for NO, the NO ask is the other side of the best YES bid
    size = row.get("yes_ask_size") if row.get("side") == "YES" else row.get("yes_bid_size")
    return size * row["price"] if (size is not None and row.get("price")) else None


def evaluate(cfg: Config, summ: dict, rows: list[dict], trust: dict, sim_age_h: float, q_both: dict | None) -> list[dict]:
    sc = cfg.scanner
    out = []
    for r in rows:
        if r.get("edge") is None:
            continue
        reasons, fails = [], []
        e, moe = r["edge"], r["moe"]
        if e < float(sc["min_edge"]):
            continue
        if e <= float(sc["moe_multiple"]) * moe:
            fails.append(f"edge {100 * e:.1f} within {sc['moe_multiple']}x MOE {100 * moe:.1f}")
        depth = _depth(r)
        if depth is not None and depth < float(sc["min_depth_dollars"]):
            fails.append(f"depth ${depth:,.0f}")
        if r.get("spread") is not None and r["spread"] > float(sc["max_spread"]):
            fails.append(f"spread {100 * r['spread']:.0f}c")
        if (r.get("volume") or 0) < float(sc["min_volume"]):
            fails.append(f"volume {r.get('volume') or 0:,.0f}")
        if sim_age_h > float(sc["max_sim_age_hours"]):
            fails.append(f"sim {sim_age_h:.1f} h old")
        if q_both is not None:
            alt = q_both.get((r["ticker"], r["side"]))
            if alt is not None and alt < float(sc["min_edge"]):
                fails.append(f"edge only {100 * alt:.1f} if the questionable player flips")
        tr = trust.get(r["market"], {"n": 0, "avg_clv": 0.0, "trusted": False})
        if not tr["trusted"]:
            fails.append(f"{r['market']} not trusted yet ({tr['n']} graded, CLV {tr['avg_clv']:+.3f})")
        hard = [f for f in fails if "not trusted" not in f]
        if hard:
            conf = "watchlist"
        elif not tr["trusted"]:
            conf = "watchlist"
        else:
            conf = "strong" if e >= float(sc["strong_edge"]) else "moderate"
        cost = r["price"] + r["fee"]
        reasons.append(f"sim {100 * r['p_side']:.0f}% vs {r['side']} ask {100 * r['price']:.0f}c + {100 * r['fee']:.1f}c fee "
                       f"-> edge {100 * e:+.1f} pts, EV ${e / cost:+.2f}/$1")
        if depth is not None:
            reasons.append(f"${depth:,.0f} at the ask")
        reasons.append(f"{r['market']} record: {tr['n']} graded, CLV {tr['avg_clv']:+.3f}")
        out.append({**r, "depth_dollars": depth, "confidence": conf, "reason": "; ".join(reasons + fails)})
    out.sort(key=lambda s: -s["edge"])
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
            summ = run_game(req, cfg, refresh=True)
            summ["created_utc"] = now.isoformat()
            age = 0.0
        try:
            mk = game_markets(k, g)
        except Exception as e:
            if verbose:
                print(f"  {g['game_id']}: Kalshi unavailable ({e})")
            continue
        rows = edges(sim_probs(summ, mk), cfg)
        # MOE test: re-sim with more sims if an edge is big enough but within the noise
        need = [r for r in rows if r.get("edge") is not None and r["edge"] >= float(cfg.scanner["min_edge"])
                and r["edge"] <= float(cfg.scanner["moe_multiple"]) * r["moe"]]
        if need and summ["n_sims"] < int(cfg.scanner["max_resim_sims"]):
            req.sims = min(int(cfg.scanner["max_resim_sims"]), summ["n_sims"] * 4)
            summ = run_game(req, cfg, refresh=True)
            summ["created_utc"] = now.isoformat()
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
                    alt = run_game(alt_req, cfg, use_cache=False)
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
    # ledger + re-alert logic
    led = _dir(cfg) / "ledger.csv"
    new = not led.exists()
    alerts = []
    with open(led, "a", newline="", encoding="utf-8") as fh:
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


def _closing_price(cfg: Config, game_id: str, ticker: str, side: str, kickoff: str | None) -> float | None:
    best = None
    for p in sorted((_dir(cfg) / "snapshots").glob("*.json")):
        snap = json.loads(p.read_text())
        if kickoff and snap["ts_utc"] > kickoff:
            continue
        mk = snap["games"].get(game_id) or {}
        for row in list((mk.get("moneyline") or {}).values()) + (mk.get("spread") or []) + (mk.get("total") or []):
            if row.get("ticker") == ticker and row.get("yes_bid") is not None and row.get("yes_ask") is not None:
                mid = (row["yes_bid"] + row["yes_ask"]) / 2
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
    for r in df.iter_rows(named=True):
        key = (r["ts_utc"], r["ticker"], r["side"])
        g = res.get(r["game_id"])
        if g is None or key in done:
            continue
        margin, total = g["home_score"] - g["away_score"], g["home_score"] + g["away_score"]
        yes = _resolve(r, g, margin, total)
        if yes is None:
            continue
        win = yes if r["side"] == "YES" else (not yes)
        cost = float(r["price"]) + float(r["fee"])
        pnl = (1.0 / cost - 1.0) if win else -1.0
        close = _closing_price(cfg, r["game_id"], r["ticker"], r["side"], r.get("kickoff_utc"))
        rows.append({**r, "won": win, "pnl": pnl, "closing": close, "clv": (close - float(r["price"])) if close is not None else None})
    if rows:
        new = not gp.exists()
        with open(gp, "a", newline="", encoding="utf-8") as fh:
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


def signals_report(cfg: Config, season: int | None = None) -> str:
    gp = _dir(cfg) / "graded.csv"
    if not gp.exists():
        return "no graded signals yet"
    df = pl.read_csv(gp, infer_schema_length=None)
    if season:
        df = df.filter(pl.col("season") == season)
    L = [f"{'market':<10}{'confidence':<11}{'n':>5}{'hit%':>7}{'avg CLV':>9}{'P&L (units)':>13}"]
    for (mt, conf), x in sorted(df.group_by(["market", "confidence"]), key=lambda kv: kv[0]):
        clv = x["clv"].drop_nulls()
        L.append(f"{mt:<10}{conf:<11}{x.height:>5}{100 * x['won'].cast(pl.Float64).mean():>7.1f}"
                 f"{(clv.mean() if clv.len() else float('nan')):>9.3f}{x['pnl'].sum():>13.2f}")
    tr = trust_table(cfg)
    L.append("trust: " + ", ".join(f"{k}: {'trusted' if v['trusted'] else 'watchlist'} (n={v['n']}, CLV {v['avg_clv']:+.3f})"
                                   for k, v in tr.items()))
    return "\n".join(L)
