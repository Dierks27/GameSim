"""Summaries of simulation results: one `summarize` dict feeds console, JSON and HTML output.

Conventions
  spread  home-team perspective, sportsbook sign: negative = home favored. Home covers when
          (home - away) + spread > 0; push when == 0. (nflverse `spread_line` is the opposite
          sign: positive = home favored, so spread = -spread_line.)
  MOE     95% Monte Carlo margin of error of a simulated probability: 1.96*sqrt(p(1-p)/n).
"""
from __future__ import annotations

import json
import math
from collections import Counter

import numpy as np

from .engine import S, SimResults

PCTS = [10, 25, 50, 75, 90]


def moe(p: float, n: int) -> float:
    return 1.96 * math.sqrt(max(p * (1 - p), 0.0) / max(n, 1))


def prob(x: np.ndarray) -> float:
    return float(np.mean(x)) if len(x) else 0.0


def american(p: float) -> str:
    if p <= 0 or p >= 1:
        return "n/a"
    return f"{-100 * p / (1 - p):+.0f}" if p >= 0.5 else f"+{100 * (1 - p) / p:.0f}"


def _pctl(x: np.ndarray) -> dict:
    q = np.percentile(x, PCTS)
    return {"mean": float(np.mean(x)), "sd": float(np.std(x)), **{f"p{p}": float(v) for p, v in zip(PCTS, q)}}


def _pm(p: float, n: int) -> dict:
    m = moe(p, n)
    return {"p": p, "moe": m, "close": abs(p - 0.5) <= m}


def line_table(values: np.ndarray, lines: list[float], kind: str, n: int) -> list[dict]:
    out = []
    for L in lines:
        if kind == "spread":   # home perspective
            v = values + L
        else:                  # total: over
            v = values - L
        win, push = prob(v > 0), prob(v == 0)
        out.append({"line": L, "hit": win, "push": push, "miss": 1 - win - push, "moe": moe(win, n)})
    return out


def team_stat_block(res: SimResults, team: int) -> dict:
    st = res.stats[:, team]
    c = lambda k: st[:, S[k]]  # noqa: E731
    pass_net = c("pass_yds_gross") - c("sack_yds")
    third = np.divide(c("third_conv"), c("third_att"), out=np.zeros_like(c("third_att")), where=c("third_att") > 0)
    stats = {
        "total_yds": pass_net + c("rush_yds"), "pass_yds": pass_net, "rush_yds": c("rush_yds"),
        "turnovers": c("ints") + c("fum_lost"), "sacks_taken": c("sacks"),
        "plays": c("pass_att") + c("sacks") + c("rush_att"), "top_min": c("top") / 60.0,
        "third_down_pct": 100 * third, "first_downs": c("first_downs"), "punts": c("punts"),
        "fg_made": c("fgm"), "tds": c("tds"), "penalties": c("penalties"),
    }
    return {k: _pctl(v) for k, v in stats.items()}


def summarize(res: SimResults, spread: float | None = None, total: float | None = None,
              model_meta: dict | None = None, cfg_hash: str | None = None, extra: dict | None = None) -> dict:
    n = res.n
    h, a = res.col("pts", 0), res.col("pts", 1)
    margin, tot = h - a, h + a
    hw, aw, tie = prob(margin > 0), prob(margin < 0), prob(margin == 0)
    h1 = res.col("q1", 0) + res.col("q2", 0)
    a1 = res.col("q1", 1) + res.col("q2", 1)
    m1 = h1 - a1
    med_margin = float(np.median(margin))
    out = {
        "matchup": {"home": res.home, "away": res.away, "neutral": res.meta.get("neutral"),
                    "playoff": res.meta.get("playoff")},
        "n_sims": n,
        "win": {"home": _pm(hw, n), "away": _pm(aw, n), "tie": _pm(tie, n)},
        "fair": {
            "home_ml": american(hw / (hw + aw) if hw + aw else 0.5), "away_ml": american(aw / (hw + aw) if hw + aw else 0.5),
            "spread_home": -med_margin, "spread_home_mean": -float(np.mean(margin)),
            "total": float(np.median(tot)), "total_mean": float(np.mean(tot)),
        },
        "scores": {"home": _pctl(h), "away": _pctl(a), "margin": _pctl(margin), "total": _pctl(tot)},
        "quarters": {
            "home": {q: float(res.col(q, 0).mean()) for q in ("q1", "q2", "q3", "q4", "ot")},
            "away": {q: float(res.col(q, 1).mean()) for q in ("q1", "q2", "q3", "q4", "ot")},
        },
        "first_half": {"home_pts": float(h1.mean()), "away_pts": float(a1.mean()),
                       "home_win": _pm(prob(m1 > 0), n), "away_win": _pm(prob(m1 < 0), n), "tie": _pm(prob(m1 == 0), n)},
        "team_stats": {"home": team_stat_block(res, 0), "away": team_stat_block(res, 1)},
        "top_scores": [{"home": int(k[0]), "away": int(k[1]), "p": v / n}
                       for k, v in Counter(zip(h.astype(int), a.astype(int))).most_common(10)],
        "hist": {"margin": _hist(margin), "total": _hist(tot)},
        "meta": {**res.meta, **(model_meta or {}), "config_hash": cfg_hash},
    }
    if spread is not None:
        cov, push = prob(margin + spread > 0), prob(margin + spread == 0)
        half = round(spread / 2 * 2) / 2
        out["spread"] = {
            "line": spread, "home_cover": _pm(cov, n), "away_cover": _pm(1 - cov - push, n), "push": push,
            "first_half_line": half, "first_half_home_cover": _pm(prob(m1 + half > 0), n),
        }
    if total is not None:
        ov, push = prob(tot > total), prob(tot == total)
        out["total"] = {"line": total, "over": _pm(ov, n), "under": _pm(1 - ov - push, n), "push": push}
    out["alt_spreads"] = line_table(margin, [x / 2 for x in range(-28, 29)], "spread", n)
    center = round(float(np.median(tot)) * 2) / 2
    out["alt_totals"] = line_table(tot, [center + x / 2 for x in range(-20, 21)], "total", n)
    if res.players:
        out["players"] = res.players
    if extra:
        out.update(extra)
    return out


def _hist(x: np.ndarray) -> dict:
    lo, hi = int(np.floor(x.min())), int(np.ceil(x.max()))
    vals, counts = np.unique(x.astype(int), return_counts=True)
    return {"values": vals.tolist(), "counts": counts.tolist(), "min": lo, "max": hi}


def _fmt_pm(d: dict) -> str:
    flag = "  (too close to call at this sim count)" if d.get("close") else ""
    return f"{100 * d['p']:5.1f}% ± {100 * d['moe']:.1f}{flag}"


def console(summary: dict) -> str:
    s = summary
    H, A = s["matchup"]["home"], s["matchup"]["away"]
    L = []
    site = " (neutral site)" if s["matchup"].get("neutral") else ""
    L.append(f"=== {A} @ {H}{site} — {s['n_sims']:,} sims ===")
    for b in s.get("banners", []):
        L.append(f"! {b}")
    w = s["win"]
    L.append(f"Win %:   {H} {_fmt_pm(w['home'])}   {A} {_fmt_pm(w['away'])}   tie {100 * w['tie']['p']:.1f}%")
    f = s["fair"]
    L.append(f"Fair ML: {H} {f['home_ml']}  {A} {f['away_ml']}    Fair spread ({H}): {f['spread_home']:+.1f} "
             f"(mean margin {-f['spread_home_mean']:+.1f})    Fair total: {f['total']:.1f} (mean {f['total_mean']:.1f})")
    L.append("")
    L.append(f"{'Score':<8}{'mean':>7}{'p10':>6}{'p25':>6}{'p50':>6}{'p75':>6}{'p90':>6}")
    for k, name in (("home", H), ("away", A), ("margin", "margin"), ("total", "total")):
        d = s["scores"][k]
        L.append(f"{name:<8}{d['mean']:>7.1f}{d['p10']:>6.0f}{d['p25']:>6.0f}{d['p50']:>6.0f}{d['p75']:>6.0f}{d['p90']:>6.0f}")
    if "spread" in s:
        sp = s["spread"]
        L.append(f"\nSpread {H} {sp['line']:+.1f}: {H} covers {_fmt_pm(sp['home_cover'])} | {A} covers "
                 f"{_fmt_pm(sp['away_cover'])} | push {100 * sp['push']:.1f}%")
        L.append(f"1H line {H} {sp['first_half_line']:+.1f}: {H} covers {_fmt_pm(sp['first_half_home_cover'])}")
    if "total" in s:
        t = s["total"]
        L.append(f"Total {t['line']:.1f}: over {_fmt_pm(t['over'])} | under {_fmt_pm(t['under'])} | push {100 * t['push']:.1f}%")
    q = s["quarters"]
    L.append(f"\nAvg points by quarter  Q1    Q2    Q3    Q4    OT")
    for k, name in (("home", H), ("away", A)):
        L.append(f"  {name:<20}" + "".join(f"{q[k][x]:6.1f}" for x in ("q1", "q2", "q3", "q4", "ot")))
    fh = s["first_half"]
    L.append(f"First half: {H} {fh['home_pts']:.1f} - {A} {fh['away_pts']:.1f}; {H} leads {100 * fh['home_win']['p']:.1f}%, "
             f"{A} leads {100 * fh['away_win']['p']:.1f}%, tied {100 * fh['tie']['p']:.1f}%")
    L.append(f"\n{'Team stats (p10 / median / p90)':<34}{H:>20}{A:>20}")
    for k in ("total_yds", "pass_yds", "rush_yds", "turnovers", "sacks_taken", "plays", "top_min", "third_down_pct"):
        hd, ad = s["team_stats"]["home"][k], s["team_stats"]["away"][k]
        fmt = lambda d: f"{d['p10']:.0f} / {d['p50']:.0f} / {d['p90']:.0f}"  # noqa: E731
        L.append(f"  {k:<32}{fmt(hd):>20}{fmt(ad):>20}")
    L.append("\nMost common final scores: " + ", ".join(f"{H} {t['home']}-{t['away']} {100 * t['p']:.1f}%" for t in s["top_scores"]))
    if s.get("availability"):
        L.append(availability_console(s["availability"], s["meta"].get("qb_starts", {})))
    if s.get("players"):
        L.append(players_console(s["players"]))
    if s.get("props"):
        L.append("\nProps:")
        for p in s["props"]:
            L.append(f"  {p['player']} {p['stat']} {p['line']}: over {_fmt_pm(p['over'])} under {100 * p['under']:.1f}%")
    for c in s.get("combos", []):
        L.append(f"\nCombo: {c['combo']}")
        for leg in c["legs"]:
            L.append(f"  {leg['leg']:<40} {100 * leg['p']:5.1f}%")
        lift = c.get("correlation_lift")
        L.append(f"  joint (from sims) {100 * c['joint']['p']:.1f}% ± {100 * c['joint']['moe']:.1f}  vs naive product "
                 f"{100 * c['naive']:.1f}%  (lift x{lift:.2f})  fair {c['fair_american']}" if lift else "  joint 0%")
    m = s["meta"]
    L.append(f"\nData: seasons {m.get('window_seasons')} weights {m.get('season_weights')} through week "
             f"{m.get('through_week') or m.get('last_week_in_data')} | seed {m.get('seed')} | runtime {m.get('runtime_seconds')}s "
             f"| config {m.get('config_hash')}")
    return "\n".join(L)


def availability_console(av: dict, qb_starts: dict) -> str:
    L = [f"\nAvailability (week {av.get('week')}):"]
    for team, d in av["teams"].items():
        qs = qb_starts.get(team) or {}
        qtxt = ", ".join(f"{k} {100 * v:.0f}%" for k, v in qs.items()) or ", ".join(f"{q['name']} {100 * q['p_start']:.0f}%" for q in d["qb"][:2])
        L.append(f"  {team} QB starts: {qtxt}")
        lst = [f"{p['name']} ({p['pos']}) {'OUT' if p['p_play'] == 0 else f'{100 * p['p_play']:.0f}% play'}"
               for p in d["listed"] if p["pos"] in ("QB", "RB", "WR", "TE", "K") and p["p_play"] < 1]
        if lst:
            L.append("    " + "; ".join(lst[:12]))
    fr = av.get("freshness", {})
    if fr:
        def _f(v):
            bits = [f"downloaded {v.get('downloaded') or '?'}"]
            if v.get("nflverse_updated"):
                bits.append(f"nflverse {v['nflverse_updated']}")
            for k in ("latest_week_in_data", "latest_snapshot"):
                if v.get(k) is not None:
                    bits.append(f"{k.replace('_', ' ')} {v[k]}")
            return ", ".join(bits)
        L.append("  data: " + " | ".join(f"{k}: {_f(v)}" for k, v in fr.items()))
    return "\n".join(L)


def players_console(players: dict) -> str:
    L = ["\nPlayer projections (median [p10-p90])"]
    for team, plist in players.items():
        L.append(f"  {team}")
        for p in plist:
            parts = []
            for k in ("pass_yds", "pass_td", "ints", "rush_yds", "rec", "rec_yds"):
                if k in p["stats"] and p["stats"][k]["mean"] > 0.05:
                    d = p["stats"][k]
                    parts.append(f"{k} {d['p50']:.0f} [{d['p10']:.0f}-{d['p90']:.0f}]")
            parts.append(f"anytime TD {100 * p['anytime_td']:.0f}%")
            L.append(f"    {p['name']:<22}{p['pos']:<4}" + "  ".join(parts))
    return "\n".join(L)


def to_json(summary: dict, path) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o))
