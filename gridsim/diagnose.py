"""Diagnose: where is the sim wrong? Reads backtest output (games + components) and reports:

1. Component scorecard: aggregate sim vs actual for every component (ratio of sums), bias, bootstrap SE
   over team-games, flagged when |bias| > 2 SE, with the sub-model each one points to.
2. Residual breakdown: margin and total errors grouped by favorite side, spread size, roof, divisional,
   early season, QB change, primetime, short rest; flagged when the group mean error is > 2 SE from 0.
3. Calibration of win % and 80%-interval coverage of points / margin / total.
4. The 15 worst misses with component detail.
5. A plain-English "top 3 most likely wrong" summary.
The diagnosis never changes code or config; it tells you where to look.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import polars as pl

from .backtest import COMPONENTS
from .metrics import calibration_table, summarize_games

HINTS = {
    "points": "overall scoring: drive count (clock: runoff_scale, team pace) and per-drive efficiency (outcome pools)",
    "pts_q1": "early-game scoring: outcome pools / opening-script tendencies",
    "pts_q2": "end of first half management: hurry_q2_seconds, q2_kneel_seconds, end-of-half FG logic",
    "pts_q3": "second-half opening: outcome pools / halftime kickoff",
    "pts_q4": "late-game management: garbage-time filter, milk/hurry tempo, defensive timeouts, onside logic",
    "plays": "clock model: runoff_scale, team pace (k_pace), incompletion/out-of-bounds stop rules",
    "pass_rate": "play-call model: league P(pass) table (smoothing_m) and team PROE shrinkage (k_team_pass)",
    "ypa": "pass outcome pools: matchup blend w_off, team shrinkage k_outcome, QB pool k_qb",
    "ypc": "run outcome pools: w_off, k_outcome, team back-off levels",
    "sack_rate": "dropback pools: QB pool (k_qb, backup_max_dropbacks) and defense side weight (w_off)",
    "int_rate": "dropback pools: QB pool (k_qb) and k_outcome",
    "fumbles_lost": "outcome pools (turnover rows) / k_outcome",
    "third_down_pct": "3rd-down team buckets (TEAM_LEVELS downgrp3 x distgrp3) and min_bucket_n",
    "fourth_down_att": "4th-down go model: league go table, k_team_4th, forced late-game logic",
    "rz_trips": "field position / drive efficiency between the 20s (outcome pools, kickoff/punt starts)",
    "rz_td_pct": "red-zone and goal-to-go outcome pools, TD censoring (Kaplan-Meier per zone), team rz split",
    "fg_att": "4th-down FG decision (fg_by_yl, kicker range shift, fg_min_make_prob) and end-of-half FG logic",
    "fg_pct": "FG logistic and kicker offsets (k_kicker)",
    "punts": "4th-down punt decision and drive outcomes",
    "kickoff_start_yl": "kickoff era pool / touchback_yardline / k_kickoff_team",
    "punt_start_yl": "punt pool by line of scrimmage / k_punter",
    "penalties": "pre-snap foul rates (k_presnap) and post-snap no-play rows",
    "top_min": "clock model: runoff by tempo, team pace",
    "turnovers": "outcome pools: turnover rows, QB pool",
}


def component_scorecard(comp: pl.DataFrame, n_boot: int = 300, seed: int = 0) -> list[dict]:
    rng = np.random.default_rng(seed)
    n = comp.height
    idx = [rng.integers(0, n, n) for _ in range(n_boot)] if n else []
    out = []
    for name, (num, den) in COMPONENTS.items():
        sn, an = comp[f"sim_{num}"].to_numpy(), comp[f"act_{num}"].to_numpy()
        sd = comp[f"sim_{den}"].to_numpy() if den else np.ones(n)
        ad = comp[f"act_{den}"].to_numpy() if den else np.ones(n)

        def stat(ii):
            s = sn[ii].sum() / max(sd[ii].sum(), 1e-9)
            a = an[ii].sum() / max(ad[ii].sum(), 1e-9)
            return s, a
        s, a = stat(np.arange(n))
        boots = np.array([stat(ii)[0] - stat(ii)[1] for ii in idx]) if idx else np.array([0.0])
        se = float(np.std(boots)) or 1e-9
        bias = s - a
        out.append({"component": name, "sim": float(s), "actual": float(a), "bias": float(bias), "se": se,
                    "t": float(bias / se), "rel_bias": float(bias / a) if a else 0.0, "flag": abs(bias) > 2 * se,
                    "points_to": HINTS.get(name, "")})
    out.sort(key=lambda r: -abs(r["t"]))
    return out


def _groups(g: pl.DataFrame) -> dict[str, pl.Expr]:
    mkt = pl.col("mkt_margin")
    rest = pl.min_horizontal(pl.col("home_rest").fill_null(7), pl.col("away_rest").fill_null(7))
    prime = (pl.col("weekday").fill_null("Sunday") != "Sunday") | (pl.col("gametime").fill_null("13:00") >= "19:00")
    return {
        "home favorite": mkt > 0, "away favorite": mkt < 0,
        "spread < 3": mkt.abs() < 3, "spread 3-7": (mkt.abs() >= 3) & (mkt.abs() < 7), "spread 7+": mkt.abs() >= 7,
        "dome/closed": pl.col("roof").is_in(["dome", "closed"]), "outdoors/open": pl.col("roof").is_in(["outdoors", "open"]),
        "divisional": pl.col("div_game") == 1, "non-divisional": pl.col("div_game") == 0,
        "weeks 1-4": pl.col("week") <= 4, "weeks 5+": pl.col("week") >= 5,
        "QB change (either team)": pl.col("home_qb_change").fill_null(False) | pl.col("away_qb_change").fill_null(False),
        "primetime": prime, "short rest (<6 days)": rest < 6, "neutral site": pl.col("location") == "Neutral",
    }


def residual_breakdown(g: pl.DataFrame) -> list[dict]:
    g = g.with_columns(err_margin=pl.col("pred_margin") - pl.col("margin"), err_total=pl.col("pred_total") - pl.col("total"),
                       err_fav=(pl.col("pred_margin") - pl.col("margin")) * pl.when(pl.col("mkt_margin") >= 0).then(1).otherwise(-1))
    out = []
    for name, cond in _groups(g).items():
        sub = g.filter(cond.fill_null(False))
        if sub.height < 8:
            continue
        for col, label in (("err_margin", "margin (home) error"), ("err_total", "total error"), ("err_fav", "favorite margin error")):
            x = sub[col].to_numpy()
            se = float(np.std(x, ddof=1) / np.sqrt(len(x)))
            m = float(np.mean(x))
            out.append({"group": name, "metric": label, "n": len(x), "mean_error": m, "se": se, "t": m / se if se else 0.0,
                        "flag": abs(m) > 2 * se})
    out.sort(key=lambda r: -abs(r["t"]))
    return out


def worst_misses(g: pl.DataFrame, comp: pl.DataFrame, k: int = 15) -> list[dict]:
    g = g.with_columns(miss=(pl.col("pred_margin") - pl.col("margin")).abs() + 0.5 * (pl.col("pred_total") - pl.col("total")).abs())
    top = g.sort("miss", descending=True).head(k)
    out = []
    for r in top.iter_rows(named=True):
        detail = {}
        for side in ("home", "away"):
            c = comp.filter((pl.col("game_id") == r["game_id"]) & (pl.col("side") == side))
            if c.height:
                c = c.row(0, named=True)
                detail[r[side]] = {name: {"sim": c[f"sim_{num}"] / (c[f"sim_{den}"] if den else 1) if (not den or c[f"sim_{den}"]) else None,
                                          "actual": c[f"act_{num}"] / (c[f"act_{den}"] if den else 1) if (not den or c[f"act_{den}"]) else None}
                                   for name, (num, den) in COMPONENTS.items()}
        out.append({"game_id": r["game_id"], "score": f"{r['away']} {r['away_score']} @ {r['home']} {r['home_score']}",
                    "pred": f"{r['away']} {r['pred_away']:.1f} @ {r['home']} {r['pred_home']:.1f}",
                    "p_home": r["p_home"], "market_margin": r["mkt_margin"], "miss": r["miss"], "components": detail})
    return out


def key_numbers(g: pl.DataFrame) -> list[dict]:
    """Predicted vs actual frequency of key margins (3, 7) and one-score games."""
    out = []
    m = (g["margin"].abs()).to_numpy()
    for col, name, act in (("p_abs3", "margin exactly 3", m == 3), ("p_abs7", "margin exactly 7", m == 7),
                           ("p_within7", "one-score game (<= 7)", m <= 7)):
        if col not in g.columns:
            continue
        pred = float(g[col].mean())
        a = float(np.mean(act))
        se = float(np.sqrt(max(a * (1 - a), 1e-9) / len(m)))
        out.append({"key": name, "sim": pred, "actual": a, "diff": pred - a, "se": se, "flag": abs(pred - a) > 2 * se})
    return out


def plain_summary(score: list[dict], resid: list[dict], metrics: dict, keys: list[dict] | None = None) -> list[str]:
    items = []
    for k in keys or []:
        if k["flag"]:
            items.append((abs(k["diff"]) / k["se"], f"{k['key']}: sim {100 * k['sim']:.1f}% vs actual {100 * k['actual']:.1f}% "
                                                    f"— the margin distribution's shape (late-game score effects) is off; matters for spreads near 3/7."))
    for c in score:
        if c["flag"]:
            direction = "too high" if c["bias"] > 0 else "too low"
            items.append((abs(c["t"]), f"{c['component']} is {direction} by {abs(c['bias']):.3g} "
                                       f"({100 * c['rel_bias']:+.1f}%, {abs(c['t']):.1f} SE) — look at {c['points_to']}."))
    for r in resid:
        if r["flag"] and r["metric"] != "favorite margin error":
            items.append((abs(r["t"]) * 0.9, f"{r['metric']} is biased {r['mean_error']:+.2f} pts in '{r['group']}' games "
                                             f"(n={r['n']}, {abs(r['t']):.1f} SE) — a missing feature or mis-weighted recency for that group."))
    fav = [r for r in resid if r["metric"] == "favorite margin error" and r["group"] in ("spread 7+", "spread 3-7") and r["flag"]]
    for r in fav:
        word = "underrates" if r["mean_error"] < 0 else "overrates"
        items.append((abs(r["t"]), f"the sim {word} favorites in '{r['group']}' games by {abs(r['mean_error']):.2f} pts "
                                   f"— team-strength shrinkage (k_outcome, w_off, season weights) is likely {'too strong' if word == 'underrates' else 'too weak'}."))
    cm, ct = metrics.get("coverage_80_margin"), metrics.get("coverage_80_total")
    for val, name in ((cm, "margin"), (ct, "total")):
        if val is not None and abs(val - 0.8) > 0.06:
            items.append((abs(val - 0.8) * 40, f"80% {name} intervals cover {100 * val:.0f}% of games — the sim's {name} variance is "
                                               f"{'too narrow' if val < 0.8 else 'too wide'}."))
    if metrics.get("sd_pred_margin") and metrics.get("sd_mkt_margin") and metrics["sd_pred_margin"] < 0.75 * metrics["sd_mkt_margin"]:
        items.append((5.0, f"predicted margins are compressed (sd {metrics['sd_pred_margin']:.1f} vs market {metrics['sd_mkt_margin']:.1f}) "
                           f"— team quality is shrunk too hard toward the league."))
    items.sort(key=lambda x: -x[0])
    return [t for _, t in items[:3]] or ["No component or group is biased beyond simulation/sampling noise."]


def diagnose(games: pl.DataFrame, comp: pl.DataFrame) -> dict:
    metrics = summarize_games(games)
    score = component_scorecard(comp)
    resid = residual_breakdown(games)
    keys = key_numbers(games)
    return {"metrics": metrics, "summary": plain_summary(score, resid, metrics, keys), "components": score,
            "key_numbers": keys,
            "residuals": resid, "worst_misses": worst_misses(games, comp),
            "coverage": {k: metrics.get(k) for k in ("coverage_80_margin", "coverage_80_total", "coverage_80_points")},
            "calibration": calibration_table((games["p_home"] + 0.5 * games["p_tie"]).to_numpy(), games["home_win"].to_numpy())}


def load_backtest_dirs(dirs: list[Path]) -> tuple[pl.DataFrame, pl.DataFrame]:
    gs, cs = [], []
    for d in dirs:
        gs.append(pl.read_csv(d / "games.csv", infer_schema_length=None))
        if (d / "components.csv").exists():
            cs.append(pl.read_csv(d / "components.csv"))
    return pl.concat(gs, how="diagonal_relaxed"), pl.concat(cs, how="diagonal_relaxed")


def write_report(diag: dict, out_dir: Path, title: str) -> Path:
    import plotly.graph_objects as go
    import plotly.io as pio

    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "diagnose.json", "w", encoding="utf-8") as fh:
        json.dump(diag, fh, indent=2, default=str)
    cal = diag["calibration"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[0, 1], y=[0, 1], mode="lines", line=dict(dash="dot", color="#888"), name="perfect"))
    fig.add_trace(go.Scatter(x=[c["pred"] for c in cal], y=[c["actual"] for c in cal], mode="markers+lines",
                             marker=dict(size=[max(6, min(24, c["n"] / 3)) for c in cal], color="#4C9BE8"),
                             text=[f"n={c['n']}" for c in cal], name="sim"))
    fig.update_layout(template="plotly_dark", title="Win % calibration", xaxis_title="predicted home win %",
                      yaxis_title="actual", height=380, margin=dict(l=40, r=20, t=50, b=40))
    comp = diag["components"]
    fig2 = go.Figure(go.Bar(x=[c["t"] for c in comp], y=[c["component"] for c in comp], orientation="h",
                            marker_color=["#E8684C" if c["flag"] else "#4C9BE8" for c in comp]))
    fig2.update_layout(template="plotly_dark", title="Component bias (in standard errors, sim − actual)", height=640,
                       margin=dict(l=140, r=20, t=50, b=40), yaxis=dict(autorange="reversed"))

    def table(rows, cols, fmt):
        h = "".join(f"<th>{c}</th>" for c in cols)
        body = "".join("<tr class='{}'>".format("flag" if r.get("flag") else "") +
                       "".join(f"<td>{fmt(c, r.get(c))}</td>" for c in cols) + "</tr>" for r in rows)
        return f"<table><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table>"

    def f(c, v):
        if isinstance(v, float):
            return f"{v:.3f}" if abs(v) < 10 else f"{v:.1f}"
        return "" if v is None else str(v)

    m = diag["metrics"]
    mk = m.get("market", {})
    head = "".join(f"<tr><td>{k}</td><td>{m[k]:.4f}</td><td>{mk.get(k, float('nan')):.4f}</td></tr>"
                   for k in ("brier", "log_loss", "mae_margin", "mae_total") if k in m)
    extra = "".join(f"<tr><td>{k}</td><td>{m[k]:.4f}</td><td></td></tr>" for k in
                    ("crps_margin", "crps_total", "crps_points", "bias_margin", "bias_total", "corr_margin_vs_spread",
                     "corr_total_vs_line", "coverage_80_margin", "coverage_80_total", "coverage_80_points") if k in m)
    misses = "".join(f"<li><b>{w['score']}</b> — sim {w['pred']} (P(home) {100 * w['p_home']:.0f}%, market home margin "
                     f"{w['market_margin']})</li>" for w in diag["worst_misses"])
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>body{{background:#0f1115;color:#e6e6e6;font-family:Segoe UI,Arial,sans-serif;margin:24px;max-width:1100px}}
h1,h2{{font-weight:600}} table{{border-collapse:collapse;margin:8px 0 20px;font-size:13px}}
td,th{{border-bottom:1px solid #2a2f3a;padding:4px 10px;text-align:left}} tr.flag td{{color:#ffb4a2}}
.sum li{{margin:6px 0;font-size:15px}} .card{{background:#171a21;border-radius:8px;padding:12px 18px;margin:12px 0}}</style></head>
<body><h1>{title}</h1><div class="card"><h2>Top 3 things most likely wrong</h2><ol class="sum">{''.join(f'<li>{s}</li>' for s in diag['summary'])}</ol></div>
<h2>Headline metrics</h2><table><thead><tr><th>metric</th><th>sim</th><th>closing market</th></tr></thead><tbody>{head}{extra}</tbody></table>
{pio.to_html(fig, include_plotlyjs="inline", full_html=False)}
<h2>Component scorecard</h2>{pio.to_html(fig2, include_plotlyjs=False, full_html=False)}
{table(comp, ["component", "sim", "actual", "bias", "se", "t", "rel_bias", "points_to"], f)}
<h2>Key numbers</h2>{table(diag.get('key_numbers', []), ["key", "sim", "actual", "diff", "se"], f)}
<h2>Residual breakdown</h2>{table(diag['residuals'], ["group", "metric", "n", "mean_error", "se", "t"], f)}
<h2>Worst misses</h2><ol>{misses}</ol><p>Full component detail for each miss is in diagnose.json.</p></body></html>"""
    path = out_dir / "diagnose.html"
    path.write_text(html, encoding="utf-8")
    return path
