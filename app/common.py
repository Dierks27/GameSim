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
