"""Weekly routine: the whole week's to-do list as buttons (no console needed).

1 Refresh data  ->  2 Lock in this week's predictions  ->  3 Grade last week  ->  4 Check what's off (diagnose)
-> 5 (once a season) self-tune in the background.  Same library calls as `gridsim weekly` / the CLI.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys

import polars as pl
import streamlit as st

import common
from gridsim import live
from gridsim.data import load_schedules

s = common.settings()
cfg = common.cfg()
ROOT = common.ROOT


def _alive(pid: int) -> bool:
    """Is the background tuning process still running?"""
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    import os
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:   # a finished child that was never waited on is a zombie: treat as not running
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split()[2] != "Z"
    except OSError:
        return True

common.page_header("Weekly routine", "Do these in order once a week. Each button does one job and tells you when "
                                     "it is finished. Nothing here places bets or orders.")

season = s["season"]
try:
    sched = load_schedules(cfg).filter(pl.col("season") == season)
    played = sched.filter(pl.col("home_score").is_not_null())
    # last week whose games are ALL final (so a Friday after Thursday Night Football still points at last week)
    done_weeks = (sched.group_by("week").agg(pl.col("home_score").is_null().sum().alias("open"))
                  .filter(pl.col("open") == 0)["week"].to_list())
    last_done = int(max(done_weeks)) if done_weeks else (int(played["week"].max()) if played.height else None)
    upcoming = sched.filter(pl.col("home_score").is_null())
    next_week = int(upcoming["week"].min()) if upcoming.height else None
except Exception as e:
    st.error(f"Schedule not available yet ({e}). Do step 1 first.")
    last_done = next_week = None

# ---- 1. refresh -------------------------------------------------------------------------
with st.container(border=True):
    st.markdown("#### 1 · Get the latest data")
    st.caption("Downloads the newest play-by-play, schedule, rosters, depth charts and injury reports. "
               "Do it first every time (takes about a minute).")
    if st.button("Refresh data", type="primary", key="wk_refresh", icon=":material/download:"):
        with st.status("Downloading ...", expanded=True) as box:
            log = st.empty()
            try:
                from gridsim.availability import refresh_availability
                from gridsim.data import refresh_all
                with common.capture_output(log, keep_lines=6):
                    refresh_all(cfg, [season])
                    refresh_availability(cfg, season)
                st.cache_data.clear()
                box.update(label="Data refreshed", state="complete")
            except Exception as e:
                box.update(label=f"Refresh failed: {e}", state="error")

# ---- 2. predictions -----------------------------------------------------------------------
with st.container(border=True):
    st.markdown("#### 2 · Lock in this week's predictions" + (f" (week {next_week})" if next_week else ""))
    st.caption("Simulates every game that has not kicked off yet and saves a frozen, time-stamped copy. These are "
               "what gets graded later — the honest record. Best: Wednesday, again Friday evening after the final "
               "injury reports, and optionally Sunday morning after inactives.")
    sims = st.number_input("Sims per game", 500, 20000, max(500, int(s["sims"])), step=500, key="wk_sims",
                           help="500 or more. 2,000 is the normal setting (about 2 minutes for a full week). The seed "
                                "and player-projection setting come from the sidebar, so This Week reuses these sims.")
    if st.button("Lock in predictions", type="primary", key="wk_predict", icon=":material/lock:",
                 disabled=next_week is None):
        bar = st.progress(0.0, text="starting ...")
        log = st.empty()
        try:
            with common.capture_output(log, keep_lines=6):
                paths = live.predict_week(cfg, season, next_week, sims=int(sims), seed=int(s["seed"]),
                                          use_players=bool(s["players"]),
                                          progress=lambda f: bar.progress(min(f, 1.0), text=f"{100 * f:.0f}%"))
            bar.progress(1.0, text="done")
            if paths:
                st.success(f"Saved {len(paths)} locked-in predictions for week {next_week} at {int(sims):,} sims per game. "
                           "Games that had already kicked off are skipped.")
            else:
                st.info("Nothing to lock in: every game of this week has already kicked off.")
        except Exception as e:
            st.error(f"Could not finish: {type(e).__name__}: {e}")

# ---- 3. grade -----------------------------------------------------------------------------
with st.container(border=True):
    st.markdown("#### 3 · Grade last week" + (f" (week {last_done})" if last_done else ""))
    st.caption("After the games are over (Tuesday is safest), scores the locked-in predictions against what "
               "happened and against the betting market. Results appear on the Scorecard page.")
    gw = st.number_input("Week to grade", 1, 22, last_done or 1, key="wk_gradeweek")
    if st.button("Grade", type="primary", key="wk_grade", icon=":material/fact_check:"):
        try:
            with st.spinner("Grading ..."):
                rows = live.grade(cfg, season, int(gw), verbose=False)
            sc = live.scorecard(cfg, season)
            if rows:
                st.success(f"Graded {len(rows)} new games for week {int(gw)}.")
            else:
                st.info(f"Nothing new to grade for week {int(gw)}: either no predictions were locked in before those "
                        "games, or they are already graded. Lock in predictions (step 2) every week so there is "
                        "something to grade the following Tuesday.")
            ov = sc.get("overall") or {}
            if ov.get("games"):
                st.markdown(f"Season so far: **{ov['games']} games**, Brier **{ov['brier']:.4f}** "
                            f"(market {ov.get('market_brier', float('nan')):.4f}, lower is better), "
                            f"average miss on the margin **{ov['mae_margin']:.1f} pts** "
                            f"(market {ov.get('market_mae_margin', float('nan')):.1f}).")
            st.page_link(common.SCORECARD_PAGE, label="Open the Scorecard", icon=":material/scoreboard:")
        except Exception as e:
            st.error(f"Could not grade: {type(e).__name__}: {e}")

# ---- 4. diagnose ----------------------------------------------------------------------------
with st.container(border=True):
    st.markdown("#### 4 · Check what the sim is getting wrong")
    st.caption("Re-plays the last few finished weeks with only the data that was available at the time and lists "
               "the top 3 things most likely off. Takes a few minutes.")
    n_weeks = st.slider("How many recent weeks", 2, 8, 4, key="wk_diag_n")
    if st.button("Run check-up", type="primary", key="wk_diag", icon=":material/troubleshoot:",
                 disabled=last_done is None):
        try:
            from gridsim.backtest import run_backtest
            from gridsim.diagnose import diagnose, write_report
            first = max(1, last_done - n_weeks + 1)
            with st.status(f"Re-playing weeks {first}-{last_done} ...", expanded=True) as box:
                log = st.empty()
                with common.capture_output(log, keep_lines=6):
                    # one process: helper processes would re-run this page's script on start-up inside Streamlit
                    g, ex = run_backtest(cfg, season, list(range(first, last_done + 1)),
                                         sims=int(cfg.tune["sims_per_game"]), verbose=True, workers=1)
                    diag = diagnose(g, ex["components"])
                    write_report(diag, cfg.resolve_path("diagnose") / f"{season}_wk{first}-{last_done}_{cfg.hash}",
                                 f"Diagnose {season} weeks {first}-{last_done}")
                box.update(label="Check-up finished", state="complete")
            common.callout("<b>Top 3 things most likely wrong</b><ol>" +
                           "".join(f"<li>{common.hr.esc(t)}</li>" for t in diag["summary"]) + "</ol>")
            st.page_link(common.DIAGNOSE_PAGE, label="See the full report", icon=":material/troubleshoot:")
        except Exception as e:
            st.error(f"Check-up failed: {type(e).__name__}: {e}")

# ---- 5. tune ----------------------------------------------------------------------------------
with st.container(border=True):
    st.markdown("#### 5 · Self-tune (once a season, optional)")
    st.caption(f"Searches the model's settings on the {season - 4}–{season - 2} seasons and tests the winner on "
               f"{season - 1}, a season it never saw. It does not use this season's games, so running it again later "
               "in the season mostly repeats the same search: once now and once after the season ends is enough. "
               "At 150 trials it runs for many hours in the background, at low priority, leaving "
               f"{(common.cfg().raw.get('resources') or {}).get('free_cores', 2)} processor cores free and keeping the "
               f"whole PC under {100 * float((common.cfg().raw.get('resources') or {}).get('max_ram_fraction', 0.8)):.0f}% "
               "memory, so the PC stays usable (it shows as many “Python” entries in Task Manager — "
               "that's normal). Starting it in the evening is easiest. It keeps going even if you close GridSim; to "
               "stop it early, restart the PC (nothing is harmed, that run's progress is just lost). It never "
               "changes your settings by itself: you decide below whether to use the result.")
    log_path = cfg.resolve_path("tuning") / "gui_tune.log"
    pid_path = cfg.resolve_path("tuning") / "gui_tune.pid"
    running = False
    if pid_path.exists():
        try:
            running = _alive(int(json.loads(pid_path.read_text())["pid"]))
        except Exception:
            running = False
    c1, c2 = st.columns(2)
    trials = c1.number_input("Trials", 10, 300, int(cfg.tune["trials"]), step=10, key="wk_trials",
                             help="More trials = better search, longer run (about 3-6 minutes each).")
    if c2.button("Start tuning in the background", key="wk_tune", icon=":material/tune:", disabled=running):
        cur = season
        cmd = [sys.executable, "-m", "gridsim", "tune", "--train", f"{cur - 4}-{cur - 2}", "--validate", str(cur - 1),
               "--trials", str(int(trials))]
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(log_path, "w", encoding="utf-8")
        # Windows: CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | BELOW_NORMAL_PRIORITY_CLASS -> no console windows
        # (also for its worker processes), keeps running if the GridSim window is closed, and yields to anything
        # else you do on the PC
        kw = ({"creationflags": 0x08000000 | 0x00000200 | 0x00004000} if sys.platform == "win32"
              else {"start_new_session": True})
        p = subprocess.Popen(cmd, cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT, **kw)
        pid_path.write_text(json.dumps({"pid": p.pid, "started": dt.datetime.now().isoformat(timespec="minutes"),
                                        "cmd": " ".join(cmd)}))
        st.success("Tuning started. You can close this page; come back later and press Refresh status.")
        running = True
    if log_path.exists():
        txt = log_path.read_text(encoding="utf-8", errors="ignore")
        trials_done = sum(1 for ln in txt.splitlines() if ln.startswith("trial "))
        st.markdown(f"Status: **{'running' if running else 'finished'}** · trials done: **{trials_done}**")
        st.button("Refresh status", key="wk_tune_refresh")
        if "Recommended:" in txt:
            tail = txt[txt.find("Train ["):] if "Train [" in txt else txt[-2000:]
            st.code(tail, language=None)
        tuned = cfg.resolve_path("config.tuned.yaml")
        if "Recommended: yes" in txt and tuned.exists():
            common.callout("The tuned settings did better on the held-out season. Use them?", tone="good")
            if st.button("Use the tuned settings", type="primary", key="wk_apply"):
                from gridsim.tune import apply_tuned
                b = apply_tuned(cfg)
                st.cache_data.clear()
                st.success(f"Done. Your previous settings were backed up to {b.name}.")
        elif "Recommended: no" in txt:
            st.info("The tuned settings did not beat your current ones on the held-out season, so nothing changes.")

st.caption("Prefer it automatic? Double-click \"Schedule GridSim\" in the GridSim folder once and press A: every "
           "Tuesday 9:00 it refreshes the data, grades last week, runs the check-up and locks in the next week; every "
           "Friday 19:00 it locks in fresh predictions after the final injury reports. It runs in the background "
           "while you are signed in to Windows.")
