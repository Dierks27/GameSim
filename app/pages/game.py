"""Game: one matchup in depth — charts, any-line cover calculator, alt lines, team stats, lineup editor."""
from __future__ import annotations

import json

import streamlit as st

import common
from gridsim.api import cover_from_hist
from gridsim.availability import default_overrides_path, load_overrides, save_overrides

hr = common.hr
s = common.settings()
ss = st.session_state
CUSTOM = "__custom__"

# ---- pick a game ----------------------------------------------------------------------------------------
try:
    season, week, games = common.slate(s["season"], s["week"])
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


if ss.pop("game_pick_new", False) or ss.get("game_choice") not in options:
    ss["game_choice"] = default_id if default_id in options else options[0]
choice = st.selectbox(f"Game ({season} week {week})", options, format_func=_label, key="game_choice")
neutral = playoff = False
if choice == CUSTOM:
    teams = common.schedule_index().get(season, {}).get("teams") or sorted({g["home_team"] for g in games})
    c1, c2, c3, c4 = st.columns([2, 2, 1, 1], vertical_alignment="bottom")
    ph = (pick or {}).get("home") if pick and default_id == CUSTOM else None
    pa = (pick or {}).get("away") if pick and default_id == CUSTOM else None
    if ss.get("custom_away") not in teams:
        ss["custom_away"] = pa if pa in teams else teams[0]
    if ss.get("custom_home") not in teams:
        ss["custom_home"] = ph if ph in teams else teams[1]
    away = c1.selectbox("Away", teams, key="custom_away")
    home = c2.selectbox("Home", teams, key="custom_home")
    neutral = c3.toggle("Neutral")
    playoff = c4.toggle("Playoff")
    gweek = None
    if home == away:
        st.warning("Pick two different teams.")
        st.stop()
else:
    g = by_id[choice]
    home, away, gweek = g["home_team"], g["away_team"], week
ss["game_pick"] = {"home": home, "away": away, "season": season, "week": gweek}

req = common.make_request(home, away, season, gweek, s, neutral=neutral, playoff=playoff)
summ = common.lookup(req)

bcol1, bcol2, _ = st.columns([2, 2, 3])
if summ is None:
    st.info(f"No result yet for {away} @ {home} at {s['sims']:,} sims, seed {s['seed']}.")
    if bcol1.button("Simulate", type="primary", width="stretch"):
        summ = common.run(req)
        if summ is not None:
            st.rerun()
    st.stop()
if bcol1.button("Re-simulate (ignore cache)", width="stretch",
                help="Runs again with the current data and overrides file, then replaces the cached result."):
    if common.run(req, force=True) is not None:
        st.rerun()

H, A = summ["matchup"]["home"], summ["matchup"]["away"]
n = summ["n_sims"]

# ---- headline ------------------------------------------------------------------------------------------
st.html('<div class="gs">' + hr.header_html(summ) + hr.tiles_html(summ) + hr.market_html(summ) + "</div>")

tabs = st.tabs(["Lines & charts", "Alt lines", "Scores", "Team stats", "Lineup", "Players", "Details"])

# ---- lines & charts ------------------------------------------------------------------------------------
with tabs[0]:
    m = summ.get("market") or {}
    sp_default = float((summ.get("spread") or {}).get("line", m.get("spread_home") if m.get("spread_home") is not None
                                                      else summ["fair"]["spread_home"]))
    t_default = float((summ.get("total") or {}).get("line", m.get("total") if m.get("total") is not None
                                                    else summ["fair"]["total"]))
    k = common.req_key(req)
    c1, c2 = st.columns(2)
    with c1:
        L = st.number_input(f"Spread ({H}, negative = {H} favored)", value=sp_default, step=0.5, format="%.1f",
                            key=f"calc_sp_{k}")
        r = cover_from_hist(summ["hist"]["margin"], L, "spread")
        away_txt, away_close = common.pm_text(r["miss"], n)
        st.html('<div class="gs card">'
                f'<div class="gc-row"><b>{hr.esc(H)} {hr.esc(hr.fmt_line(L))}</b></div>'
                f'<div class="gc-row">{hr.swatch(hr.C["home"])}{hr.esc(H)} covers {hr.pm_html({"p": r["hit"], "moe": r["moe"], "close": abs(r["hit"] - .5) <= r["moe"]})}</div>'
                f'<div class="gc-row">{hr.swatch(hr.C["away"])}{hr.esc(A)} covers {hr.esc(away_txt)}'
                f'{" <span class=close>" + hr.CLOSE_TEXT + "</span>" if away_close else ""}</div>'
                f'<div class="gc-row muted">push {hr.pct(r["push"])}</div></div>')
        st.plotly_chart(hr.margin_figure(summ, L), theme=None, config=hr.PLOTLY_CONFIG, key=f"mfig_{k}")
    with c2:
        T = st.number_input("Total", value=t_default, step=0.5, format="%.1f", min_value=0.0, key=f"calc_t_{k}")
        r = cover_from_hist(summ["hist"]["total"], T, "total")
        u_txt, u_close = common.pm_text(r["miss"], n)
        st.html('<div class="gs card">'
                f'<div class="gc-row"><b>Total {T:.1f}</b></div>'
                f'<div class="gc-row">{hr.swatch(hr.C["over"])}Over {hr.pm_html({"p": r["hit"], "moe": r["moe"], "close": abs(r["hit"] - .5) <= r["moe"]})}</div>'
                f'<div class="gc-row">{hr.swatch(hr.C["under"])}Under {hr.esc(u_txt)}'
                f'{" <span class=close>" + hr.CLOSE_TEXT + "</span>" if u_close else ""}</div>'
                f'<div class="gc-row muted">push {hr.pct(r["push"])}</div></div>')
        st.plotly_chart(hr.total_figure(summ, T), theme=None, config=hr.PLOTLY_CONFIG, key=f"tfig_{k}")
    st.caption("Type any line: cover % is counted exactly from the stored simulations (no re-sim).")
    st.html('<div class="gs">' + hr.lines_html(summ) + "</div>")

with tabs[1]:
    st.html('<div class="gs">' + hr.alt_html(summ) + "</div>")

with tabs[2]:
    st.html('<div class="gs">' + hr.scores_html(summ) + hr.quarters_html(summ) + hr.top_scores_html(summ) + "</div>")

with tabs[3]:
    st.html('<div class="gs">' + hr.team_stats_html(summ) + "</div>")

# ---- lineup ----------------------------------------------------------------------------------------------
with tabs[4]:
    av = summ.get("availability")
    if not av:
        st.info("This result was simulated without availability data.")
    else:
        st.html('<div class="gs">' + hr.availability_html(summ) + "</div>")
        av_week = av.get("week") or gweek
        ov_path = default_overrides_path(common.cfg(), season, av_week)
        existing = load_overrides(ov_path)
        st.markdown(f"##### Change the lineup — writes `{ov_path.relative_to(common.ROOT) if ov_path.is_relative_to(common.ROOT) else ov_path}`")
        st.caption("OUT = ruled out in every sim; IN = forced active (overrides the injury report); QB = forced starter. "
                   "Saved to the week's overrides file (the CLI reads the same file), then re-simulated.")
        picks = {}
        with st.form(f"lineup_{common.req_key(req)}"):
            cols = st.columns(2)
            for col, team in zip(cols, (H, A)):
                with col:
                    ent = existing.get(team) or {}
                    roster = common.team_roster(season, team, av_week)
                    pos = {r["name"]: r["pos"] for r in roster}
                    listed = [p["name"] for p in av["teams"].get(team, {}).get("listed", [])]
                    for p in av["teams"].get(team, {}).get("listed", []):
                        pos.setdefault(p["name"], p["pos"])
                    active = [r["name"] for r in roster if r["status"] in ("ACT", "INA") and r["pos"] in common.SKILL_POS]
                    out_opts = list(dict.fromkeys(active + list(ent.get("out") or [])))
                    in_opts = list(dict.fromkeys([nm for nm in listed] + active + list(ent.get("in") or [])))
                    qbs = list(dict.fromkeys([q["name"] for q in av["teams"].get(team, {}).get("qb", [])]
                                             + [r["name"] for r in roster if r["pos"] == "QB"]))
                    if ent.get("qb") and ent["qb"] not in qbs:
                        qbs.append(ent["qb"])
                    fmt = lambda nm: f"{nm} · {pos[nm]}" if pos.get(nm) else nm  # noqa: E731
                    st.markdown(f"**{team}**")
                    o = st.multiselect(f"{team} OUT", out_opts, default=list(ent.get("out") or []), format_func=fmt)
                    i_ = st.multiselect(f"{team} force IN", in_opts, default=list(ent.get("in") or []), format_func=fmt)
                    q_opts = ["(depth chart)"] + qbs
                    q = st.selectbox(f"{team} starting QB", q_opts,
                                     index=q_opts.index(ent["qb"]) if ent.get("qb") in q_opts else 0)
                    picks[team] = {"out": o, "in": i_, "qb": None if q == "(depth chart)" else q}
            submitted = st.form_submit_button("Re-sim with these changes", type="primary")
        if submitted:
            clash = [f"{t}: {nm}" for t, p in picks.items() for nm in set(p["out"]) & set(p["in"])]
            if clash:
                st.error("A player can't be both OUT and IN: " + ", ".join(clash))
            else:
                before = {t: dict(v) for t, v in existing.items()} if existing else {}
                new = {t: dict(v) if isinstance(v, dict) else v for t, v in existing.items()}
                for team, p in picks.items():
                    ent = dict(new.get(team) or {})
                    for key in ("out", "in", "qb"):
                        if p[key]:
                            ent[key] = p[key]
                        else:
                            ent.pop(key, None)
                    if ent:
                        new[team] = ent
                    else:
                        new.pop(team, None)
                had_file = ov_path.exists()
                save_overrides(ov_path, new)
                res = common.run(req, force=True, label=f"{A} @ {H} with lineup changes")
                if res is None:            # name did not match: put the file back the way it was
                    if had_file:
                        save_overrides(ov_path, before)
                    else:
                        ov_path.unlink(missing_ok=True)
                else:
                    from gridsim.userdata import auto_backup
                    auto_backup(common.cfg(), "lineup", min_interval_h=1)
                    st.rerun()
        if existing:
            with st.expander("Current overrides file"):
                st.code(ov_path.read_text(encoding="utf-8"), language="yaml")

# ---- players / props -----------------------------------------------------------------------------------
with tabs[5]:
    if summ.get("players") or summ.get("props"):
        st.html('<div class="gs">' + hr.players_html(summ) + "</div>")
    else:
        st.info("No player projections in this result (turn on 'Player projections' in the sidebar, if the engine "
                "build includes them).")

with tabs[6]:
    st.html('<div class="gs">' + hr.footer_html(summ) + "</div>")
    st.markdown("**Same run from the command line**")
    cmd = (f"gridsim run --home {H} --away {A} --season {season}" + (f" --week {gweek}" if gweek else "")
           + f" --sims {n} --seed {summ['meta'].get('seed')} --market-lines"
           + (" --neutral" if neutral else "") + (" --playoff" if playoff else "") + ("" if s["players"] else " --no-players"))
    st.code(cmd, language="bash")
    d1, d2 = st.columns(2)
    stem = f"{A}_at_{H}_{season}_s{summ['meta'].get('seed')}"
    d1.download_button("Download HTML report", data=lambda: hr.render_html(summ), file_name=f"{stem}.html",
                       mime="text/html", on_click="ignore", width="stretch")
    d2.download_button("Download JSON", data=lambda: json.dumps(summ, indent=2), file_name=f"{stem}.json",
                       mime="application/json", on_click="ignore", width="stretch")
