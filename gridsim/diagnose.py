"""Diagnose: where is the sim wrong? Reads backtest output (games + components) and reports:

1. Component scorecard: aggregate sim vs actual for every component (ratio of sums), bias, bootstrap SE
   over team-games, flagged when |bias| > 2 SE, with the sub-model each one points to.
2. Residual breakdown: margin and total errors grouped by favorite side and spread size (picked by the market and,
   separately, by the sim's own forecast), roof, divisional, early season, QB change, primetime, short rest; flagged
   when the group mean error is > 2 SE from 0, shown next to the closing line's error on the same games.
   Margin calibration: slope of result (and of closing line) on the sim's predicted margin.
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
    "ypc": "all rushes incl. QB scrambles: see ypc_designed (run pools) and scramble_rate (QB dropback pools / pass rate)",
    "ypc_designed": "designed-run outcome pools: w_off, k_outcome, team back-off levels, season_weights (league run-game drift)",
    "scramble_rate": "QB dropback pools (k_qb, backup_max_dropbacks): scrambles per dropback",
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


def _available(comp: pl.DataFrame) -> list[tuple[str, tuple]]:
    """Components whose columns exist (backtests saved before a component was added lack its columns)."""
    cols = set(comp.columns)
    return [(name, (num, den)) for name, (num, den) in COMPONENTS.items()
            if {f"sim_{num}", f"act_{num}"} <= cols and (not den or {f"sim_{den}", f"act_{den}"} <= cols)]


def component_scorecard(comp: pl.DataFrame, n_boot: int = 300, seed: int = 0) -> list[dict]:
    rng = np.random.default_rng(seed)
    n_all = comp.height
    idx_all = [rng.integers(0, n_all, n_all) for _ in range(n_boot)] if n_all else []
    out = []
    for name, (num, den) in _available(comp):
        cols = [f"sim_{num}", f"act_{num}"] + ([f"sim_{den}", f"act_{den}"] if den else [])
        part = comp.select(cols).drop_nulls().drop_nans()
        n = part.height
        if n == 0:
            continue
        # backtests saved before a component existed have no values for it: use the rows that do
        idx = idx_all if n == n_all else [rng.integers(0, n, n) for _ in range(n_boot)]
        sn, an = part[f"sim_{num}"].to_numpy(), part[f"act_{num}"].to_numpy()
        sd = part[f"sim_{den}"].to_numpy() if den else np.ones(n)
        ad = part[f"act_{den}"].to_numpy() if den else np.ones(n)

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
    sim = pl.col("pred_margin")
    # Groups picked by the MARKET condition on a better-informed forecast: any less-informed but calibrated forecast
    # shows a "favorite" bias there (it is less extreme than the market), so their margin errors are reported next to
    # the closing line's own error on the same games. Groups picked by the SIM's own forecast are the calibration test.
    return {
        "home favorite": mkt > 0, "away favorite": mkt < 0,
        "spread < 3": mkt.abs() < 3, "spread 3-7": (mkt.abs() >= 3) & (mkt.abs() < 7), "spread 7+": mkt.abs() >= 7,
        "sim: home favored": sim > 0, "sim: away favored": sim < 0,
        "sim spread 3-7": (sim.abs() >= 3) & (sim.abs() < 7), "sim spread 7+": sim.abs() >= 7,
        "dome/closed": pl.col("roof").is_in(["dome", "closed"]), "outdoors/open": pl.col("roof").is_in(["outdoors", "open"]),
        "divisional": pl.col("div_game") == 1, "non-divisional": pl.col("div_game") == 0,
        "weeks 1-4": pl.col("week") <= 4, "weeks 5+": pl.col("week") >= 5,
        "QB change (either team)": pl.col("home_qb_change").fill_null(False) | pl.col("away_qb_change").fill_null(False),
        "primetime": prime, "short rest (<6 days)": rest < 6, "neutral site": pl.col("location") == "Neutral",
    }


MARKET_PICKED = {"home favorite", "away favorite", "spread < 3", "spread 3-7", "spread 7+"}


def residual_breakdown(g: pl.DataFrame) -> list[dict]:
    fav = pl.when(pl.col("mkt_margin") >= 0).then(1).otherwise(-1)
    g = g.with_columns(err_margin=pl.col("pred_margin") - pl.col("margin"), err_total=pl.col("pred_total") - pl.col("total"),
                       err_fav=(pl.col("pred_margin") - pl.col("margin")) * fav,
                       # the closing line's error on the same games, same sign convention
                       mkt_err_margin=pl.col("mkt_margin") - pl.col("margin"), mkt_err_total=pl.col("mkt_total") - pl.col("total"),
                       mkt_err_fav=(pl.col("mkt_margin") - pl.col("margin")) * fav)
    out = []
    for name, cond in _groups(g).items():
        sub = g.filter(cond.fill_null(False))
        if sub.height < 8:
            continue
        for col, label in (("err_margin", "margin (home) error"), ("err_total", "total error"), ("err_fav", "favorite margin error")):
            x = sub[col].to_numpy()
            se = float(np.std(x, ddof=1) / np.sqrt(len(x)))
            m = float(np.mean(x))
            row = {"group": name, "metric": label, "n": len(x), "mean_error": m, "se": se, "t": m / se if se else 0.0,
                   "flag": abs(m) > 2 * se}
            mk = sub[f"mkt_{col}"].to_numpy().astype(float)
            ok = ~np.isnan(mk)
            if ok.sum() >= 8:
                d = x[ok] - mk[ok]            # sim minus market on the same games (paired)
                row["market_error"] = float(np.mean(mk[ok]))
                row["sim_vs_market"] = float(np.mean(d))
                row["se_vs_market"] = float(np.std(d, ddof=1) / np.sqrt(ok.sum()))
            out.append(row)
    out.sort(key=lambda r: -abs(r["t"]))
    return out


def margin_calibration(g: pl.DataFrame) -> dict:
    """Is the spread of predicted margins right? Slope of actual margin on predicted margin (1 = calibrated, > 1 =
    compressed, < 1 = too extreme), and the same slope with the closing line in place of the result: if the market
    is efficient E[result | sim] = E[market | sim], so this is a far less noisy estimate of the same slope. Comparing
    sd(sim) with sd(market) is NOT a test: a calibrated forecast that knows less than the market is always less spread out."""
    def slope(y, x):
        x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
        ok = ~(np.isnan(x) | np.isnan(y))
        x, y = x[ok], y[ok]
        if len(x) < 20 or np.std(x) == 0:
            return None, None
        b, a = np.polyfit(x, y, 1)
        r = y - (a + b * x)
        return float(b), float(np.std(r, ddof=2) / (np.std(x) * np.sqrt(len(x))))
    b_act, se_act = slope(g["margin"].to_numpy(), g["pred_margin"].to_numpy())
    b_mkt, se_mkt = slope(g["mkt_margin"].to_numpy(), g["pred_margin"].to_numpy())
    return {"slope_actual": b_act, "se_actual": se_act, "slope_market": b_mkt, "se_market": se_mkt, "n": int(g.height)}


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
                                   for name, (num, den) in _available(comp)}
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


def plain_summary(score: list[dict], resid: list[dict], metrics: dict, keys: list[dict] | None = None,
                  calib: dict | None = None) -> list[str]:
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
        if r["group"] in MARKET_PICKED and r["metric"] != "total error":
            # the sim is always less extreme than the market in groups picked by the market, even when calibrated;
            # its paired sim-vs-market SE is small, so it would rank high every season. The calibration slope and the
            # "sim: ..." groups are the test; these rows stay in the table for reference only.
            continue
        if r["flag"] and r["metric"] != "favorite margin error":
            msg = (f"{r['metric']} is biased {r['mean_error']:+.2f} pts in '{r['group']}' games "
                   f"(n={r['n']}, {abs(r['t']):.1f} SE)")
            if "market_error" in r:
                # the part the closing line missed too is outcome noise for any forecaster; only the rest is the sim's
                msg += (f": the closing line was off by {r['market_error']:+.2f} on the same games, so the sim's own part is "
                        f"{r['sim_vs_market']:+.2f} ± {r['se_vs_market']:.2f}")
                if abs(r["sim_vs_market"]) <= 2 * r["se_vs_market"]:
                    continue        # the line missed by as much: nothing here is the sim's own; the table shows it
                weight = abs(r["sim_vs_market"]) / max(r["se_vs_market"], 1e-9) * 0.5
            else:
                msg += " — a missing feature or mis-weighted recency for that group"
                weight = abs(r["t"]) * 0.9
            items.append((min(weight, abs(r["t"])), msg + "."))
    if calib and calib.get("slope_market") is not None:
        b, se = calib["slope_market"], calib["se_market"]
        ba, sea = calib.get("slope_actual"), calib.get("se_actual")
        if abs(b - 1) > 2 * se:
            word = "compressed (team strength shrunk too hard or scaled too low)" if b > 1 else "too extreme (team strength noise passed through)"
            items.append((abs(b - 1) / se * 0.5, f"predicted margins are {word}: closing line vs sim margin slope {b:.2f} ± {se:.2f}, "
                                                  f"result vs sim margin slope {ba:.2f} ± {sea:.2f} (1 = calibrated)."))
    for name, d in (metrics.get("coverage_detail") or {}).items():
        if abs(d["z"]) < 2:
            continue      # within binomial noise of 80% (n ~ 56 games: 72% is only 1.5 SE)
        msg = f"80% {name} intervals cover {100 * d['cov']:.0f}% of games ({abs(d['z']):.1f} SE) — "
        narrow = d["cov"] < 0.8
        vr = d.get("var_ratio")
        if narrow and d.get("below") is not None and abs(d["below"] - d["above"]) >= (0.8 - d["cov"]):
            hi = d["below"] > d["above"]
            msg += (f"misses are lopsided ({100 * d['below']:.0f}% below the 10th percentile, {100 * d['above']:.0f}% above "
                    f"the 90th): the sim's {name} is centred too {'high' if hi else 'low'} (bias {d['bias']:+.2f}), not too narrow.")
        elif vr is not None and ((vr > 1.1) if narrow else (vr < 0.9)):
            msg += (f"the sim's {name} spread is too {'narrow' if narrow else 'wide'} "
                    f"(squared error / sim variance = {vr:.2f}; 1 = right).")
        elif vr is not None:
            msg += (f"but squared error / sim variance is {vr:.2f} (1 = right), so neither the width nor the centre is "
                    f"clearly off; watch it over more games.")
        else:
            msg += f"the sim's {name} variance is {'too narrow' if narrow else 'too wide'}."
        items.append((abs(d["z"]), msg))
    items.sort(key=lambda x: -x[0])
    return [t for _, t in items[:3]] or ["No component or group is biased beyond simulation/sampling noise."]


def coverage_detail(g: pl.DataFrame) -> dict:
    """80% interval coverage with its binomial SE, and (when the backtest saved them) which tail the misses fall in
    and the ratio of squared error to sim variance, so a biased centre is not mistaken for too narrow a spread."""
    out = {}
    for name, y, pred in (("margin", "margin", "pred_margin"), ("total", "total", "pred_total")):
        col = f"cov_{name}"
        if col not in g.columns or g.height == 0:
            continue
        n = g.height
        cov = float(g[col].mean())
        d = {"n": n, "cov": cov, "z": (cov - 0.8) / float(np.sqrt(0.16 / n)),
             "bias": float((g[pred] - g[y]).mean()), "below": None, "above": None, "var_ratio": None}
        # older games.csv files (mixed in by --from-dirs) lack these columns: use only the rows that have them
        if f"pit_{name}" in g.columns and g[f"pit_{name}"].drop_nulls().len():
            pit = g[f"pit_{name}"].drop_nulls().to_numpy()
            d["below"], d["above"] = float(np.mean(pit < 0.1)), float(np.mean(pit > 0.9))
        if f"sd_{name}" in g.columns:
            h = g.filter(pl.col(f"sd_{name}").is_not_null())
            if h.height:
                err = (h[pred] - h[y]).to_numpy()
                d["var_ratio"] = float(np.mean(err ** 2) / np.mean(h[f"sd_{name}"].to_numpy() ** 2))
        out[name] = d
    return out


def diagnose(games: pl.DataFrame, comp: pl.DataFrame) -> dict:
    metrics = summarize_games(games)
    metrics["coverage_detail"] = coverage_detail(games)
    score = component_scorecard(comp)
    resid = residual_breakdown(games)
    keys = key_numbers(games)
    calib = margin_calibration(games)
    return {"metrics": metrics, "summary": plain_summary(score, resid, metrics, keys, calib), "components": score,
            "key_numbers": keys, "margin_calibration": calib,
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
<h2>Residual breakdown</h2><p>market_error = the closing line's mean error on the same games; sim_vs_market = the sim's own part (paired). Groups
named "sim: ..." are picked by the sim's forecast (the calibration test); the others are picked by the market.</p>
{table(diag['residuals'], ["group", "metric", "n", "mean_error", "se", "t", "market_error", "sim_vs_market", "se_vs_market"], f)}
<h2>Worst misses</h2><ol>{misses}</ol><p>Full component detail for each miss is in diagnose.json.</p></body></html>"""
    path = out_dir / "diagnose.html"
    path.write_text(html, encoding="utf-8")
    return path
