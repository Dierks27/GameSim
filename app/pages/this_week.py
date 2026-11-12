"""This Week: the slate as cards (market lines, sim result if one exists at the current settings, sim-vs-market gap)."""
from __future__ import annotations

import streamlit as st

import common

common.via_app(common.WEEK_PAGE)

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

# Kalshi edges per game, from the last price check (no network here)
wb = common.week_board(s["season"], s["week"])
band = common.price_filter()


def _edges(gid: str) -> list[dict]:
    """Edges after adjusting, then GridSim's leans (the board lists the cleanest first)."""
    rows = (wb["games"].get(gid) or {}).get("rows") or []
    return [r for r in rows if r["rating"] in common.EDGE_RATINGS + ("lean",) and common.in_band(r, band)]


def _kalshi_line(gid: str) -> str:
    g_ = wb["games"].get(gid)
    if g_ is None:
        return ""
    if g_.get("rows") is None:
        return f"Kalshi: {g_.get('note') or 'no prices yet'}"
    ed = _edges(gid)
    real = [r for r in ed if r["rating"] in common.EDGE_RATINGS]
    if not ed:
        return "Kalshi: no edge and no lean at these prices"
    b = ed[0]
    head = (f"**{len(real)} edge{'s' if len(real) != 1 else ''}**" if real else "no edge after adjusting")
    leans = len(ed) - len(real)
    return (f"Kalshi: {head}" + (f" · GridSim leans to {leans}" if leans else "") + f" · top: {b['what']} at "
            f"{100 * b['cost']:.0f}¢ (market {100 * b['p_market']:.0f}%, GridSim {100 * b['p_side']:.0f}%) "
            f"{common.RATING_LABEL[b['rating']].split()[0]}")


if wb.get("ts_utc") and any(g_.get("rows") for g_ in wb["games"].values()):
    import datetime as _dt
    try:
        when = _dt.datetime.fromisoformat(wb["ts_utc"]).astimezone().strftime("%a %I:%M %p").replace(" 0", " ")
    except ValueError:
        when = "the last price check"
    st.caption(f"The Kalshi line on each card is from the last price check ({when}). An **edge** is what is left "
               "after adjusting GridSim's number toward the market's; a **lean** ⚪ is GridSim's own opinion. Open a "
               "game for the full list.")
else:
    st.caption(common.board_empty_text(wb, "**Scan now** on the Signals page"))

c1, c2, c3 = st.columns([3, 2, 2], vertical_alignment="bottom")
with c1:
    sort = st.radio("Sort by", ["Kickoff", "Biggest spread gap", "Strongest Kalshi lean"], horizontal=True, key="tw_sort")
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
elif sort == "Strongest Kalshi lean":
    top = {g["game_id"]: max((max(r["edge"], r["lean"]) for r in _edges(g["game_id"])), default=None) for g in games}
    ordered = sorted(games, key=lambda g: (top[g["game_id"]] is None, -(top[g["game_id"]] or 0), _kick(g)))
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
            kl = _kalshi_line(gid)
            if kl:
                st.caption(kl)
            b1, b2 = st.columns(2)
            if results[gid] is None:
                if b1.button("Sim this game", key=f"sim_{gid}", type="primary", width="stretch"):
                    if common.run(reqs[gid]) is not None:
                        st.rerun()
            if (b1 if results[gid] else b2).button("Open game", key=f"open_{gid}", width="stretch"):
                common.open_game(g["home_team"], g["away_team"], season, week)

st.caption("Spreads from the home team's perspective (negative = home favored). Market = nflverse schedule lines. "
           "Gap = market spread − sim fair spread (median margin).")
