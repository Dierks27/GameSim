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
        st.warning(f"Schedule not downloaded yet ({e}). Open **Weekly routine** and press **Refresh data**, then reload this page.")
    cards = [
        (common.WEEKLY_PAGE, "Weekly routine", ":material/checklist:",
         "Your weekly to-do list as buttons: refresh data, lock in predictions, grade last week, check-up."),
        (common.WEEK_PAGE, "This Week", ":material/calendar_month:",
         "The slate as cards with market lines, sim win % and the sim-vs-market gap. Sim one game or all."),
        (common.GAME_PAGE, "Game", ":material/sports_football:",
         "One matchup in depth: charts, any-line cover calculator, alt lines, team stats, lineup editor."),
        (common.PROPS_PAGE, "Player props", ":material/person:",
         "Any player stat's distribution with a movable line — over / under counted from the sims."),
        (common.PARLAY_PAGE, "Parlay builder", ":material/join_inner:",
         "Same-game parlays priced from the sims: joint % vs naive product, correlation lift, fair odds."),
        (common.WHATIF_PAGE, "What-if & Live", ":material/swap_horiz:",
         "With vs without a player (or a QB swap), and sims that start from a live game situation."),
        (common.SIGNALS_PAGE, "Signals", ":material/radar:",
         "Kalshi scanner (read-only): current edges, watchlist and the graded signal record."),
        (common.POSITIONS_PAGE, "My positions", ":material/visibility:",
         "Position watch (read-only): enter what you hold on Kalshi; get alerts on price jumps and lineup news."),
        (common.SCORECARD_PAGE, "Scorecard", ":material/scoreboard:",
         "The live record of frozen predictions, calibration, backtests and tuning history."),
        (common.DIAGNOSE_PAGE, "Diagnose", ":material/troubleshoot:",
         "Where the sim is wrong: top-3 summary, component bias, residual groups, worst misses."),
        (common.SETTINGS_PAGE, "Settings", ":material/settings:",
         "Edit config.yaml (comments kept), refresh data, test notifications, schedule status."),
    ]
    for i in range(0, len(cards), 3):
        cols = st.columns(3)
        for col, (page, title, icon, blurb) in zip(cols, cards[i:i + 3]):
            with col, st.container(border=True):
                st.markdown(f"#### {title}")
                st.caption(blurb)
                st.page_link(page, label=f"Open {title}", icon=icon)
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
    import gridsim
    from gridsim.config import MODEL_REV
    st.sidebar.caption(f"GridSim version {gridsim.__version__} · model {MODEL_REV} · settings {c.hash}",
                       help="Version: the date this copy of GridSim was released — it changes with every update. "
                            "Model: counts up when an update changes how games are simulated. Settings: a code for "
                            "your current settings; the Scorecard lists its results under it.")


pages = {
    "": [st.Page(landing, title="Home", icon=":material/home:", default=True)],
    "Games": [
        st.Page(common.WEEK_PAGE, title="This Week", icon=":material/calendar_month:"),
        st.Page(common.GAME_PAGE, title="Game", icon=":material/sports_football:"),
        st.Page(common.PROPS_PAGE, title="Player props", icon=":material/person:"),
        st.Page(common.PARLAY_PAGE, title="Parlay builder", icon=":material/join_inner:"),
        st.Page(common.WHATIF_PAGE, title="What-if & Live", icon=":material/swap_horiz:"),
    ],
    "Markets & grading": [
        st.Page(common.SIGNALS_PAGE, title="Signals", icon=":material/radar:"),
        st.Page(common.POSITIONS_PAGE, title="My positions", icon=":material/visibility:"),
        st.Page(common.SCORECARD_PAGE, title="Scorecard", icon=":material/scoreboard:"),
        st.Page(common.DIAGNOSE_PAGE, title="Diagnose", icon=":material/troubleshoot:"),
    ],
    "Setup": [st.Page(common.WEEKLY_PAGE, title="Weekly routine", icon=":material/checklist:"),
              st.Page(common.SETTINGS_PAGE, title="Settings", icon=":material/settings:")],
}
nav = st.navigation(pages)
# Widget values under common.KEEP keys survive page switches: re-assigning them as plain session state stops
# Streamlit from discarding them when their widget is not on the current page.
for _k in [k for k in st.session_state.keys() if isinstance(k, str) and k.startswith(common.KEEP)]:
    st.session_state[_k] = st.session_state[_k]
sidebar()


def data_guard() -> None:
    """Once per session: move data from the program folder into the data folder (older versions kept it there) and
    make the daily backup. On every page: a loud banner if the data folder is empty while backups exist."""
    from gridsim import userdata
    ss = st.session_state
    c = common.cfg()
    if not ss.get("_data_checked"):
        ss["_data_checked"] = True
        rep = userdata.ensure(c)
        if rep.get("added") or rep.get("records added"):
            ss["_data_moved"] = userdata.format_report(rep)
        if rep.get("error"):
            st.warning(f"Could not move some data from the GridSim program folder: {rep['error']}")
        userdata.auto_backup(c, "daily", min_interval_h=24)
    if ss.get("_data_moved"):
        st.toast(f"Moved your data into {userdata.data_dir()}", icon=":material/move_to_inbox:")
        ss["_data_moved"] = None
    if ss.get("_restored"):
        st.success(ss.pop("_restored"))
    import time
    if ss.get("_status") is None or time.time() - ss.get("_status_t", 0) > 60:   # checked at most once a minute
        ss["_status"], ss["_status_t"] = userdata.status(c), time.time()
    stt = ss["_status"]
    if stt["warn_empty"]:
        cand = stt["restore_candidate"]
        what = ", ".join(stt["missing"].get(cand.name, [])) or "predictions or records"
        with st.container(border=True):
            st.error(f"**Your GridSim data looks lost.** A backup from {common.mtime_text(cand)} holds {what} that your "
                     f"data folder ({stt['data_dir']}) doesn't have. Restore it before doing anything else, so your "
                     "record carries on instead of starting over. Restoring only adds what's missing — nothing you "
                     "have now is lost. (Automatic backups are paused until you decide.)")
            c1, c2, c3 = st.columns(3)
            if c1.button("Restore the latest backup", type="primary", icon=":material/settings_backup_restore:"):
                try:
                    rep = userdata.restore(cand, c)
                    ss["_restored"] = "Restored — " + userdata.format_report(rep)
                    ss["_status"] = None
                except Exception as e:
                    ss["_restored"] = None
                    st.error(f"Restore failed — {e}")
                else:
                    st.rerun()
            c2.page_link(common.SETTINGS_PAGE, label="Other options: Settings → Your data", icon=":material/settings:")
            with c3.popover("Start fresh instead"):
                st.markdown("Keep the data folder as it is and stop this warning. No backup is deleted; you can "
                            "still restore any of them later in Settings → Your data. (Anything an older GridSim left "
                            "in the program folder stays there too.)")
                if st.button("Yes, start fresh"):
                    userdata.start_fresh(c)
                    ss["_status"] = None
                    st.rerun()


try:
    data_guard()
except Exception as e:  # never block the app
    st.caption(f"(data folder check skipped: {e})")
nav.run()
