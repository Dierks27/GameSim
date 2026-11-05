"""Slate mode: sim every game of a week (multiprocessing) and write one combined report, sorted by the biggest
sim-vs-market disagreement."""
from __future__ import annotations

import html
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from .api import RunRequest, run_game, week_slate
from .config import Config


def _one(job: dict) -> dict:
    cfg = Config(job["cfg_raw"])
    req = RunRequest(**job["req"])
    try:
        return run_game(req, cfg, use_cache=True)
    except Exception as e:  # keep the slate going
        return {"error": str(e), "request": job["req"]}


def run_slate(cfg: Config, season: int | None = None, week: int | None = None, sims: int | None = None,
              workers: int | None = None, verbose: bool = True) -> list[dict]:
    import multiprocessing as mp
    season, week, games = week_slate(cfg, season, week)
    jobs = [{"cfg_raw": cfg.raw, "req": RunRequest(home=g["home_team"], away=g["away_team"], season=season, week=week,
                                                    sims=sims, market_lines=True).__dict__}
            for g in games if g.get("home_score") is None]
    workers = workers or max(1, min(len(jobs), os.cpu_count() or 2))
    if workers > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn")) as ex:
            out = list(ex.map(_one, jobs))
    else:
        out = [_one(j) for j in jobs]
    for s in out:
        s["gap"] = _gap(s)
    out.sort(key=lambda s: -(s.get("gap") or {}).get("abs", 0.0))
    if verbose:
        for s in out:
            if s.get("error"):
                print(f"  error: {s['error']}")
                continue
            g = s["gap"]
            print(f"  {s['matchup']['away']:>3} @ {s['matchup']['home']:<3} home win {100 * s['win']['home']['p']:5.1f}%  "
                  f"fair spread {s['fair']['spread_home']:+5.1f} (mkt {g.get('mkt_spread', float('nan')):+5.1f})  "
                  f"fair total {s['fair']['total']:5.1f} (mkt {g.get('mkt_total', float('nan')):5.1f})  "
                  f"gap {g.get('abs', 0):.1f}")
    return out


def _gap(s: dict) -> dict:
    if s.get("error") or not s.get("market"):
        return {}
    m = s["market"]
    out = {"mkt_spread": m["spread_home"], "mkt_total": m.get("total")}
    ds = s["fair"]["spread_home_mean"] - m["spread_home"]
    dt_ = (s["fair"]["total_mean"] - m["total"]) if m.get("total") is not None else 0.0
    out.update({"spread": ds, "total": dt_, "abs": abs(ds) + 0.5 * abs(dt_)})
    return out


def write_slate_html(results: list[dict], path: Path, title: str) -> Path:
    rows = []
    for s in results:
        if s.get("error"):
            continue
        H, A = s["matchup"]["home"], s["matchup"]["away"]
        g = s.get("gap") or {}
        w = s["win"]
        rows.append(
            f"<tr><td>{html.escape(A)} @ {html.escape(H)}</td><td>{(s.get('game') or {}).get('gameday', '')}</td>"
            f"<td>{100 * w['home']['p']:.1f}% ± {100 * w['home']['moe']:.1f}</td>"
            f"<td>{s['fair']['spread_home']:+.1f}</td><td>{g.get('mkt_spread', '')}</td>"
            f"<td>{s['fair']['total']:.1f}</td><td>{g.get('mkt_total', '')}</td>"
            f"<td>{g.get('spread', 0):+.1f}</td><td>{g.get('total', 0):+.1f}</td></tr>")
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{background:#0f1115;color:#e6e6e6;font-family:Segoe UI,Arial,sans-serif;margin:16px;max-width:1100px}}
table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{border-bottom:1px solid #2a2f3a;padding:6px 8px;text-align:left}}
th{{color:#9aa4b2;font-weight:600}}</style></head><body><h1>{html.escape(title)}</h1>
<p>Sorted by the biggest sim-vs-market disagreement. Spreads are home perspective (negative = home favored).</p>
<table><thead><tr><th>Game</th><th>Date</th><th>Home win</th><th>Fair spread</th><th>Market</th><th>Fair total</th>
<th>Market</th><th>Spread gap</th><th>Total gap</th></tr></thead><tbody>{''.join(rows)}</tbody></table></body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc, encoding="utf-8")
    return path
