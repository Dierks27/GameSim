"""This Week: the slate as cards (market lines, sim result if one exists at the current settings, sim-vs-market gap)."""
from __future__ import annotations

import streamlit as st

import common

hr = common.hr
s = common.settings()

try:
    season, week, games = common.slate(s["season"], s["week"])
except Exception as e:
    st.error(f"Could not load the schedule ({e}). Open **Weekly routine** and press **Refresh data**, then reload this page.")
    st.stop()

reqs = {g["game_id"]: common.make_request(g["home_team"], g["away_team"], season, week, s) for g in games}
results = {gid: common.lookup(r) for gid, r in reqs.items()}

st.html(f'<div class="gs"><h1>{season} · Week {week}</h1><p class="sub">{len(games)} games · '
        f'{s["sims"]:,} sims per game · seed {s["seed"]}'
        f'{" · player projections on" if s["players"] else ""}</p></div>')

# data freshness: from a simulated game's availability block if we have one, else local file times
fresh = next((r["availability"]["freshness"] for r in results.values() if r and r.get("availability")), None)
lines = hr.freshness_text(fresh) if fresh else common.file_freshness(season)
st.caption("**Data** — " + " · ".join(lines))

c1, c2, c3 = st.columns([3, 2, 2], vertical_alignment="bottom")
with c1:
    sort = st.radio("Sort by", ["Kickoff", "Biggest spread gap"], horizontal=True, key="tw_sort")
todo = [g for g in games if results[g["game_id"]] is None]
with c2:
    sim_all = st.button(f"Sim all ({len(todo)} left)" if todo else "All simulated", type="primary",
                        disabled=not todo, width="stretch")
with c3:
    done = sum(r is not None for r in results.values())
    st.caption(f"{done}/{len(games)} simulated at these settings. Cached results load instantly.")

if sim_all and todo:
    bar = st.progress(0.0)
    for i, g in enumerate(todo):
        common.run(reqs[g["game_id"]], bar=bar, label=f"{i + 1}/{len(todo)} {g['away_team']} @ {g['home_team']}",
                   frac=(i / len(todo), (i + 1) / len(todo)))
    bar.empty()
    st.rerun()


def _kick(g):
    return (str(g.get("gameday") or ""), str(g.get("gametime") or ""))


if sort == "Kickoff":
    ordered = sorted(games, key=_kick)
else:
    gaps = {g["game_id"]: common.spread_gap(g, results[g["game_id"]]) for g in games}
    ordered = sorted(games, key=lambda g: (gaps[g["game_id"]] is None, -abs(gaps[g["game_id"]] or 0), _kick(g)))

cols_per_row = 2
for i in range(0, len(ordered), cols_per_row):
    cols = st.columns(cols_per_row)
    for col, g in zip(cols, ordered[i:i + cols_per_row]):
        gid = g["game_id"]
        with col, st.container(border=True):
            slot = st.empty()
            slot.html(common.game_card_html(g, results[gid]))
            b1, b2 = st.columns(2)
            if results[gid] is None:
                if b1.button("Sim this game", key=f"sim_{gid}", type="primary", width="stretch"):
                    if common.run(reqs[gid]) is not None:
                        st.rerun()
            if (b1 if results[gid] else b2).button("Open game", key=f"open_{gid}", width="stretch"):
                common.open_game(g["home_team"], g["away_team"], season, week)

st.caption("Spreads from the home team's perspective (negative = home favored). Market = nflverse schedule lines. "
           "Gap = market spread − sim fair spread (median margin).")
