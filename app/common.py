"""Shared GUI helpers: settings, caching wrappers around gridsim.api, formatting. NO modeling logic here:
every number comes from gridsim.api (the same service layer the CLI uses), so the same request + seed gives
identical results in the GUI and in `gridsim run`.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:          # works even without `pip install -e .`
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from gridsim import api  # noqa: E402
from gridsim import html_report as hr  # noqa: E402
from gridsim.availability import NameMatchError  # noqa: E402
from gridsim.config import load_config  # noqa: E402
from gridsim.report import moe  # noqa: E402

SKILL_POS = ("QB", "RB", "FB", "WR", "TE", "K")
GAME_PAGE = "pages/game.py"
WEEK_PAGE = "pages/this_week.py"
PROPS_PAGE = "pages/props.py"
PARLAY_PAGE = "pages/parlay.py"
WHATIF_PAGE = "pages/whatif_live.py"
SIGNALS_PAGE = "pages/signals.py"
SCORECARD_PAGE = "pages/scorecard.py"
DIAGNOSE_PAGE = "pages/diagnose.py"
SETTINGS_PAGE = "pages/settings.py"
KEEP = "p_"   # widget keys starting with this survive page switches (re-asserted in streamlit_app.py)


def cfg():
    """config.yaml is re-read on every rerun (cheap) so edits show up without restarting."""
    return load_config()


def inject_css() -> None:
    st.html(f"<style>{hr.TOKENS}{hr.COMPONENT_CSS}{_APP_CSS}</style>")


_APP_CSS = """
.block-container { padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1180px; }
@media (max-width: 640px) { .block-container { padding-left: 0.8rem; padding-right: 0.8rem; padding-top: 3.6rem; } }
.gs .gc-teams { font-size: 19px; font-weight: 650; }
.gs .gc-meta { color: var(--muted); font-size: 12.5px; margin: 1px 0 8px; }
.gs .gc-row { display: flex; gap: 8px; flex-wrap: wrap; align-items: baseline; font-size: 14px; margin: 3px 0; }
.gs .gc-row .lbl { color: var(--muted); width: 58px; flex: none; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
.gs .gc-gap { color: var(--ink); font-weight: 600; }
.gs .gc-none { color: var(--muted); font-size: 13px; font-style: italic; }
.gs .final { color: var(--ink2); font-size: 13px; }
.gs .callout { background: var(--surface); border: 1px solid var(--border); border-left: 4px solid var(--warn);
  border-radius: 0 12px 12px 0; padding: 12px 16px; margin: 4px 0 12px; }
.gs .callout h2 { margin: 0 0 6px; }
.gs .callout ol, .gs .callout ul { margin: 0; padding-left: 20px; }
.gs .callout li { margin: 4px 0; }
.gs .big { font-size: 26px; font-weight: 650; line-height: 1.2; }
.gs .kv { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin: 8px 0 4px; }
.gs .kv > div { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 10px 12px; min-width: 0; }
.gs .kv .k { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
.gs .kv .v { font-size: 21px; font-weight: 600; margin-top: 2px; overflow-wrap: anywhere; }
.gs .kv .n { color: var(--ink2); font-size: 12.5px; margin-top: 2px; }
.gs .readonly { display: inline-block; border: 1px solid var(--warn); color: var(--warn); border-radius: 999px;
  padding: 1px 10px; font-size: 12px; font-weight: 600; letter-spacing: .03em; vertical-align: middle; margin-left: 6px; }
"""


# ---- settings (sidebar widgets live in streamlit_app.py so they persist across pages) --------------------

def settings() -> dict:
    ss = st.session_state
    c = cfg()
    return {
        "season": ss.get("set_season") or current_season(),
        "week": None if ss.get("set_week") in (None, "Current") else int(ss["set_week"]),
        "sims": int(ss.get("set_sims") or c.sim["n_sims"]),
        "seed": int(ss["set_seed"]) if ss.get("set_seed") is not None else int(c.sim["seed"]),
        "players": bool(ss.get("set_players", True)),
    }


def current_season() -> int:
    from gridsim.data import current_season as cs
    return cs()


@st.cache_data(ttl=600, show_spinner=False)
def schedule_index() -> dict:
    """{season: [weeks]} and team list per season, from the cached nflverse schedule."""
    from gridsim.data import load_schedules
    s = load_schedules(cfg())
    out = {}
    for season in sorted(set(s["season"].to_list())):
        g = s.filter(s["season"] == season)
        out[int(season)] = {"weeks": sorted({int(w) for w in g["week"].to_list()}),
                            "teams": sorted(set(g["home_team"].to_list()) | set(g["away_team"].to_list()))}
    return out


@st.cache_data(ttl=600, show_spinner=False)
def slate(season: int | None, week: int | None) -> tuple[int, int, list[dict]]:
    return api.week_slate(cfg(), season, week)


# ---- runs and the result cache -------------------------------------------------------------------------

def make_request(home: str, away: str, season: int, week: int | None, st_: dict | None = None, **kw) -> api.RunRequest:
    """Same request the CLI builds for `gridsim run --home H --away A --season S --market-lines [--no-players]`."""
    st_ = st_ or settings()
    return api.RunRequest(home=home, away=away, season=season, week=week, sims=st_["sims"], seed=st_["seed"],
                          market_lines=True, use_players=st_["players"], **kw)


def req_key(req: api.RunRequest) -> str:
    # same key as the api's disk cache (config hash + overrides-file digest)
    return api.cache_path(req, cfg()).stem


def _results() -> dict:
    return st.session_state.setdefault("results", {})


def lookup(req: api.RunRequest) -> dict | None:
    """A finished result for this exact request, from this session or the api's disk cache (no simulation)."""
    k = req_key(req)
    res = _results()
    if k in res:
        return res[k]
    summ = api.cached_summary(req, cfg())
    if summ is not None:
        res[k] = summ
    return summ


def run(req: api.RunRequest, force: bool = False, bar=None, label: str | None = None, frac=(0.0, 1.0)) -> dict | None:
    """api.run_game with a Streamlit progress bar. force=True re-simulates (ignores the cache) and then stores
    the fresh result where the cache lookup finds it, so every page shows the newest numbers."""
    own_bar = bar is None
    label = label or f"{req.away} @ {req.home}"
    lo, hi = frac
    bar = bar or st.progress(0.0)
    bar.progress(lo, text=f"{label}: loading data / building model …")

    def cb(f):
        bar.progress(min(1.0, lo + (hi - lo) * f), text=f"{label}: simulating … {100 * f:.0f}%")

    try:
        summ = api.run_game(req, cfg(), progress=cb, use_cache=True, refresh=force)
    except (NameMatchError, ValueError) as e:
        if own_bar:
            bar.empty()
        st.error(str(e))
        return None
    except Exception as e:  # never crash the page on a data / network problem
        if own_bar:
            bar.empty()
        st.error(f"{label}: simulation failed — {type(e).__name__}: {e}")
        return None
    _results()[req_key(req)] = summ
    if own_bar:
        bar.empty()
    else:
        bar.progress(hi, text=f"{label}: done")
    return summ


# ---- roster names for the lineup editor (data layer only) --------------------------------------------

@st.cache_data(ttl=600, show_spinner=False)
def team_roster(season: int, team: str, week: int | None) -> list[dict]:
    """Latest weekly roster for a team on/before `week`: [{name, pos, status}] (skill positions first)."""
    from gridsim.availability import _load_source
    import polars as pl
    df = _load_source("rosters_weekly", season, cfg())
    if df.height == 0:
        return []
    t = df.filter((pl.col("team") == team) & (pl.col("week") <= (week or 99)))
    if t.height == 0:
        return []
    t = t.filter(pl.col("week") == t["week"].max())
    rows = [{"name": r["full_name"], "pos": r["position"] or "", "status": r["status"] or ""}
            for r in t.select(["full_name", "position", "status"]).iter_rows(named=True) if r["full_name"]]
    order = {p: i for i, p in enumerate(SKILL_POS)}
    rows.sort(key=lambda r: (order.get(r["pos"], 99), r["pos"], r["name"]))
    return rows


# ---- formatting ----------------------------------------------------------------------------------------

def pm_text(p: float, n: int) -> tuple[str, bool]:
    m = moe(p, n)
    return f"{100 * p:.1f}% ± {100 * m:.1f}", abs(p - 0.5) <= m


def market_spread(row: dict) -> float | None:
    return None if row.get("spread_line") is None else -float(row["spread_line"])


def spread_gap(row: dict, summ: dict | None) -> float | None:
    """Market spread minus sim fair spread (home perspective); > 0 = sim likes the home team more."""
    ms = market_spread(row)
    if summ is None or ms is None:
        return None
    return ms - float(summ["fair"]["spread_home"])


def game_card_html(row: dict, summ: dict | None) -> str:
    H, A = row["home_team"], row["away_team"]
    e = hr.esc
    meta = [hr.kickoff_text(row.get("gameday"), row.get("gametime"))]
    if row.get("location") == "Neutral":
        meta.append("neutral site")
    if row.get("roof"):
        meta.append(str(row["roof"]))
    head = (f'<div class="gc-teams">{hr.swatch(hr.C["away"])}{e(A)} <span class="muted">@</span> '
            f'{hr.swatch(hr.C["home"])}{e(H)}</div><div class="gc-meta">{e(" · ".join(m for m in meta if m))}</div>')
    if row.get("home_score") is not None:
        head += f'<div class="final">Final: {e(H)} {row["home_score"]} – {e(A)} {row["away_score"]}</div>'
    ms = market_spread(row)
    mk = []
    if ms is not None:
        mk.append(f"{e(H)} {e(hr.fmt_line(ms))}")
    if row.get("total_line") is not None:
        mk.append(f"O/U {float(row['total_line']):.1f}")
    if row.get("home_moneyline") is not None:
        mk.append(f'<span class="muted">ML {e(hr.fmt_ml(row["home_moneyline"]))} / {e(hr.fmt_ml(row["away_moneyline"]))}</span>')
    body = f'<div class="gc-row"><span class="lbl">Market</span>{" · ".join(mk) or "<span class=muted>no line yet</span>"}</div>'
    if summ:
        w, f = summ["win"], summ["fair"]
        fav = "home" if w["home"]["p"] >= w["away"]["p"] else "away"
        team = H if fav == "home" else A
        close = ' <span class="close">too close to call</span>' if w[fav].get("close") else ""
        body += (f'<div class="gc-row"><span class="lbl">Sim</span>{e(team)} {e(hr.fmt_pm(w[fav]))}{close}</div>'
                 f'<div class="gc-row"><span class="lbl">Fair</span>{e(H)} {e(hr.fmt_line(f["spread_home"]))} · '
                 f'total {f["total"]:.1f} <span class="muted">· {summ["n_sims"]:,} sims</span></div>')
        gap = spread_gap(row, summ)
        if gap is not None:
            tg = ""
            if row.get("total_line") is not None:
                d = float(f["total"]) - float(row["total_line"])
                tg = f' · total {d:+.1f}'
            body += (f'<div class="gc-row"><span class="lbl">Gap</span><span class="gc-gap">'
                     f'{e(hr.spread_gap_text(summ, gap))}</span><span class="muted">{e(tg)}</span></div>')
    else:
        body += '<div class="gc-row"><span class="lbl">Sim</span><span class="gc-none">not simulated at these settings</span></div>'
    return f'<div class="gs">{head}{body}</div>'


def file_freshness(season: int) -> list[str]:
    """Download times of the local data cache files (used when no simulated game is loaded yet)."""
    c = cfg()
    d = c.resolve_path(c.data["cache_dir"])
    names = [("Play-by-play", f"pbp_{season}.parquet"), ("Schedule", "schedules.parquet"),
             ("Rosters", f"rosters_weekly_{season}.parquet"), ("Injury report", f"injuries_{season}.parquet"),
             ("Depth charts", f"depth_charts_{season}.parquet")]
    out = []
    for label, fn in names:
        p = d / fn
        when = dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M") if p.exists() else "not downloaded"
        out.append(f"{label}: {when}")
    return out


def open_game(home: str, away: str, season: int, week: int | None) -> None:
    st.session_state["game_pick"] = {"home": home, "away": away, "season": season, "week": week}
    st.session_state["game_pick_new"] = True
    st.switch_page(GAME_PAGE)


# ---- shared building blocks for the analysis pages ------------------------------------------------------

CUSTOM = "__custom__"


def keep(name: str, default) -> str:
    """Session-state key for a widget whose value should survive page switches (see streamlit_app.py).
    Create the widget WITHOUT value=/index= and pass key=keep(...)."""
    k = KEEP + name
    if k not in st.session_state:
        st.session_state[k] = default
    return k


def pick_game(prefix: str, label: str | None = None) -> dict:
    """The Game page's matchup picker (slate games + "Any matchup …"), shared selection via
    session_state["game_pick"] so every page opens on the last game looked at."""
    s = settings()
    ss = st.session_state
    try:
        season, week, games = slate(s["season"], s["week"])
    except Exception:
        season, week, games = s["season"], s["week"], []
    by_id = {g["game_id"]: g for g in games}
    pick = ss.get("game_pick")
    default_id = None
    if pick:
        default_id = next((g["game_id"] for g in games if g["home_team"] == pick["home"] and g["away_team"] == pick["away"]
                           and pick.get("season") == season), CUSTOM)
    options = list(by_id) + [CUSTOM]

    def _label(gid):
        if gid == CUSTOM:
            return "Any matchup …"
        g = by_id[gid]
        return f"{g['away_team']} @ {g['home_team']} · {hr.kickoff_text(g.get('gameday'), g.get('gametime'))}"

    key = f"{prefix}_choice"
    if ss.get(key) not in options:
        ss[key] = default_id if default_id in options else options[0]
    choice = st.selectbox(label or f"Game ({season} week {week})", options, format_func=_label, key=key)
    neutral = playoff = False
    game = None
    if choice == CUSTOM:
        teams = schedule_index().get(season, {}).get("teams") or sorted({g["home_team"] for g in games})
        c1, c2, c3, c4 = st.columns([2, 2, 1, 1], vertical_alignment="bottom")
        ph = pick.get("home") if pick and default_id == CUSTOM else None
        pa = pick.get("away") if pick and default_id == CUSTOM else None
        if ss.get(f"{prefix}_away") not in teams:
            ss[f"{prefix}_away"] = pa if pa in teams else teams[0]
        if ss.get(f"{prefix}_home") not in teams:
            ss[f"{prefix}_home"] = ph if ph in teams else teams[1]
        away = c1.selectbox("Away", teams, key=f"{prefix}_away")
        home = c2.selectbox("Home", teams, key=f"{prefix}_home")
        neutral = c3.toggle("Neutral", key=f"{prefix}_neutral")
        playoff = c4.toggle("Playoff", key=f"{prefix}_playoff")
        gweek = None
        if home == away:
            st.warning("Pick two different teams.")
            st.stop()
    else:
        game = by_id[choice]
        home, away, gweek = game["home_team"], game["away_team"], week
    ss["game_pick"] = {"home": home, "away": away, "season": season, "week": gweek}
    return {"home": home, "away": away, "season": season, "week": gweek, "slate_week": week, "neutral": neutral,
            "playoff": playoff, "game": game}


def page_header(title: str, sub: str = "") -> None:
    st.html(f'<div class="gs"><h1>{hr.esc(title)}</h1>' + (f'<p class="sub">{sub}</p>' if sub else "") + "</div>")


def callout(html_body: str, tone: str = "warn") -> None:
    """Highlighted card (left accent bar). tone: warn | home | good."""
    color = {"warn": hr.C["warn"], "home": hr.C["home"], "good": hr.C["good"], "away": hr.C["away"]}.get(tone, tone)
    st.html(f'<div class="gs"><div class="callout" style="border-left-color:{color}">{html_body}</div></div>')


class _Tee:
    """stdout tee that mirrors the library's progress prints into a Streamlit placeholder."""

    def __init__(self, placeholder, keep_lines: int = 6):
        self.ph, self.keep_lines, self.buf, self.lines = placeholder, keep_lines, "", []

    def write(self, s):
        try:
            sys.__stdout__.write(s)
        except Exception:
            pass
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                self.lines.append(line.rstrip())
        try:
            if self.lines:
                self.ph.code("\n".join(self.lines[-self.keep_lines:]), language=None)
        except Exception:      # printed from a thread without a Streamlit context
            pass
        return len(s)

    def flush(self):
        pass


def capture_output(placeholder, keep_lines: int = 6):
    """with capture_output(st.empty()): long_library_call()  -> shows the last printed lines live."""
    import contextlib
    return contextlib.redirect_stdout(_Tee(placeholder, keep_lines))


def stat_figure(hist: dict, line: float | None, xlabel: str, height: int = 300):
    """Per-sim distribution of one player stat (values/counts), split over / under / push at `line`."""
    import plotly.graph_objects as go
    C = hr.C
    n = sum(hist["counts"]) or 1
    fig = go.Figure()
    hover = f"{xlabel} %{{x}}<br>%{{y:.1f}}% of sims (%{{customdata}})<extra></extra>"
    groups = {"u": ([], [], []), "o": ([], [], []), "p": ([], [], [])}
    for v, c in zip(hist["values"], hist["counts"]):
        key = "p" if line is not None and v == line else ("o" if line is not None and v > line else "u")
        g = groups[key]
        g[0].append(v); g[1].append(100 * c / n); g[2].append(c)  # noqa: E702
    if line is None:
        hr._bars(fig, *groups["u"], xlabel, C["home"], hover)
        fig.update_layout(showlegend=False)
    else:
        hr._bars(fig, *groups["u"], f"Under {line:g}", C["under"], hover)
        hr._bars(fig, *groups["o"], f"Over {line:g}", C["over"], hover)
        hr._bars(fig, *groups["p"], "Push", C["neutral"], hover)
    hr._layout(fig, xlabel, height)
    if line is not None:
        hr._line_marker(fig, float(line), f"{line:g}")
    return fig


def percentile_figure(stats: dict, line: float | None, xlabel: str, height: int = 170):
    """Fallback when no per-sim histogram is stored: the five stored percentiles as a range strip."""
    import plotly.graph_objects as go
    C = hr.C
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[stats["p10"], stats["p90"]], y=[0, 0], mode="lines", line=dict(color=C["axis"], width=10),
                             hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(x=[stats["p25"], stats["p75"]], y=[0, 0], mode="lines", line=dict(color=C["home"], width=18),
                             hoverinfo="skip", showlegend=False))
    xs = [stats[k] for k in ("p10", "p25", "p50", "p75", "p90")]
    fig.add_trace(go.Scatter(x=xs, y=[0] * 5, mode="markers+text", text=["p10", "p25", "p50", "p75", "p90"],
                             textposition="top center", marker=dict(color=C["ink"], size=9), showlegend=False,
                             textfont=dict(color=C["muted"], size=11), hovertemplate="%{text}: %{x:.1f}<extra></extra>"))
    hr._layout(fig, xlabel, height)
    fig.update_yaxes(visible=False, range=[-1, 1.4])
    if line is not None:
        hr._line_marker(fig, float(line), f"{line:g}")
    return fig


def calibration_figure(cal: list[dict], title_x: str = "Predicted home win %", height: int = 320):
    """Calibration bins [{pred, actual, n}] vs the diagonal (palette of the report charts)."""
    import plotly.graph_objects as go
    C = hr.C
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[0, 100], y=[0, 100], mode="lines", line=dict(dash="dot", color=C["muted"], width=1),
                             name="perfect", hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=[100 * c["pred"] for c in cal], y=[100 * c["actual"] for c in cal], mode="markers+lines",
                             marker=dict(size=[max(7, min(22, 4 + c["n"] ** 0.5 * 1.6)) for c in cal], color=C["home"]),
                             line=dict(color=C["home"], width=2), name="sim",
                             customdata=[[c.get("bin", ""), c["n"]] for c in cal],
                             hovertemplate="bin %{customdata[0]}<br>predicted %{x:.1f}%<br>actual %{y:.1f}%"
                                           "<br>n=%{customdata[1]}<extra></extra>"))
    hr._layout(fig, title_x, height)
    fig.update_yaxes(title=dict(text="Actual home win %"), range=[0, 100])
    fig.update_xaxes(range=[0, 100], showgrid=True, ticksuffix="%")
    return fig


def tvalue_figure(components: list[dict], height: int | None = None):
    """Component bias in standard errors (sim − actual); flagged components (|bias| > 2 SE) in the warm color."""
    import plotly.graph_objects as go
    from helpers import is_flag
    C = hr.C
    comp = list(components)
    fig = go.Figure(go.Bar(
        x=[c["t"] for c in comp], y=[c["component"] for c in comp], orientation="h",
        marker=dict(color=[C["away"] if is_flag(c.get("flag")) else C["home"] for c in comp], line=dict(width=0)),
        customdata=[[c["sim"], c["actual"], c.get("rel_bias", 0) * 100] for c in comp],
        hovertemplate="%{y}<br>%{x:+.1f} SE<br>sim %{customdata[0]:.3g} vs actual %{customdata[1]:.3g}"
                      " (%{customdata[2]:+.1f}%)<extra></extra>"))
    hr._layout(fig, "Bias in standard errors (sim − actual)", height or max(260, 22 * len(comp) + 80))
    fig.update_yaxes(title=None, ticksuffix="", autorange="reversed", tickfont=dict(color=C["ink2"]))
    fig.update_xaxes(showgrid=True, zeroline=True, zerolinecolor=C["axis"])
    for x in (-2, 2):
        fig.add_vline(x=x, line=dict(color=C["muted"], width=1, dash="dot"))
    fig.update_layout(margin=dict(l=8, r=16, t=24, b=44), showlegend=False)
    return fig


def mtime_text(p: Path) -> str:
    return dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M") if p.exists() else "—"


def kv_html(items: list[tuple]) -> str:
    """Tile row: [(label, value_html, note_html?), ...] (values are already-escaped HTML)."""
    cells = []
    for it in items:
        k, v = it[0], it[1]
        n = it[2] if len(it) > 2 and it[2] else ""
        cells.append(f'<div><div class="k">{hr.esc(k)}</div><div class="v">{v}</div>'
                     + (f'<div class="n">{n}</div>' if n else "") + "</div>")
    return f'<div class="gs"><div class="kv">{"".join(cells)}</div></div>'


def pm_span(p: float, m: float) -> str:
    """'54.2% ± 2.1' plus the too-close flag when within the MOE of 50%."""
    return hr.pm_html({"p": p, "moe": m, "close": abs(p - 0.5) <= m})
