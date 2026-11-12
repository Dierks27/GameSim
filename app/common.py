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
POSITIONS_PAGE = "pages/positions.py"
SCORECARD_PAGE = "pages/scorecard.py"
DIAGNOSE_PAGE = "pages/diagnose.py"
SETTINGS_PAGE = "pages/settings.py"
WEEKLY_PAGE = "pages/weekly.py"
KEEP = "p_"   # widget keys starting with this survive page switches (re-asserted in streamlit_app.py)


def cfg():
    """config.yaml is re-read on every rerun (cheap) so edits show up without restarting."""
    return load_config()


def via_app(page: str) -> None:
    """Call at the top of every page. A page opened by its own address before the app has run once (a browser tab
    left open while GridSim was restarted) is served by Streamlit on its own: no styling, no menu, no Settings box.
    Send it through the app, which then opens this page properly."""
    if st.session_state.get("_via_app"):
        return
    st.session_state["_goto"] = page
    try:
        st.switch_page("streamlit_app.py")
    except Exception as e:                 # (a control-flow rerun is a BaseException and passes through)
        if type(e).__name__ != "StreamlitAPIException":
            raise
        inject_css()                       # not reachable through the app (a test harness): at least the styling


def inject_css() -> None:
    st.html(f"<style>{hr.TOKENS}{hr.COMPONENT_CSS}{_APP_CSS}</style>")


_APP_CSS = """
.block-container { padding-top: 2.2rem; padding-bottom: 3rem; max-width: none; padding-left: 2.2rem; padding-right: 2.2rem; }
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
.gs .kv .v small { font-size: 14px; color: var(--ink2); font-weight: 400; }
.gs .kv .n { color: var(--ink2); font-size: 12.5px; margin-top: 2px; }
.gs.lastleft td:last-child, .gs.lastleft th:last-child, .gs .lastleft td:last-child, .gs .lastleft th:last-child { text-align: left; white-space: normal; min-width: 220px; }
/* Tables: always as wide as the page, never wider. Text wraps inside its cell, numbers stay on one line, long
   tables scroll up and down under a fixed header. In a narrow window the least important columns drop out, and on a
   phone every row becomes a small card (label: value), so nothing ever has to be scrolled sideways. */
.gs .tbl { border: 1px solid var(--border); border-radius: 10px; background: var(--surface); overflow-x: auto; overflow-y: auto; margin: 6px 0 10px; }
.gs .tbl table { width: 100%; table-layout: auto; font-size: 13.5px; }
.gs .tbl th, .gs .tbl td { padding: 5px 9px; white-space: normal; overflow-wrap: anywhere; vertical-align: top; text-align: left; }
.gs .tbl th { font-size: 11.5px; text-transform: uppercase; letter-spacing: .03em; z-index: 1; vertical-align: bottom;
  overflow-wrap: normal; }
.gs .tbl td.t { min-width: 8em; }
.gs .tbl td.l, .gs .tbl th.l { white-space: nowrap; text-align: right; min-width: 0; }
.gs .tbl th[title] { cursor: help; text-decoration: underline dotted var(--muted); text-underline-offset: 3px; }
.gs .tbl td.n, .gs .tbl th.n { text-align: right; white-space: nowrap; }
.gs .tbl td.n { white-space: pre; }
.gs .tbl td.c, .gs .tbl th.c { text-align: center; white-space: nowrap; }
.gs .tbl td.k { white-space: nowrap; min-width: 0; }
.gs .tbl td.pos { color: #0ca30c; font-weight: 600; }
.gs .tbl td.neg { color: var(--ink2); }
.gs .tbl tr:last-child td { border-bottom: 0; }
.gs .tbl a { color: var(--home); text-decoration: none; }
.gs .tbl-note { color: var(--muted); font-size: 12.5px; margin: -4px 0 10px; }
@media (max-width: 1500px) { .gs .tbl .w2 { display: none; } }
@media (max-width: 1150px) { .gs .tbl .w3 { display: none; } }
@media (max-width: 900px) {
  .gs .tbl { max-height: none !important; }
  .gs .tbl table, .gs .tbl tbody, .gs .tbl tr, .gs .tbl td { display: block; width: auto; }
  .gs .tbl thead { display: none; }
  .gs .tbl tr { padding: 7px 10px; border-bottom: 1px solid var(--grid); }
  .gs .tbl tr:last-child { border-bottom: 0; }
  .gs .tbl td, .gs .tbl td.n, .gs .tbl td.c, .gs .tbl td.k, .gs .tbl td.l, .gs .tbl td.t { display: flex; justify-content: space-between; gap: 14px; border: 0; padding: 1px 0;
    text-align: right; white-space: normal; font-size: 13.5px; overflow-wrap: anywhere; min-width: 0; }
  .gs .tbl td::before { content: attr(data-label); color: var(--muted); font-size: 11px; text-transform: uppercase;
    letter-spacing: .03em; flex: none; text-align: left; padding-top: 2px; }
  .gs .tbl td:first-child { font-weight: 600; }
  .gs .tbl td.w1, .gs .tbl td.w2, .gs .tbl td.w3 { display: flex; }
  .gs .tbl td.empty { display: none; }
}
/* the report's own tables (alt lines, team stats, players ...): let them wrap instead of scrolling sideways */
.gs .scroll { overflow-x: auto; }
.gs .scroll td, .gs .scroll th { white-space: normal; }
.gs .scroll td, .gs .scroll th { overflow-wrap: anywhere; }
@media (max-width: 1150px) { .gs .scroll th, .gs .scroll td { padding: 5px 6px; font-size: 13px; } .gs .tbl th, .gs .tbl td { padding: 5px 6px; } }
@media (max-width: 900px) { .gs .scroll th, .gs .scroll td { padding: 4px 5px; font-size: 12.5px; overflow-wrap: anywhere; } }
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


def _nice_width(span: float, target_bins: int = 45) -> int:
    raw = span / target_bins
    for w in (1, 2, 5, 10, 20, 25, 50, 100):
        if raw <= w:
            return w
    return 100


def stat_figure(hist: dict, line: float | None, xlabel: str, height: int = 300):
    """Per-sim distribution of one player stat (values/counts), split over / under / push at `line`.
    Wide stats (yards) are drawn in bins anchored at the line so no bar straddles it; the over / under
    numbers on the page are always counted from the raw per-sim values."""
    import math
    import plotly.graph_objects as go
    C = hr.C
    vals, cnts = hist["values"], hist["counts"]
    n = sum(cnts) or 1
    vmin, vmax = (min(vals), max(vals)) if vals else (0, 0)
    w = _nice_width(vmax - vmin)
    fig = go.Figure()
    groups = {"u": {}, "o": {}, "p": {}}
    for v, c in zip(vals, cnts):
        if line is None:
            key, x, lab = "u", v, f"{v}"
            if w > 1:
                b = math.floor(v / w)
                x, lab = (b + 0.5) * w, f"{b * w}–{b * w + w - 1}"
        elif v == line:
            key, x, lab = "p", v, f"{v:g}"
        elif w == 1:
            key, x, lab = ("o" if v > line else "u"), v, f"{v}"
        else:
            k = math.ceil(abs(v - line) / w) - 1
            key = "o" if v > line else "u"
            x = line + (k + 0.5) * w if key == "o" else line - (k + 0.5) * w
            if key == "o":      # (line + k*w, line + (k+1)*w]
                lo, hi = math.floor(line + k * w) + 1, math.floor(line + (k + 1) * w)
            else:               # [line - (k+1)*w, line - k*w)
                lo, hi = math.ceil(line - (k + 1) * w), math.ceil(line - k * w) - 1
            lo, hi = max(lo, vmin), min(hi, vmax)
            lab = f"{lo}–{hi}" if hi > lo else f"{lo}"
        d = groups[key].setdefault(x, [0, lab])
        d[0] += c
    hover = f"{xlabel} %{{customdata[0]}}<br>%{{y:.1f}}% of sims (%{{customdata[1]}})<extra></extra>"

    def add(key, name, color):
        g = groups[key]
        xs = sorted(g)
        hr._bars(fig, xs, [100 * g[x][0] / n for x in xs], [[g[x][1], g[x][0]] for x in xs], name, color, hover)
    if line is None:
        add("u", xlabel, C["home"])
        fig.update_layout(showlegend=False)
    else:
        add("u", f"Under {line:g}", C["under"])
        add("o", f"Over {line:g}", C["over"])
        add("p", "Push", C["neutral"])
    hr._layout(fig, xlabel + (f" (bins of {w})" if w > 1 else ""), height)
    if w > 1:
        fig.update_traces(width=w * 0.86)
        fig.update_layout(bargap=0.05)
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
    fig.update_yaxes(title=None, ticksuffix="", autorange="reversed", tickfont=dict(color=C["ink2"]), automargin=True,
                     showgrid=False)
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


def pm_value(p: float, m: float) -> str:
    """Tile value '48.7% ± 5.7' (the ± part small)."""
    return f"{100 * p:.1f}%<small> ± {100 * m:.1f}</small>"


def close_note(p: float, m: float, extra: str = "") -> str:
    """Tile note: the too-close flag when p is within its MOE of 50%, then `extra`."""
    flag = f'<span class="close">{hr.CLOSE_TEXT}</span>' if abs(p - 0.5) <= m else ""
    return " · ".join(x for x in (flag, extra) if x)


def pm_span(p: float, m: float) -> str:
    """'54.2% ± 2.1' plus the too-close flag when within the MOE of 50%."""
    return hr.pm_html({"p": p, "moe": m, "close": abs(p - 0.5) <= m})


MARKET_COLORS = {"moneyline": hr.C["home"], "spread": hr.C["away"], "total": hr.C["over"]}


def signal_history_figures(rows: list[dict], height: int = 300):
    """From graded signals (signals/graded.csv rows: ts_utc, market, pnl, clv): cumulative flat-unit P&L and the
    running average closing-line value, one line per market type. Pure presentation of the ledger."""
    import plotly.graph_objects as go
    rows = sorted((r for r in rows if r.get("ts_utc")), key=lambda r: str(r["ts_utc"]))
    pnl_fig, clv_fig = go.Figure(), go.Figure()
    for mt in sorted({r["market"] for r in rows}):
        xs, cum, run_clv = [], [], []
        tot, csum, cn = 0.0, 0.0, 0
        for r in rows:
            if r["market"] != mt:
                continue
            tot += float(r.get("pnl") or 0.0)
            if r.get("clv") not in (None, ""):
                csum += float(r["clv"])
                cn += 1
            xs.append(str(r["ts_utc"])[:16].replace("T", " "))
            cum.append(tot)
            run_clv.append(100 * csum / cn if cn else None)
        color = MARKET_COLORS.get(mt, hr.C["under"])
        pnl_fig.add_trace(go.Scatter(x=xs, y=cum, mode="lines+markers", name=mt, line=dict(color=color, width=2),
                                     marker=dict(size=5), hovertemplate=f"{mt}<br>%{{x}}<br>%{{y:+.2f}} units<extra></extra>"))
        clv_fig.add_trace(go.Scatter(x=xs, y=run_clv, mode="lines+markers", name=mt, line=dict(color=color, width=2),
                                     marker=dict(size=5), connectgaps=True,
                                     hovertemplate=f"{mt}<br>%{{x}}<br>avg CLV %{{y:+.2f}}¢<extra></extra>"))
    for fig, yt in ((pnl_fig, "Cumulative P&L (units)"), (clv_fig, "Average CLV (¢ per contract)")):
        hr._layout(fig, "Signal time (UTC)", height)
        fig.update_yaxes(title=dict(text=yt), ticksuffix="", rangemode="normal", zeroline=True, zerolinecolor=hr.C["axis"])
        fig.update_layout(hovermode="x unified")
    return pnl_fig, clv_fig


# ---- Kalshi board: the same table on Game, This Week, Signals and the parlay page ---------------------------------

PRICE_FILTERS = {"all": "All prices", "under50": "Under 50¢ (long shots)", "over50": "50¢ and up"}
RATING_LABEL = {"signal": "🟢 Signal", "unproven": "🟡 Unproven", "catch": "🟠 Catch", "lean": "⚪ Lean",
                "small": "▫️ Small", "none": "—"}
EDGE_RATINGS = ("signal", "unproven", "catch")
MARKET_LABEL = {"moneyline": "Who wins", "spread": "Spread", "total": "Total points"}
BOARD_HELP = {
    "rating": "🟢 Signal: an adjusted edge that passes every rule. 🟡 Unproven: the same, without a proven record yet. "
              "🟠 Catch: an adjusted edge with a problem (see the last column). ⚪ Lean: no edge after adjusting, but "
              "GridSim alone rates it well above its cost.",
    "you buy": "What has to happen for the contract to pay $1.",
    "costs": "Kalshi's price to buy it now, plus Kalshi's fee.",
    "pays": "What you get back for every $1 put in if it wins (your $1 included).",
    "market": "The market's own chance for it: the middle of Kalshi's buy and sell price.",
    "GridSim": "How often it won in GridSim's simulations, ± its margin of error.",
    "adjusted": "The best estimate: the market's chance, moved toward GridSim's by as much as GridSim's "
                "disagreements have been worth in the past.",
    "edge": "Adjusted chance minus the cost, in points. Positive = worth more than it costs.",
    "lean": "GridSim's own chance minus the cost: the edge there would be if GridSim alone were right. Its opinion, "
            "not a proven edge.",
    "catch": "Why to be careful with this row: a thin market, too few sims, or far from the market.",
}


@st.cache_data(ttl=120, show_spinner="Reading Kalshi prices …")
def _kalshi_contracts(game_id: str, home: str, away: str, gameday: str | None) -> dict:
    from gridsim.markets import Kalshi, game_markets
    k = Kalshi(cfg())
    k.timeout = min(k.timeout, 6.0)          # someone is waiting on the page: do not hang for the full timeout
    return game_markets(k, {"home_team": home, "away_team": away, "gameday": gameday})


def kalshi_contracts(game_id: str, home: str, away: str, gameday: str | None) -> dict:
    """Kalshi's open contracts for one game, read now (a good answer is kept for two minutes). {"error": text}
    when Kalshi can't be reached — not kept, so the next look tries again."""
    try:
        return _kalshi_contracts(game_id, home, away, gameday)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


kalshi_contracts.clear = _kalshi_contracts.clear          # "Refresh prices" buttons


@st.cache_data(ttl=3600, show_spinner=False)
def _week_board(season: int | None, week: int | None, stamp: str, cfg_hash: str) -> dict:
    from gridsim import scanner
    return scanner.week_board(cfg(), season, week)


def week_board(season: int | None, week: int | None) -> dict:
    """Every upcoming game's contracts as of the last price check (see scanner.week_board). Re-read only when a new
    price snapshot, a new sim or a new grading exists."""
    from gridsim import scanner
    c = cfg()
    try:
        s_, w_, _ = slate(season, week)
        snap = scanner.latest_snapshot(c, s_, w_)
        cache = c.resolve_path("out/cache")
        newest = max((p.stat().st_mtime for p in cache.glob("*.json")), default=0) if cache.exists() else 0
        graded = c.resolve_path("signals") / "graded.csv"
        from gridsim.markets import blend_weight
        # (market_blend is left out of the config hash — it never changes a sim — but it changes every rating here)
        stamp = (f"{snap.name if snap else ''}|{newest}|{graded.stat().st_mtime if graded.exists() else 0}"
                 f"|w{blend_weight(c)}")
        # the minute is part of the key near kickoffs only through the snapshot name; started games drop out at the next check
        return _week_board(season, week, stamp, c.hash)
    except Exception as e:
        return {"ts_utc": None, "season": season, "week": week, "games": {}, "error": f"{type(e).__name__}: {e}"}


def board_empty_text(wb: dict, scan_hint: str) -> str:
    """Why the week has nothing to list from Kalshi, in plain words. Cases that look alike on screen: no price check
    yet, prices saved but the games not simulated at the standard settings, and a week with no game left."""
    games = list((wb.get("games") or {}).values())
    if wb.get("error"):
        return f"The Kalshi prices of this week could not be read ({wb['error']})."
    if not games:
        return ("Every game of this week has already kicked off, so there is nothing left to compare with Kalshi's "
                "prices. Set **Week** in the sidebar to **Current** for the coming games.")
    if not wb.get("ts_utc"):
        return ("No Kalshi prices saved for this week yet. GridSim fetches them by itself within a few minutes of "
                f"starting — or press {scan_hint}.")
    try:
        when = dt.datetime.fromisoformat(wb["ts_utc"]).astimezone().strftime("%a %b %d, %I:%M %p").replace(" 0", " ")
    except (TypeError, ValueError):
        when = "the last price check"
    waiting = sum("not simulated" in (g.get("note") or "") for g in games)
    if not waiting:
        return (f"The last price check ({when}) found no Kalshi prices for the coming games. GridSim looks again at "
                f"the next price check — or press {scan_hint}.")
    return (f"Kalshi's prices were saved at {when}, but {waiting} of the {len(games)} coming games have not been "
            "simulated yet with the newest data and settings, so there is nothing to compare them with. The next "
            f"price check simulates them by itself — or press {scan_hint}.")


def remember_display(name: str, value) -> None:
    """Keep a display choice for next time (config.local.yaml, section `display`; never part of the config hash)."""
    from gridsim.config import load_local, save_local
    try:
        local = load_local()
        if (local.get("display") or {}).get(name) == value:
            return
        local.setdefault("display", {})[name] = value
        save_local(local, backup=False)
    except Exception:
        pass            # a read-only settings file must not break a page


def price_filter(where: str = "") -> str:
    """The "which prices" choice, shared by every page that lists contracts and remembered between sessions."""
    ss = st.session_state
    start = (cfg().raw.get("display") or {}).get("price_filter", "all")
    key = keep("price_filter", start if start in PRICE_FILTERS else "all")
    if ss[key] not in PRICE_FILTERS:
        ss[key] = "all"

    def _saved():
        remember_display("price_filter", ss[key])

    return st.radio("Show contracts priced", list(PRICE_FILTERS), format_func=PRICE_FILTERS.get, horizontal=True,
                    key=key, on_change=_saved,
                    help="A contract pays $1 if it wins. One that costs under 50¢ has to win less than half the "
                         "time to break even and pays more than double. GridSim remembers this choice.")


def in_band(row: dict, band: str) -> bool:
    """By what you would pay (price plus fee), the number the "costs" column shows."""
    return band == "all" or (row["cost"] < 0.5) == (band == "under50")


def _pct1(p: float) -> str:
    """41% / 30.5%: the half point is kept (Kalshi's middle price is very often x.5) so the columns add up."""
    return f"{100 * p:.1f}".rstrip("0").rstrip(".") + "%"


def board_table(rows: list[dict], game: bool = False) -> list[dict]:
    """Board rows (scanner.game_board) as plain table rows (see board())."""
    from gridsim.markets import blend_weight
    adj = blend_weight(cfg()) > 0            # with weight 0 the adjusted chance IS the market's: no second column
    out = []
    for r in rows:
        d = {"rating": RATING_LABEL.get(r["rating"], r["rating"]) + (" (strong)" if r.get("confidence") == "strong" else "")}
        if game:
            d["game"] = r.get("matchup")
        d.update({"you buy": r["what"], "costs": 100 * r["cost"], "pays": r["pays"], "market": _pct1(r["p_market"]),
                  "GridSim": f"{100 * r['p_side']:.0f}% ±{100 * (r.get('moe') or 0):.0f}"})
        if adj:
            d["adjusted"] = _pct1(r["p_adj"])
        d.update({"edge": 100 * r["edge"], "lean": 100 * r["lean"], "catch": "; ".join(r.get("catches") or []),
                  "Kalshi": r.get("url")})
        out.append(d)
    return out


def board(rows: list[dict], game: bool = False, height: int | None = 640) -> None:
    """The contract table of the Game, Signals and parlay pages: fits the page, no sideways scrolling."""
    table(board_table(rows, game=game), help=BOARD_HELP, link={"Kalshi": "open ↗"}, right=("GridSim", "market", "adjusted"),
          nowrap=("rating", "game"),
          fmt={"costs": "%.0f¢", "pays": "$%.2f", "edge": "%+.1f", "lean": "%+.1f"},
          sign=("edge", "lean"), hide={"pays": 2, "market": 3} if game else {"pays": 3},
          height=height if len(rows) > 16 else None)


def blend_note() -> None:
    """How the numbers are adjusted, with the evidence — said once per page that lists contracts."""
    from gridsim.markets import blend_weight
    w = blend_weight(cfg())
    if w <= 0:
        body = ("<b>How the numbers are adjusted.</b> When GridSim and the market disagree, the question is who is "
                "usually right. Replaying past seasons with the closing betting line next to GridSim's number, "
                "GridSim's disagreement did <b>not</b> predict results: the market's number alone did best. So the "
                "<b>adjusted</b> chance is the market's own, and GridSim's number is shown as its <b>lean</b> — an "
                "opinion, not an edge. After fees, the <b>edge</b> of a contract is then slightly negative: the cost of "
                "trading. If GridSim's graded record on Kalshi starts to show that its leans come true, the weight "
                "can be raised (Settings → Config → market_blend) and edges will appear here.")
    else:
        body = (f"<b>How the numbers are adjusted.</b> The <b>adjusted</b> chance is the market's own, moved "
                f"{100 * w:.0f}% of the way toward GridSim's — the weight set under Settings → Config → market_blend. "
                "(Measured on four replayed seasons, GridSim's disagreements with the market were worth 0%; a higher "
                "weight is a choice, not a measurement.) <b>Edge</b> = adjusted chance − cost. <b>Lean</b> = what the "
                "edge would be if GridSim alone were right.")
    callout(body, tone="home")


def longshot_note(season: int | None = None) -> None:
    """What to know about contracts under 50¢ — facts and GridSim's own record there, not advice."""
    from gridsim import scanner
    rec = None
    try:
        rec = next((r for r in scanner.record_by_price(cfg(), season) if r["price paid"] == "all under 50¢"), None)
    except Exception:
        pass
    if rec:
        clv = "" if rec["avg CLV"] is None else (f"; Kalshi's price then moved {abs(100 * rec['avg CLV']):.1f}¢ "
                                                 f"{'toward' if rec['avg CLV'] >= 0 else 'away from'} GridSim's side by kickoff")
        line = (f"GridSim's record on the under-50¢ contracts it leaned to so far: {rec['contracts']} contracts in {rec['games']} games, "
                f"{rec['won %']:.0f}% won ({rec['needed %']:.0f}% was needed to break even){clv}.")
    else:
        line = "GridSim has no graded record yet on under-50¢ contracts it leaned to; it builds up as weeks are graded."
    callout("<b>Long shots — the arithmetic</b><ul>"
            "<li>A 30¢ contract has to win about 1 time in 3 just to break even. Most of them lose; the few that win "
            "pay for the rest, or don't.</li>"
            "<li>The <b>market</b> column is the best estimate of how often each one wins. Where GridSim's number is "
            "higher, that is GridSim's lean — and on long shots a model that knows less than the market leans too "
            "high more often than not, which is why the adjusted number does not follow it.</li>"
            f"<li>{hr.esc(line)}</li></ul>", tone="warn")


@st.cache_data(ttl=3600, show_spinner="Putting parlays together …")
def _auto_parlays(season: int | None, week: int | None, band: str, max_legs: int, stamp: str) -> dict:
    from gridsim import parlay
    wb = week_board(season, week)
    games = {gid: {**g, "rows": [r for r in (g.get("rows") or []) if in_band(r, band)]} for gid, g in wb["games"].items()}
    return parlay.auto_parlays({"games": games}, max_legs=max_legs)


def auto_parlays(season: int | None, week: int | None, band: str, max_legs: int) -> dict:
    """Auto parlays from the last price check's board (see gridsim.parlay); recomputed when the board changes."""
    import hashlib
    import json
    wb = week_board(season, week)
    # the board itself is the key: a re-simulated game or a changed weight changes the numbers without a new price check
    stamp = hashlib.sha1(json.dumps(wb["games"], sort_keys=True, default=str).encode()).hexdigest()
    return _auto_parlays(season, week, band, max_legs, stamp)


# ---- tables that fit ----------------------------------------------------------------------------------------------

def _cell(v, f) -> tuple[str, bool]:
    """(text, is a number) for one value; f is a printf format ("%+.1f", "$%.2f", "%.0f%%") or a function."""
    import math
    if v is None or (isinstance(v, float) and math.isnan(v)) or v == "":
        return "—", False
    if isinstance(v, bool):
        return ("yes" if v else "no"), False
    num = isinstance(v, (int, float))
    if callable(f):
        return str(f(v)), num
    if f and num:
        try:
            return f % v, True
        except (TypeError, ValueError):
            pass
    if isinstance(v, float):
        return (f"{v:,.0f}" if abs(v) >= 1000 else f"{v:.2f}"), True
    if isinstance(v, int):
        return (f"{v:,}" if abs(v) >= 10000 else str(v)), True
    return str(v), False


def table_html(data, fmt: dict | None = None, labels: dict | None = None, help: dict | None = None,
               link: dict | None = None, hide: dict | None = None, center: tuple = (), sign: tuple = (),
               right: tuple = (), nowrap: tuple = (), height: int | None = None, cols: list[str] | None = None) -> str:
    """A table that always fits the page: text wraps inside its cell, numbers stay on one line, nothing scrolls
    sideways. data: list of dicts (or a polars DataFrame).
      fmt     {column: printf format or function}        labels  {column: shorter heading}
      help    {column: what it means}  (shown when the mouse rests on the heading)
      link    {column: link text}  (the value is the address)
      hide    {column: 2 | 3}  drop the column in a narrow window (2: below about 1500 px, 3: below about 1150 px;
              on a phone every row becomes a card and shows everything)
      center  columns centred (icons);  right  text columns set like numbers;  nowrap  short text columns that
      stay on one line (a rating, a matchup);  sign  columns coloured by sign
      (positive green);  height  max px, then it
      scrolls up and down under its heading;  cols  which columns, in which order (default: all, as they come)."""
    if hasattr(data, "to_dicts"):
        data = data.to_dicts()
    data = list(data or [])
    if not data:
        return ""
    fmt, labels, help, link, hide = fmt or {}, labels or {}, help or {}, link or {}, hide or {}
    names = cols or list(dict.fromkeys(k for r in data for k in r))
    e = hr.esc
    numeric = {c: all(isinstance(r.get(c), (int, float)) and not isinstance(r.get(c), bool) or r.get(c) in (None, "")
                      for r in data) and any(isinstance(r.get(c), (int, float)) for r in data) for c in names}

    def cls(c: str, extra: str = "") -> str:
        k = "l" if c in link else ("c" if c in center else ("n" if numeric[c] or c in right else "t"))
        w = (f" w{hide[c]}" if c in hide else "") + (" k" if c in nowrap else "")
        return f' class="{k}{w}{extra}"'

    head = "".join(f"<th{cls(c)}" + (f' title="{e(help[c])}"' if c in help else "") + f">{e(labels.get(c, c))}</th>"
                   for c in names)
    body = []
    for r in data:
        tds = []
        for c in names:
            v = r.get(c)
            if c in link:
                txt = f'<a href="{e(str(v))}" target="_blank" rel="noopener">{e(link[c])}</a>' if v else "—"
                tds.append(f'<td{cls(c, "" if v else " empty")} data-label="{e(labels.get(c, c))}">{txt}</td>')
                continue
            txt, _ = _cell(v, fmt.get(c))
            extra = ""
            if c in sign and isinstance(v, (int, float)) and not isinstance(v, bool):
                extra = " pos" if v > 0 else (" neg" if v < 0 else "")
            if txt == "—":
                extra += " empty"            # (dropped from the phone's card view: an empty line says nothing)
            tds.append(f'<td{cls(c, extra)} data-label="{e(labels.get(c, c))}">{e(txt)}</td>')
        body.append("<tr>" + "".join(tds) + "</tr>")
    style = f' style="max-height:{int(height)}px"' if height else ""
    return (f'<div class="gs"><div class="tbl"{style}><table><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div></div>')


def table(data, **kw) -> None:
    """Show a table that fits (see table_html). Nothing is drawn for no rows."""
    html = table_html(data, **kw)
    if html:
        st.html(html)
