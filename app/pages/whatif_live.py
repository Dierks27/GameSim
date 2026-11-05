"""What-if & Live: (a) the same game with vs without a player (or a QB swap) via api.player_impact;
(b) start the simulation from a live game state (quarter, clock, score, ball) via RunRequest.state."""
from __future__ import annotations

import streamlit as st

import common
import helpers
from gridsim import api
from gridsim.availability import NameMatchError
from gridsim.state import StateError, parse_state

hr = common.hr
ss = st.session_state
s = common.settings()

common.page_header("What-if & Live", "Rule a player out (or swap the QB) and see what moves — or start the sim from "
                                     "the current game situation.")
g = common.pick_game("wl")
H, A = g["home"], g["away"]
tab_w, tab_l = st.tabs(["What-if: player out / QB swap", "Live game state"])

# ======================================================================================================
with tab_w:
    tkey = common.keep("wi_team", H)
    if ss[tkey] not in (H, A):
        ss[tkey] = H
    c1, c2, c3 = st.columns([1, 2, 2])
    team = c1.selectbox("Team", [H, A], key=tkey)
    try:
        roster = common.team_roster(g["season"], team, g["week"])
    except Exception as e:
        roster = []
        st.warning(f"Roster unavailable ({e}).")
    base_req = common.make_request(H, A, g["season"], g["week"], {**s, "players": True},
                                   neutral=g["neutral"], playoff=g["playoff"])
    base = common.lookup(base_req)
    pos = {r["name"]: r["pos"] for r in roster}
    sim_names = [p["name"] for p in ((base or {}).get("players") or {}).get(team, [])]
    for p in ((base or {}).get("players") or {}).get(team, []):
        pos.setdefault(p["name"], p.get("pos", ""))
    active = [r["name"] for r in roster if r["pos"] in ("QB", "RB", "FB", "WR", "TE", "K") and r["status"] in ("ACT", "INA", "")]
    names = list(dict.fromkeys(sim_names + active))
    qbs = [r["name"] for r in roster if r["pos"] == "QB"]
    if not names:
        st.info("No players found for this team (roster data missing — run a data refresh on the Settings page).")
    else:
        pkey = common.keep("wi_player", names[0])
        if ss[pkey] not in names:
            ss[pkey] = names[0]
        player = c2.selectbox("Player ruled OUT", names, key=pkey,
                              format_func=lambda nm: f"{nm} · {pos.get(nm) or '?'}")
        q_opts = ["(no QB swap)"] + [q for q in qbs if q != player]
        qkey = common.keep("wi_qb", q_opts[0])
        if ss[qkey] not in q_opts:
            ss[qkey] = q_opts[0]
        qb = c3.selectbox("Starting QB instead (optional)", q_opts, key=qkey,
                          help="QB swap: the player on the left is ruled out and this QB is forced to start.")
        qb = None if qb == q_opts[0] else qb
        wkey = f"{common.req_key(base_req)}|{team}:{player}|{qb or ''}"
        store = ss.setdefault("whatif", {})
        c1, c2 = st.columns([2, 3], vertical_alignment="center")
        go = c1.button("Run what-if", type="primary", width="stretch")
        c2.caption(f"Two runs at {s['sims']:,} sims with the same seed ({s['seed']}): as listed, then without "
                   f"{player}{' and with ' + qb + ' starting' if qb else ''}.")
        if go:
            bar = st.progress(0.0, text="1/2 with the lineup as listed …")

            def cb(f):
                bar.progress(min(1.0, 0.5 * f), text=f"1/2 with the lineup as listed … {100 * f:.0f}%")
                if f >= 1:
                    bar.progress(0.5, text=f"2/2 without {player} …")
            try:
                store[wkey] = api.player_impact(base_req, f"{team}:{player}", common.cfg(),
                                                qb=f"{team}:{qb}" if qb else None, progress=cb)
            except (NameMatchError, ValueError) as e:
                st.error(str(e))
            except Exception as e:
                st.error(f"What-if failed — {type(e).__name__}: {e}")
            bar.empty()
        r = store.get(wkey)
        if r:
            w0, w1, d = r["with"], r["without"], r["delta"]
            Hh = r["home"]
            what = f"{player} OUT" + (f", {qb} starts" if qb else "")
            st.markdown(f"##### {r['away']} @ {Hh}: {what}")
            st.html(common.kv_html([
                (f"{Hh} win %", f"{100 * w1['home_win']:.1f}% <small class='muted'>from {100 * w0['home_win']:.1f}%</small>",
                 f"{100 * d['home_win']:+.1f} pts"),
                (f"{Hh} margin", f"{w1['margin']:+.1f} <small class='muted'>from {w0['margin']:+.1f}</small>",
                 f"{d['margin']:+.2f} pts"),
                ("Total", f"{w1['total']:.1f} <small class='muted'>from {w0['total']:.1f}</small>", f"{d['total']:+.2f} pts"),
            ]))
            mo = 100 * common.moe(0.5, r["n_sims"])
            st.caption(f"{r['n_sims']:,} sims each, same seed, so most of the noise cancels; a single win % still "
                       f"carries up to ± {mo:.1f} pts of simulation error.")
            shifts = r.get("usage_shifts") or []
            if shifts:
                rows = [[f"{hr.esc(x['player'])} <span class='muted'>{hr.esc(x['team'])}</span>", f"{x['d_targets']:+.2f}", f"{x['d_carries']:+.2f}",
                         f"{x['d_rec_yds']:+.1f}", f"{x['d_rush_yds']:+.1f}", f"{x['d_pass_yds']:+.1f}"] for x in shifts]
                st.html('<div class="gs card"><h2>Usage shifts (per game, without − with)</h2>'
                        + hr.table_html(["Player", "Targets", "Carries", "Rec yds", "Rush yds", "Pass yds"], rows)
                        + "</div>")
            else:
                st.caption("No teammate's usage moved by 0.3 or more (or player projections were off).")

# ======================================================================================================
with tab_l:
    st.caption("Enter the situation; the sim plays the rest of the game from there (box-score stats count from this "
               "point, scores are final scores). Kalshi prices are not fetched for live states.")
    c1, c2, c3 = st.columns(3)
    q = c1.selectbox("Quarter", ["Q1", "Q2", "Q3", "Q4", "OT"], key=common.keep("lv_q", "Q3"))
    mx = 10 if q == "OT" else 15
    if ss[common.keep("lv_min", 8)] > mx:
        ss[common.KEEP + "lv_min"] = mx
    mins = c2.number_input("Clock minutes", 0, mx, step=1, key=common.keep("lv_min", 8))
    secs = c3.number_input("Clock seconds", 0, 59, step=1, key=common.keep("lv_sec", 0))
    c1, c2, c3, c4 = st.columns(4)
    hs = c1.number_input(f"{H} score", 0, 99, step=1, key=common.keep("lv_hs", 0))
    as_ = c2.number_input(f"{A} score", 0, 99, step=1, key=common.keep("lv_as", 0))
    hto = c3.number_input(f"{H} timeouts", 0, 3, step=1, key=common.keep("lv_hto", 3))
    ato = c4.number_input(f"{A} timeouts", 0, 3, step=1, key=common.keep("lv_ato", 3))
    sit_lbl = {"home_ball": f"{H} has the ball", "away_ball": f"{A} has the ball",
               "home_kick": f"{H} kicking off", "away_kick": f"{A} kicking off"}
    sit = st.radio("Situation", list(sit_lbl), format_func=sit_lbl.get, horizontal=True, key=common.keep("lv_sit", "home_ball"))
    kick = sit.endswith("kick")
    poss = H if sit.startswith("home") else A
    down, dist, side, yard = 1, 10, None, 25
    if not kick:
        c1, c2, c2b, c3, c4 = st.columns([3, 3, 2, 3, 3], vertical_alignment="bottom")
        down = c1.selectbox("Down", [1, 2, 3, 4], format_func=lambda d: {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}[d],
                            key=common.keep("lv_down", 1))
        goal = c2b.toggle("& goal", key=common.keep("lv_goal", False))
        dist_in = c2.number_input("Distance", 1, 99, step=1, key=common.keep("lv_dist", 10), disabled=goal)
        dist = "goal" if goal else dist_in
        side_lbl = {"home": f"{H} side", "away": f"{A} side", "mid": "Midfield (50)"}
        sd = c3.selectbox("Ball on", list(side_lbl), format_func=side_lbl.get, key=common.keep("lv_side", "home"))
        if sd == "mid":
            yard, side = 50, None
        else:
            yard = c4.number_input("Yard line", 1, 49, step=1, key=common.keep("lv_yard", 25))
            side = H if sd == "home" else A
    r2 = None
    if q in ("Q1", "Q2"):
        r2o = {"": "coin flip per sim", H: H, A: A}
        r2 = st.selectbox("Receives 2nd-half kickoff", list(r2o), format_func=r2o.get, key=common.keep("lv_r2", "")) or None
    try:
        state = helpers.state_string(q, mins, secs, H, hs, A, as_, poss, down=down, distance=dist, spot_side=side,
                                     spot_yard=yard, home_timeouts=hto, away_timeouts=ato, kickoff=kick, receives2h=r2)
        parsed = parse_state(state, H, A)          # the library's own parser validates it
    except (ValueError, StateError) as e:
        st.error(str(e))
        st.stop()
    st.markdown("State string (same as `gridsim run --state`):")
    st.code(state, language=None)
    if "yl" in parsed:
        st.caption(f"{poss} needs {parsed['yl']} yards for a touchdown · {parsed['down']} & {parsed['togo']}.")
    req = common.make_request(H, A, g["season"], g["week"], s, neutral=g["neutral"], playoff=g["playoff"], state=state)
    summ = common.lookup(req)
    c1, _ = st.columns([2, 3])
    if c1.button("Simulate from this state" if summ is None else "Re-simulate (ignore cache)",
                 type="primary" if summ is None else "secondary", width="stretch"):
        summ = common.run(req, force=summ is not None, label=f"{A} @ {H} from {q} {mins}:{secs:02d}")
        if summ is not None:
            st.rerun()
    if summ is not None:
        st.html('<div class="gs">' + hr.tiles_html(summ) + "</div>")
        c1, c2 = st.columns(2)
        with c1:
            st.plotly_chart(hr.margin_figure(summ), theme=None, config=hr.PLOTLY_CONFIG, key=f"lv_m_{common.req_key(req)}")
        with c2:
            st.plotly_chart(hr.total_figure(summ), theme=None, config=hr.PLOTLY_CONFIG, key=f"lv_t_{common.req_key(req)}")
        st.html('<div class="gs">' + hr.scores_html(summ) + hr.top_scores_html(summ) + "</div>")
        if summ.get("banners"):
            with st.expander("Run notes"):
                for b in summ["banners"]:
                    st.markdown(f"- {b}")
