"""gridsim GUI entry point: navigation, shared sidebar settings and the landing page.

Run:  python -m streamlit run app/streamlit_app.py     (or double-click "Start GridSim.bat")
"""
from __future__ import annotations

import streamlit as st

import common

st.set_page_config(page_title="gridsim", page_icon="🏈", layout="wide", initial_sidebar_state="auto")
common.inject_css()
st.session_state["_via_app"] = True     # pages check this (common.via_app): they are only shown through this script


def landing() -> None:
    import datetime as dt

    from gridsim import positions as pos
    s = common.settings()
    st.html('<div class="gs"><h1>GridSim</h1><p class="sub">Every NFL game of the week simulated thousands of times, '
            "then compared with Kalshi's prices. <b>Read-only</b>: GridSim shows numbers and watches what you tell it "
            "you hold; it never places an order.</p></div>")
    try:
        season, week, games = common.slate(s["season"], s["week"])
    except Exception as e:  # schedule not downloaded yet
        season = week = None
        games = []
        st.warning(f"Schedule not downloaded yet ({e}). Open **Weekly routine** and press **Refresh data**, then reload this page.")

    # ---- where things stand right now ----
    if season is not None:
        wb = common.week_board(s["season"], s["week"])
        rows = [r for g in wb["games"].values() for r in (g.get("rows") or [])]
        edges = [r for r in rows if r["rating"] in common.EDGE_RATINGS]
        leans = [r for r in rows if r["rating"] == "lean"]
        try:
            when = dt.datetime.fromisoformat(wb["ts_utc"]).astimezone().strftime("%a %I:%M %p").replace(" 0", " ")
        except (TypeError, ValueError):
            when = None
        c = common.cfg()
        try:
            held = pos.open_positions(c)
            last_alert = (pos.recent_alerts(c, 1) or [None])[0]
        except Exception:
            held, last_alert = [], None
        st.html(common.kv_html([
            (f"{season} · week {week}", f"{len(games)} games", f"{len(wb['games'])} still to kick off"),
            ("Kalshi edges", f"{len(edges)}" if rows else "—",
             (f"after adjusting · GridSim leans to {len(leans)} more ({sum(r['cost'] < 0.5 for r in leans)} under 50¢)")
             if rows else ("no games left this week" if not wb["games"] else
                           "prices saved; the games are simulated at the next price check" if when
                           else "no prices saved yet this week")),
            ("Last price check", common.hr.esc(when) if when else "—",
             "GridSim checks every 45 minutes while it is open" if when else
             ("this week's games have all kicked off" if not wb["games"] and not wb.get("error")
              else "GridSim fetches them within a few minutes of starting")),
            ("My positions", f"{len(held)} open",
             common.hr.esc(last_alert["title"][:70]) if last_alert else "no alerts yet"),
        ]))

    # ---- what GridSim did by itself ----
    try:
        from gridsim import autopilot
        ap = autopilot.status(common.cfg())
        low = lambda t: t[:1].lower() + t[1:]            # ("Checking Kalshi's prices": only the first letter)
        if st.session_state.get("_ap_wait") or st.session_state.get("_ap_busy") or ap.get("state") == "running":
            st.caption("⏳ GridSim is looking after itself right now (see the line at the top); this page refreshes "
                       "when it is done.")
        elif ap.get("interrupted"):
            st.caption("GridSim's own upkeep was interrupted (GridSim or the PC was closed while it ran); it picks up "
                       "where it left off within a few minutes.")
        elif ap.get("state") == "done" and ap.get("finished_utc"):
            fin = dt.datetime.fromisoformat(ap["finished_utc"]).astimezone().strftime("%a %I:%M %p").replace(" 0", " ")
            did = [f"{low(x['what'])} ({x['detail']})" for x in ap["steps"] if x["state"] == "done"]
            bad = [f"{low(x['what'])} — {x['detail']}" for x in ap["steps"] if x["state"] == "failed"]
            if bad:
                st.warning(f"GridSim's own upkeep at {fin} could not finish: " + "; ".join(bad) + ". It tries again "
                           "by itself every few minutes while GridSim is open.")
            else:
                st.caption(f"✅ Up to date — GridSim looked after itself at {fin}"
                           + (": " + "; ".join(did) if did else ": nothing was due") + ". Nothing for you to press.")
        elif ap.get("state") == "never":
            st.caption("GridSim looks after itself: when you start it, it refreshes the data, grades finished games, "
                       "locks in predictions and checks Kalshi's prices in the background.")
    except Exception:
        pass

    # ---- the way through the app ----
    st.markdown("#### Start here")
    steps = [
        ("1 · See the week", common.WEEK_PAGE, "This Week", ":material/calendar_month:",
         "Every game as a card: the line, GridSim's number and its top Kalshi edge or lean."),
        ("2 · Open a game", common.GAME_PAGE, "Game", ":material/sports_football:",
         "Everything about one game. The **Kalshi prices** tab lists every contract you could buy against GridSim's "
         "chance — filter to under 50¢ for the long shots."),
        ("3 · Or search the whole week", common.SIGNALS_PAGE, "Signals", ":material/radar:",
         "All games in one list, filtered by price, type and game — plus how the contracts GridSim flagged before "
         "turned out."),
        ("4 · Bought something?", common.POSITIONS_PAGE, "My positions", ":material/visibility:",
         "Note what you hold; GridSim watches the price, the injury report and its own number, and alerts you."),
    ]
    cols = st.columns(4)
    for col, (head, page, title, icon, blurb) in zip(cols, steps):
        with col, st.container(border=True):
            st.markdown(f"**{head}**")
            st.caption(blurb)
            st.page_link(page, label=title, icon=icon)

    groups = [
        ("More ways to look at a game", [
            (common.PARLAY_PAGE, "Parlays", ":material/join_inner:",
             "Auto parlay from this week's edges and leans (safer to long shot), or build your own across games."),
            (common.PROPS_PAGE, "Player props", ":material/person:",
             "Any player stat with a movable line — over / under counted from the sims."),
            (common.WHATIF_PAGE, "What-if & Live", ":material/swap_horiz:",
             "With vs without a player, a QB swap, or a sim that starts from a live game situation."),
        ]),
        ("How good is GridSim?", [
            (common.SCORECARD_PAGE, "Scorecard", ":material/scoreboard:",
             "The record of locked-in predictions against results and the betting line."),
            (common.DIAGNOSE_PAGE, "Diagnose", ":material/troubleshoot:",
             "The check-up: where the sim is off, in plain words."),
        ]),
        ("Upkeep", [
            (common.WEEKLY_PAGE, "Weekly routine", ":material/checklist:",
             "Once a week: refresh data, lock in predictions, grade last week, check-up, tuning."),
            (common.SETTINGS_PAGE, "Settings", ":material/settings:",
             "Your settings, your data and backups, phone alerts, the schedule."),
        ]),
    ]
    for head, cards in groups:
        st.markdown(f"##### {head}")
        cols = st.columns(3)
        for col, (page, title, icon, blurb) in zip(cols, cards):
            with col, st.container(border=True):
                st.markdown(f"**{title}**")
                st.caption(blurb)
                st.page_link(page, label=title, icon=icon)
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
    "This week": [
        st.Page(common.WEEK_PAGE, title="This Week", icon=":material/calendar_month:"),
        st.Page(common.GAME_PAGE, title="Game", icon=":material/sports_football:"),
        st.Page(common.SIGNALS_PAGE, title="Signals", icon=":material/radar:"),
        st.Page(common.POSITIONS_PAGE, title="My positions", icon=":material/visibility:"),
    ],
    "Game tools": [
        st.Page(common.PARLAY_PAGE, title="Parlays", icon=":material/join_inner:"),
        st.Page(common.PROPS_PAGE, title="Player props", icon=":material/person:"),
        st.Page(common.WHATIF_PAGE, title="What-if & Live", icon=":material/swap_horiz:"),
    ],
    "Track record": [
        st.Page(common.SCORECARD_PAGE, title="Scorecard", icon=":material/scoreboard:"),
        st.Page(common.DIAGNOSE_PAGE, title="Diagnose", icon=":material/troubleshoot:"),
    ],
    "Upkeep": [st.Page(common.WEEKLY_PAGE, title="Weekly routine", icon=":material/checklist:"),
               st.Page(common.SETTINGS_PAGE, title="Settings", icon=":material/settings:")],
}
nav = st.navigation(pages)
_goto = st.session_state.pop("_goto", None)      # a page that was opened by its own address (see common.via_app)
if _goto:
    st.switch_page(_goto)
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


def autopilot_line(view_page: bool = True) -> None:
    """GridSim looks after itself: whatever is due (data, grading, lock-in, price check, check-up) is started in the
    background when the app is opened and, by a timer, whenever something becomes due while it stays open; this line
    says what it is doing. Nothing to press."""
    from gridsim import autopilot
    ss = st.session_state
    c = common.cfg()
    autopilot.ensure_ticker()          # and every few minutes from now on, for as long as GridSim keeps running
    started = autopilot.start_if_due(c)
    if started == "started":
        ss["_ap_wait"] = True

    def show() -> None:
        stt = autopilot.status(c)
        busy = stt.get("state") == "running" or (ss.get("_ap_wait") and autopilot.running(c))
        if busy:
            ss["_ap_wait"], ss["_ap_busy"] = False, True
            steps = stt.get("steps") or []
            cur = next((x for x in steps if x["state"] == "running"), None)
            n = sum(x["state"] != "waiting" for x in steps)
            what = cur["what"][:1].lower() + cur["what"][1:] if cur else "starting"
            st.info(f"**GridSim is getting things ready** — {what}"
                    + (f" (step {n} of {len(steps)})" if steps else "") + ". You can use the app meanwhile; the "
                    "numbers refresh by themselves when it is done.", icon=":material/autorenew:")
        elif ss.get("_ap_busy"):
            ss["_ap_busy"] = False                # just finished: the new data is there on the next look
            st.cache_data.clear()
            if view_page:
                st.rerun()                        # a page that only shows things: redraw it with the new numbers
            # a page with buttons may be showing the result of one just pressed (a check-up, a priced parlay):
            # redrawing would wipe it, so say it instead
            st.toast("GridSim finished its upkeep — the numbers are fresh on your next click.", icon=":material/check:")

    busy_now = autopilot.status(c).get("state") == "running" or ss.get("_ap_wait")
    frag = getattr(st, "fragment", None)
    if frag is not None:
        frag(show, run_every=4 if busy_now else 30)()
    else:
        show()


try:
    # pages that only show things may be redrawn when a background run ends; the others keep what they show
    autopilot_line(view_page=(getattr(nav, "title", "") in ("Home", "This Week", "Signals", "Scorecard", "My positions")))
except Exception as e:  # never block the app
    st.caption(f"(automatic upkeep skipped: {type(e).__name__}: {e})")
nav.run()
