"""Self-contained HTML report for one simulated game.

Renders the `report.summarize` dict (same content as `report.console`) into one HTML file with plotly.js
embedded inline, so the file opens offline. Pure presentation: every number shown comes from the summary.
The figure builders and formatters here are also used by the Streamlit GUI (app/), so both look the same.
"""
from __future__ import annotations

import datetime as dt
import html
from pathlib import Path

import plotly.graph_objects as go
import plotly.io as pio

# Dark chart system (reference dataviz palette, dark steps). Home = series 1 (blue), away = series 2 (orange).
C = {
    "page": "#0d0d0d", "surface": "#1a1a19", "raised": "#222220", "ink": "#f0efec", "ink2": "#c3c2b7",
    "muted": "#898781", "grid": "#2c2c2a", "axis": "#383835", "border": "rgba(255,255,255,0.10)",
    "home": "#3987e5", "away": "#d95926", "over": "#199e70", "under": "#9085e9", "neutral": "#898781",
    "warn": "#fab219", "good": "#0ca30c",
}
FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'
PLOTLY_CONFIG = {"displayModeBar": False, "responsive": True}
CLOSE_TEXT = "too close to call at this sim count"

STAT_LABELS = {
    "total_yds": "Total yards", "pass_yds": "Pass yards (net)", "rush_yds": "Rush yards", "turnovers": "Turnovers",
    "sacks_taken": "Sacks taken", "plays": "Offensive plays", "top_min": "Time of possession (min)",
    "third_down_pct": "3rd-down conv. %", "first_downs": "First downs", "punts": "Punts", "fg_made": "FG made",
    "tds": "Touchdowns", "penalties": "Penalties",
}
PLAYER_STATS = {"pass_yds": "Pass yds", "pass_td": "Pass TD", "ints": "INT", "rush_yds": "Rush yds", "rec": "Rec",
                "rec_yds": "Rec yds"}


# ---- formatting (shared with the GUI) -------------------------------------------------------------------

def pct(p: float | None, digits: int = 1) -> str:
    return "–" if p is None else f"{100 * p:.{digits}f}%"


def fmt_pm(d: dict) -> str:
    """'61.3% ± 1.1' for a {p, moe, close} dict."""
    return f"{100 * d['p']:.1f}% ± {100 * d['moe']:.1f}"


def fmt_line(x: float | None) -> str:
    """Spread in sportsbook form: -2.5, +3.5, PK."""
    if x is None:
        return "–"
    x = float(x)
    return "PK" if x == 0 else f"{x:+.1f}"


def fmt_ml(v) -> str:
    if v is None or v == "":
        return "–"
    if isinstance(v, str):
        return v
    v = int(round(float(v)))
    return f"{v:+d}"


def ml_implied(ml) -> float | None:
    """Raw implied probability of an American moneyline (includes the book's margin)."""
    if ml is None:
        return None
    ml = float(ml)
    return 100 / (ml + 100) if ml > 0 else -ml / (-ml + 100)


def market_no_vig(home_ml, away_ml) -> tuple[float, float] | None:
    h, a = ml_implied(home_ml), ml_implied(away_ml)
    if h is None or a is None or h + a <= 0:
        return None
    return h / (h + a), a / (h + a)


def kickoff_text(gameday: str | None, gametime: str | None) -> str:
    """'Sun Nov 9 · 8:15 PM ET' from nflverse gameday/gametime (Eastern)."""
    if not gameday:
        return ""
    try:
        d = dt.date.fromisoformat(str(gameday)[:10])
        out = f"{d:%a} {d:%b} {d.day}"
    except ValueError:
        out = str(gameday)
    if gametime:
        try:
            t = dt.datetime.strptime(str(gametime)[:5], "%H:%M")
            out += f" · {t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'} ET"
        except ValueError:
            out += f" · {gametime}"
    return out


def market_rows(s: dict) -> list[dict]:
    """Market vs simulation, home perspective. gap > 0 on the spread = sim likes the home team more."""
    m = s.get("market")
    if not m:
        return []
    H = s["matchup"]["home"]
    f = s["fair"]
    rows = []
    if m.get("spread_home") is not None:
        gap = float(m["spread_home"]) - float(f["spread_home"])
        rows.append({"what": f"Spread ({H})", "market": fmt_line(m["spread_home"]), "sim": fmt_line(f["spread_home"]),
                     "gap": gap, "gap_text": spread_gap_text(s, gap)})
    if m.get("total") is not None:
        gap = float(f["total"]) - float(m["total"])
        rows.append({"what": "Total", "market": f"{float(m['total']):.1f}", "sim": f"{f['total']:.1f}", "gap": gap,
                     "gap_text": "even" if gap == 0 else f"sim {abs(gap):.1f} {'higher' if gap > 0 else 'lower'}"})
    nv = market_no_vig(m.get("home_ml"), m.get("away_ml"))
    if nv:
        w = s["win"]
        hw = w["home"]["p"] / max(w["home"]["p"] + w["away"]["p"], 1e-9)
        gap = hw - nv[0]
        rows.append({"what": f"{H} win (no-vig)", "market": f"{pct(nv[0])} ({fmt_ml(m.get('home_ml'))})",
                     "sim": f"{pct(hw)} ({f['home_ml']})", "gap": gap,
                     "gap_text": f"{100 * gap:+.1f} pts for {H}"})
    return rows


def spread_gap_text(s: dict, gap: float) -> str:
    H, A = s["matchup"]["home"], s["matchup"]["away"]
    if abs(gap) < 1e-9:
        return "matches market"
    return f"sim favors {H if gap > 0 else A} by {abs(gap):.1f} vs market"


def freshness_text(fr: dict) -> list[str]:
    out = []
    names = {"rosters_weekly": "Rosters", "depth_charts": "Depth charts", "injuries": "Injury report"}
    for k, v in (fr or {}).items():
        bits = [f"downloaded {str(v.get('downloaded') or '?').replace('T', ' ')}"]
        if v.get("nflverse_updated"):
            bits.append(f"nflverse updated {str(v['nflverse_updated']).replace('T', ' ').replace('Z', ' UTC')}")
        if v.get("latest_week_in_data") is not None:
            bits.append(f"latest week {v['latest_week_in_data']}")
        if v.get("latest_snapshot"):
            bits.append(f"snapshot {str(v['latest_snapshot'])[:16].replace('T', ' ')}")
        out.append(f"{names.get(k, k)}: " + ", ".join(bits))
    return out


# ---- figures (shared with the GUI) --------------------------------------------------------------------

def _layout(fig: go.Figure, xtitle: str, height: int = 320) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=48, r=16, t=40, b=44), paper_bgcolor=C["surface"], plot_bgcolor=C["surface"],
        font=dict(family=FONT, color=C["ink2"], size=12), barmode="overlay", bargap=0.18, barcornerradius=3,
        legend=dict(orientation="h", x=0, y=1.02, yanchor="bottom", bgcolor="rgba(0,0,0,0)", font=dict(color=C["ink2"])),
        hoverlabel=dict(bgcolor=C["raised"], bordercolor=C["axis"], font=dict(family=FONT, color=C["ink"])),
        hovermode="closest",
    )
    fig.update_xaxes(title=dict(text=xtitle, font=dict(color=C["muted"])), gridcolor=C["grid"], zeroline=False,
                     linecolor=C["axis"], tickcolor=C["axis"], tickfont=dict(color=C["muted"]), showgrid=False)
    fig.update_yaxes(title=dict(text="% of sims", font=dict(color=C["muted"])), gridcolor=C["grid"], zeroline=False,
                     linecolor=C["axis"], ticksuffix="%", tickfont=dict(color=C["muted"]), rangemode="tozero")
    return fig


def _line_marker(fig: go.Figure, x: float, label: str) -> None:
    fig.add_vline(x=x, line=dict(color=C["ink"], width=2))
    fig.add_annotation(x=x, y=1, yref="paper", text=label, showarrow=False, xanchor="left", yanchor="top", xshift=6,
                       font=dict(color=C["ink"], size=12), bgcolor=C["surface"])


def _bars(fig, xs, ys, cs, name, color, hover):
    if not xs:
        return
    fig.add_bar(x=xs, y=ys, name=name, marker=dict(color=color, line=dict(width=0)), customdata=cs,
                hovertemplate=hover)


def margin_figure(s: dict, spread: float | None = None, height: int = 320) -> go.Figure:
    """Histogram of home margin, colored by winner. spread (home perspective) draws the cover threshold."""
    H, A = s["matchup"]["home"], s["matchup"]["away"]
    hist = s["hist"]["margin"]
    n = sum(hist["counts"]) or 1
    groups = {"h": ([], [], []), "a": ([], [], []), "t": ([], [], [])}
    for v, c in zip(hist["values"], hist["counts"]):
        key = "h" if v > 0 else "a" if v < 0 else "t"
        label = f"{H} by {v}" if v > 0 else f"{A} by {-v}" if v < 0 else "Tie"
        g = groups[key]
        g[0].append(v); g[1].append(100 * c / n); g[2].append([label, c])  # noqa: E702
    hover = "%{customdata[0]}<br>%{y:.1f}% of sims (%{customdata[1]})<extra></extra>"
    fig = go.Figure()
    _bars(fig, *groups["h"], f"{H} wins", C["home"], hover)
    _bars(fig, *groups["a"], f"{A} wins", C["away"], hover)
    _bars(fig, *groups["t"], "Tie", C["neutral"], hover)
    _layout(fig, f"Final margin ({H} − {A})", height)
    if spread is not None:
        _line_marker(fig, -float(spread), f"{H} {fmt_line(spread)}")
    return fig


def total_figure(s: dict, total: float | None = None, height: int = 320) -> go.Figure:
    """Histogram of combined points. With a total line, bars are split over / under / push."""
    hist = s["hist"]["total"]
    n = sum(hist["counts"]) or 1
    fig = go.Figure()
    hover = "Total %{x}<br>%{y:.1f}% of sims (%{customdata})<extra></extra>"
    if total is None:
        _bars(fig, hist["values"], [100 * c / n for c in hist["counts"]], hist["counts"], "Total points", C["under"], hover)
        fig.update_layout(showlegend=False)
    else:
        L = float(total)
        groups = {"o": ([], [], []), "u": ([], [], []), "p": ([], [], [])}
        for v, c in zip(hist["values"], hist["counts"]):
            g = groups["o" if v > L else "u" if v < L else "p"]
            g[0].append(v); g[1].append(100 * c / n); g[2].append(c)  # noqa: E702
        _bars(fig, *groups["u"], f"Under {L:g}", C["under"], hover)
        _bars(fig, *groups["o"], f"Over {L:g}", C["over"], hover)
        _bars(fig, *groups["p"], "Push", C["neutral"], hover)
    _layout(fig, "Total points", height)
    if total is not None:
        _line_marker(fig, float(total), f"{float(total):g}")
    return fig


# ---- HTML -----------------------------------------------------------------------------------------------

TOKENS = f"""
:root {{
  color-scheme: dark;
  --page: {C['page']}; --surface: {C['surface']}; --raised: {C['raised']}; --ink: {C['ink']}; --ink2: {C['ink2']};
  --muted: {C['muted']}; --grid: {C['grid']}; --axis: {C['axis']}; --border: {C['border']};
  --home: {C['home']}; --away: {C['away']}; --warn: {C['warn']};
}}
"""
# Component styles, all scoped under .gs so the GUI can inject them without touching Streamlit's own widgets.
COMPONENT_CSS = f"""
.gs, .gs * {{ box-sizing: border-box; }}
.gs {{ color: var(--ink); font-family: {FONT}; font-size: 15px; line-height: 1.45; }}
.gs h1 {{ font-size: 28px; margin: 0 0 4px; letter-spacing: -0.01em; color: var(--ink); padding: 0; font-weight: 700; }}
.gs h2 {{ font-size: 16px; margin: 0 0 12px; color: var(--ink); font-weight: 600; padding: 0; }}
.gs h3 {{ font-size: 14px; margin: 16px 0 8px; color: var(--ink2); font-weight: 600; padding: 0; }}
.gs .sub {{ color: var(--ink2); margin: 0 0 12px; }}
.gs .tag {{ display: inline-block; padding: 1px 8px; border: 1px solid var(--axis); border-radius: 999px; font-size: 12px; color: var(--ink2); margin-left: 6px; vertical-align: middle; font-weight: 400; }}
.gs .banners {{ list-style: none; padding: 0; margin: 12px 0 0; }}
.gs .banners li {{ border-left: 3px solid var(--warn); background: var(--surface); padding: 6px 10px; margin: 4px 0; color: var(--ink2); font-size: 13px; border-radius: 0 6px 6px 0; }}
.gs .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 16px; margin: 16px 0; }}
.gs.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 16px; margin: 0; }}
.gs .grid2 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }}
.gs .grid2 > * {{ min-width: 0; }}
.gs .grid2 > .card {{ margin: 0; }}
.gs .tiles {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin: 16px 0; }}
@media (min-width: 1440px) {{ .gs .tiles {{ grid-template-columns: repeat(6, minmax(0, 1fr)); }} }}
.gs .tile {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px; }}
.gs .tile .k {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; }}
.gs .tile .v {{ font-size: 24px; font-weight: 600; margin-top: 2px; }}
.gs .tile .v small {{ font-size: 14px; color: var(--ink2); font-weight: 400; }}
.gs .tile .n {{ color: var(--ink2); font-size: 12px; margin-top: 2px; }}
.gs .sw {{ display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 6px; vertical-align: 0; }}
.gs .close {{ color: var(--muted); font-size: 12px; font-style: italic; }}
.gs .scroll {{ overflow-x: auto; }}
.gs .tall {{ max-height: 360px; overflow-y: auto; }}
.gs table {{ border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; font-size: 14px; margin: 0; border: 0; }}
.gs th, .gs td {{ padding: 6px 10px; text-align: right; border: 0; border-bottom: 1px solid var(--grid); white-space: nowrap; color: var(--ink); }}
.gs th {{ color: var(--muted); font-weight: 500; font-size: 12px; position: sticky; top: 0; background: var(--surface); }}
.gs th:first-child, .gs td:first-child {{ text-align: left; }}
.gs table.wrap td {{ white-space: normal; }}
.gs table.wrap td .close {{ display: inline-block; }}
.gs tr.hl td {{ background: var(--raised); color: var(--ink); font-weight: 600; }}
.gs .muted {{ color: var(--muted); }}
.gs .ink2 {{ color: var(--ink2); }}
.gs ul {{ margin: 0; padding-left: 20px; }}
.gs footer {{ color: var(--muted); font-size: 12px; margin-top: 24px; border-top: 1px solid var(--grid); padding-top: 12px; }}
.gs footer code {{ color: var(--ink2); background: none; padding: 0; }}
@media (max-width: 600px) {{
  .gs h1 {{ font-size: 22px; }}
  .gs th, .gs td {{ padding: 5px 6px; font-size: 13px; white-space: normal; }}
  .gs .tiles {{ grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }}
  .gs .tile .v {{ font-size: 20px; }}
}}
"""
PAGE_CSS = f"""
html, body {{ margin: 0; background: var(--page); color: var(--ink); font-family: {FONT}; }}
main {{ max-width: 1120px; margin: 0 auto; padding: 24px 16px 48px; }}
@media (max-width: 600px) {{ main {{ padding: 16px 12px 32px; }} }}
"""
CSS = TOKENS + PAGE_CSS + COMPONENT_CSS


def esc(x) -> str:
    return html.escape(str(x))


def pm_html(d: dict) -> str:
    flag = f' <span class="close" title="within the simulation margin of error of 50%">{CLOSE_TEXT}</span>' if d.get("close") else ""
    return f"{esc(fmt_pm(d))}{flag}"


def table_html(head: list[str], rows: list[list], hl: set[int] | None = None, wrap: bool = False) -> str:
    th = "".join(f"<th>{esc(h)}</th>" for h in head)
    body = []
    for i, r in enumerate(rows):
        row_cls = ' class="hl"' if hl and i in hl else ""
        body.append(f"<tr{row_cls}>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>")
    table_cls = ' class="wrap"' if wrap else ""
    return f'<div class="scroll"><table{table_cls}><thead><tr>{th}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def swatch(color: str) -> str:
    return f'<span class="sw" style="background:{color}"></span>'


def _fig_div(fig: go.Figure, div_id: str) -> str:
    return pio.to_html(fig, full_html=False, include_plotlyjs=False, config=PLOTLY_CONFIG, div_id=div_id)


def header_html(s: dict) -> str:
    H, A = s["matchup"]["home"], s["matchup"]["away"]
    g = s.get("game") or {}
    tags = ""
    if s["matchup"].get("neutral"):
        tags += '<span class="tag">neutral site</span>'
    if s["matchup"].get("playoff"):
        tags += '<span class="tag">playoff</span>'
    bits = []
    season = (s.get("request") or {}).get("season") or s["meta"].get("season")
    if g.get("week"):
        bits.append(f"{season} week {g['week']}" if season else f"Week {g['week']}")
    elif season:
        bits.append(str(season))
    if g.get("gameday"):
        bits.append(kickoff_text(g.get("gameday"), g.get("gametime")))
    if g.get("stadium"):
        bits.append(g["stadium"] + (f" ({g['roof']})" if g.get("roof") else ""))
    bits.append(f"{s['n_sims']:,} simulations")
    banners = "".join(f"<li>{esc(b)}</li>" for b in s.get("banners", []))
    return (f'<h1>{swatch(C["away"])}{esc(A)} @ {swatch(C["home"])}{esc(H)}{tags}</h1>'
            f'<p class="sub">{esc(" · ".join(bits))}</p>' + (f'<ul class="banners">{banners}</ul>' if banners else ""))


def tiles_html(s: dict) -> str:
    H, A = s["matchup"]["home"], s["matchup"]["away"]
    w, f = s["win"], s["fair"]

    def tile(k, v, n=""):
        return f'<div class="tile"><div class="k">{k}</div><div class="v">{v}</div><div class="n">{n}</div></div>'

    def win_tile(team, d, color):
        v = f"{100 * d['p']:.1f}%<small> ± {100 * d['moe']:.1f}</small>"
        n = f'<span class="close">{CLOSE_TEXT}</span>' if d.get("close") else "win probability"
        return tile(f"{swatch(color)}{esc(team)} win", v, n)

    return ('<div class="tiles">' + win_tile(H, w["home"], C["home"]) + win_tile(A, w["away"], C["away"])
            + tile("Tie", f"{100 * w['tie']['p']:.1f}%<small> ± {100 * w['tie']['moe']:.1f}</small>", "after overtime")
            + tile("Fair moneyline", f"{esc(f['home_ml'])}<small> / {esc(f['away_ml'])}</small>", f"{esc(H)} / {esc(A)}")
            + tile(f"Fair spread ({esc(H)})", esc(fmt_line(f["spread_home"])), f"from median margin; mean-based {esc(fmt_line(f['spread_home_mean']))}")
            + tile("Fair total", f"{f['total']:.1f}", f"median; mean {f['total_mean']:.1f}") + "</div>")


def market_html(s: dict) -> str:
    rows = market_rows(s)
    if not rows:
        return ""
    return ('<div class="card"><h2>Market vs simulation</h2>'
            + table_html(["", "Market", "Simulation", "Gap"], [[esc(r["what"]), esc(r["market"]), esc(r["sim"]), esc(r["gap_text"])] for r in rows])
            + '<p class="muted" style="font-size:12px;margin:8px 0 0">Spreads are from the home team\'s perspective; negative = home favored. '
              "Market from the nflverse schedule.</p></div>")


def lines_html(s: dict) -> str:
    H, A = s["matchup"]["home"], s["matchup"]["away"]
    rows = []
    if "spread" in s:
        sp = s["spread"]
        rows.append([f"Spread {esc(H)} {esc(fmt_line(sp['line']))}", f"{esc(H)} covers {pm_html(sp['home_cover'])}",
                     f"{esc(A)} covers {pm_html(sp['away_cover'])}", pct(sp["push"])])
        rows.append([f"1st half {esc(H)} {esc(fmt_line(sp['first_half_line']))}", f"{esc(H)} covers {pm_html(sp['first_half_home_cover'])}", "", ""])
    if "total" in s:
        t = s["total"]
        rows.append([f"Total {t['line']:.1f}", f"Over {pm_html(t['over'])}", f"Under {pm_html(t['under'])}", pct(t["push"])])
    if not rows:
        return ""
    return '<div class="card"><h2>Lines</h2>' + table_html(["Line", "", "", "Push"], rows, wrap=True) + "</div>"


def scores_html(s: dict) -> str:
    H, A = s["matchup"]["home"], s["matchup"]["away"]
    rows = []
    for k, name in (("home", H), ("away", A), ("margin", f"Margin ({H} − {A})"), ("total", "Total")):
        d = s["scores"][k]
        rows.append([esc(name), f"{d['mean']:.1f}"] + [f"{d[p]:.0f}" for p in ("p10", "p25", "p50", "p75", "p90")])
    return '<div class="card"><h2>Score distribution</h2>' + table_html(["", "Mean", "p10", "p25", "Median", "p75", "p90"], rows) + "</div>"


def alt_html(s: dict) -> str:
    H, A = s["matchup"]["home"], s["matchup"]["away"]
    sp_line = (s.get("spread") or {}).get("line")
    t_line = (s.get("total") or {}).get("line")
    sp_rows, sp_hl = [], set()
    # window of +-7 points around the line being looked at (user/market line, else the fair spread)
    center = float(sp_line if sp_line is not None else s["fair"]["spread_home"])
    alt = [r for r in s.get("alt_spreads", []) if abs(r["line"] - center) <= 7] or s.get("alt_spreads", [])
    for i, r in enumerate(alt):
        if sp_line is not None and r["line"] == sp_line:
            sp_hl.add(i)
        sp_rows.append([esc(fmt_line(r["line"])), f"{pct(r['hit'])} ± {100 * r['moe']:.1f}", pct(r["push"]), pct(r["miss"])])
    t_rows, t_hl = [], set()
    for i, r in enumerate(s.get("alt_totals", [])):
        if t_line is not None and r["line"] == t_line:
            t_hl.add(i)
        t_rows.append([f"{r['line']:.1f}", f"{pct(r['hit'])} ± {100 * r['moe']:.1f}", pct(r["push"]), pct(r["miss"])])
    return ('<div class="grid2">'
            f'<div class="card"><h2>Alternate spreads ({esc(H)})</h2><div class="tall">'
            + table_html([f"{H} line", f"{H} covers", "Push", f"{A} covers"], sp_rows, sp_hl) + "</div></div>"
            f'<div class="card"><h2>Alternate totals</h2><div class="tall">'
            + table_html(["Total", "Over", "Push", "Under"], t_rows, t_hl) + "</div></div></div>")


def quarters_html(s: dict) -> str:
    H, A = s["matchup"]["home"], s["matchup"]["away"]
    q = s["quarters"]
    rows = [[f"{swatch(C[k])}{esc(name)}"] + [f"{q[k][x]:.1f}" for x in ("q1", "q2", "q3", "q4", "ot")]
            + [f"{sum(q[k].values()):.1f}"] for k, name in (("home", H), ("away", A))]
    fh = s["first_half"]
    fh_rows = [[f"{esc(H)} leads", pm_html(fh["home_win"])], [f"{esc(A)} leads", pm_html(fh["away_win"])],
               ["Tied", pm_html(fh["tie"])]]
    return ('<div class="grid2"><div class="card"><h2>Average points by quarter</h2>'
            + table_html(["", "Q1", "Q2", "Q3", "Q4", "OT", "Game"], rows) + "</div>"
            f'<div class="card"><h2>First half</h2><p class="sub">{esc(H)} {fh["home_pts"]:.1f} – {esc(A)} {fh["away_pts"]:.1f} (average)</p>'
            + table_html(["At halftime", "Probability"], fh_rows) + "</div></div>")


def team_stats_html(s: dict) -> str:
    H, A = s["matchup"]["home"], s["matchup"]["away"]
    ts = s["team_stats"]
    rows = []
    for k in ts["home"]:
        hd, ad = ts["home"][k], ts["away"][k]
        dec = 1 if k in ("turnovers", "sacks_taken", "fg_made", "tds", "punts", "penalties") else 0
        f = lambda d: f"{d['p10']:.0f} / <b>{d['p50']:.0f}</b> / {d['p90']:.0f}"  # noqa: E731
        rows.append([esc(STAT_LABELS.get(k, k)), f(hd), f"{hd['mean']:.{dec}f}", f(ad), f"{ad['mean']:.{dec}f}"])
    return ('<div class="card"><h2>Team stat ranges</h2><p class="muted" style="font-size:12px;margin:-6px 0 8px">p10 / median / p90 across simulations</p>'
            + table_html(["", f"{H} range", f"{H} mean", f"{A} range", f"{A} mean"], rows) + "</div>")


def top_scores_html(s: dict) -> str:
    H, A = s["matchup"]["home"], s["matchup"]["away"]
    rows = [[str(i + 1), f"{esc(H)} {t['home']} – {esc(A)} {t['away']}", pct(t["p"])] for i, t in enumerate(s.get("top_scores", []))]
    return '<div class="card"><h2>Most common final scores</h2>' + table_html(["#", "Score", "Share of sims"], rows) + "</div>"


def availability_html(s: dict) -> str:
    av = s.get("availability")
    if not av:
        return ""
    qb_starts = s["meta"].get("qb_starts") or {}
    parts = [f'<div class="card"><h2>Availability (week {esc(av.get("week"))})</h2><div class="grid2">']
    for team, d in av.get("teams", {}).items():
        qs = qb_starts.get(team) or {}
        qrows = [[esc(q["name"]), pct(q["p_start"], 0)] for q in d.get("qb", [])]
        if qs:
            qrows.append(['<span class="muted">realized in sims</span>', esc(", ".join(f"{k} {100 * v:.0f}%" for k, v in qs.items()))])
        lrows = [[esc(p["name"]), esc(p["pos"]), "OUT" if p["p_play"] == 0 else pct(p["p_play"], 0),
                  f'<span class="ink2">{esc(p.get("reason") or p.get("status") or "")}</span>'] for p in d.get("listed", [])]
        parts.append(f'<div><h3>{esc(team)} — starting QB</h3>' + table_html(["QB", "P(start)"], qrows)
                     + f"<h3>{esc(team)} — listed players</h3>"
                     + (table_html(["Player", "Pos", "Plays", "Why"], lrows, wrap=True) if lrows else '<p class="muted">nobody listed</p>') + "</div>")
    parts.append("</div>")
    if av.get("overrides"):
        parts.append("<h3>Overrides applied</h3><ul>" + "".join(f"<li>{esc(o)}</li>" for o in av["overrides"]) + "</ul>")
    fr = freshness_text(av.get("freshness"))
    if fr:
        parts.append('<h3>Data freshness</h3><ul class="ink2" style="font-size:13px">' + "".join(f"<li>{esc(x)}</li>" for x in fr) + "</ul>")
    parts.append("</div>")
    return "".join(parts)


def players_html(s: dict) -> str:
    out = []
    pl = s.get("players")
    if pl:
        out.append('<div class="card"><h2>Player projections</h2><p class="muted" style="font-size:12px;margin:-6px 0 8px">median [p10–p90]</p>')
        for team, plist in pl.items():
            rows = []
            for p in plist:
                st = p.get("stats", {})
                cells = []
                for k in PLAYER_STATS:
                    d = st.get(k)
                    cells.append(f"{d['p50']:.0f} <span class='muted'>[{d['p10']:.0f}–{d['p90']:.0f}]</span>"
                                 if d and d.get("mean", 0) > 0.05 else '<span class="muted">–</span>')
                rows.append([esc(p.get("name", "")), esc(p.get("pos", ""))] + cells + [pct(p.get("anytime_td"), 0)])
            out.append(f"<h3>{esc(team)}</h3>" + table_html(["Player", "Pos", *PLAYER_STATS.values(), "Anytime TD"], rows))
        out.append("</div>")
    props = s.get("props")
    if props:
        rows = [[esc(p.get("player", "")), esc(p.get("stat", "")), esc(p.get("line", "")), pm_html(p["over"]),
                 pct(p.get("under") if not isinstance(p.get("under"), dict) else p["under"]["p"])] for p in props]
        out.append('<div class="card"><h2>Props</h2>' + table_html(["Player", "Stat", "Line", "Over", "Under"], rows) + "</div>")
    return "".join(out)


def footer_html(s: dict) -> str:
    m = s["meta"]
    w = m.get("season_weights")
    wtxt = ", ".join(f"{k}: {v}" for k, v in w.items()) if isinstance(w, dict) else esc(w)
    thru = m.get("through_week") or m.get("last_week_in_data")
    bits = [f"Data window: seasons {esc(m.get('window_seasons'))} (weights {esc(wtxt)}), through week {esc(thru)}",
            f"seed <code>{esc(m.get('seed'))}</code>", f"sims {s['n_sims']:,}",
            f"runtime {esc(m.get('runtime_seconds'))} s" + (f" (total {esc(m.get('total_seconds'))} s)" if m.get("total_seconds") else ""),
            f"config <code>{esc(m.get('config_hash'))}</code>"]
    return ("<footer>" + " · ".join(bits) + "<br>Every percentage is shown with its 95% Monte Carlo margin of error; "
            f"values within that error of 50% are flagged “{CLOSE_TEXT}”. Generated by gridsim "
            f"{dt.datetime.now():%Y-%m-%d %H:%M}.</footer>")


# Charts are drawn while the page is still being parsed; refit them once the grid has its final widths.
_RESIZE_JS = ("<script>window.addEventListener('load',function(){document.querySelectorAll('.js-plotly-plot')"
              ".forEach(function(d){Plotly.Plots.resize(d);});});</script>")


def render_html(summary: dict) -> str:
    """The complete report page as one string (plotly.js inline)."""
    from plotly.offline import get_plotlyjs

    s = summary
    H, A = s["matchup"]["home"], s["matchup"]["away"]
    sp = (s.get("spread") or {}).get("line")
    tl = (s.get("total") or {}).get("line")
    charts = ('<div class="grid2">'
              f'<div class="card"><h2>Final margin</h2>{_fig_div(margin_figure(s, sp), "margin-chart")}</div>'
              f'<div class="card"><h2>Total points</h2>{_fig_div(total_figure(s, tl), "total-chart")}</div></div>')
    body = (header_html(s) + tiles_html(s) + market_html(s) + lines_html(s) + charts + scores_html(s) + alt_html(s) + quarters_html(s)
            + team_stats_html(s) + top_scores_html(s) + availability_html(s) + players_html(s) + footer_html(s))
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{esc(A)} @ {esc(H)} · gridsim</title><style>{CSS}</style>"
            f'<script type="text/javascript">{get_plotlyjs()}</script></head>'
            f'<body><main class="gs">{body}</main>{_RESIZE_JS}</body></html>')


def write_html(summary: dict, path) -> None:
    """Write one self-contained HTML report (no external requests)."""
    Path(path).write_text(render_html(summary), encoding="utf-8")
