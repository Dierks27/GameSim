"""gridsim GUI entry point: navigation, shared sidebar settings and the landing page.

Run:  python -m streamlit run app/streamlit_app.py     (or double-click "Start GridSim.bat")
"""
from __future__ import annotations

import streamlit as st

import common

st.set_page_config(page_title="gridsim", page_icon="🏈", layout="wide", initial_sidebar_state="auto")
common.inject_css()


def landing() -> None:
    s = common.settings()
    st.html('<div class="gs"><h1>gridsim</h1><p class="sub">NFL single-game Monte Carlo simulator — every snap '
            "resampled from real nflverse play-by-play. Same inputs and seed give the same numbers here and in "
            "<code>gridsim run</code>.</p></div>")
    try:
        season, week, games = common.slate(s["season"], s["week"])
        st.markdown(f"**{season} week {week}** — {len(games)} games on the slate.")
    except Exception as e:  # schedule not downloaded yet
        st.warning(f"Schedule unavailable ({e}). Run `gridsim refresh-data` once.")
    c1, c2 = st.columns(2)
    with c1, st.container(border=True):
        st.markdown("#### This Week")
        st.caption("The slate as cards with market lines, sim win % and the sim-vs-market gap. Sim one game or all.")
        st.page_link(common.WEEK_PAGE, label="Open This Week", icon=":material/calendar_month:")
    with c2, st.container(border=True):
        st.markdown("#### Game")
        st.caption("One matchup in depth: charts, any-line cover calculator, alt lines, team stats, lineup editor.")
        st.page_link(common.GAME_PAGE, label="Open Game", icon=":material/sports_football:")
    done = st.session_state.get("results", {})
    if done:
        st.markdown("##### Simulated this session")
        for k, r in list(done.items())[-8:][::-1]:
            H, A = r["matchup"]["home"], r["matchup"]["away"]
            st.markdown(f"- {A} @ {H}: {H} {common.hr.fmt_pm(r['win']['home'])} · fair {H} "
                        f"{common.hr.fmt_line(r['fair']['spread_home'])} · total {r['fair']['total']:.1f} "
                        f"({r['n_sims']:,} sims, seed {r['meta'].get('seed')})")
    st.caption("Spreads are always from the home team's perspective: negative = home favored. Every percentage carries "
               "its 95% simulation margin of error.")


def _persist(name: str, default) -> str:
    """Widget values are kept under "set_<name>" (plain session state) and copied into the widget key on every
    run, so they survive page switches (Streamlit drops widget-owned state when the page changes)."""
    ss = st.session_state
    ss.setdefault(f"set_{name}", default)
    ss[f"w_{name}"] = ss[f"set_{name}"]
    return f"w_{name}"


def _sync(name: str) -> None:
    st.session_state[f"set_{name}"] = st.session_state[f"w_{name}"]


def sidebar() -> None:
    c = common.cfg()
    ss = st.session_state
    try:
        idx = common.schedule_index()
    except Exception:
        idx = {}
    cur = common.current_season()
    seasons = sorted({cur, *[y for y in idx if cur - 4 <= y <= cur]}, reverse=True)
    st.sidebar.markdown("### Settings")
    st.sidebar.selectbox("Season", seasons, key=_persist("season", cur), on_change=_sync, args=("season",))
    weeks = idx.get(ss["set_season"], {}).get("weeks", list(range(1, 23)))
    opts = ["Current"] + [str(w) for w in weeks]
    if ss.get("set_week") not in opts:
        ss["set_week"] = "Current"
    st.sidebar.selectbox("Week", opts, key=_persist("week", "Current"), on_change=_sync, args=("week",),
                         help="'Current' = first week with an unplayed game")
    st.sidebar.number_input("Sims per game", min_value=100, max_value=100_000, step=500,
                            key=_persist("sims", int(c.sim["n_sims"])), on_change=_sync, args=("sims",),
                            help="Margin of error shrinks with more sims (±2.2 pts at 2,000; ±1.0 at 10,000).")
    st.sidebar.number_input("Seed", min_value=0, max_value=2**31 - 1, step=1, key=_persist("seed", int(c.sim["seed"])),
                            on_change=_sync, args=("seed",))
    st.sidebar.toggle("Player projections", key=_persist("players", True), on_change=_sync, args=("players",),
                      help="Same as leaving out --no-players on the CLI. Off is faster.")
    st.sidebar.caption(f"config {c.hash}")


pages = [
    st.Page(landing, title="Home", icon=":material/home:", default=True),
    st.Page(common.WEEK_PAGE, title="This Week", icon=":material/calendar_month:"),
    st.Page(common.GAME_PAGE, title="Game", icon=":material/sports_football:"),
]
nav = st.navigation(pages)
sidebar()
nav.run()
