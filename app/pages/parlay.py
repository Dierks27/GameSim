"""Parlay builder: same-game parlay legs from dropdowns -> gridsim.combos syntax -> priced from the sims
(joint % counted from simulations where every leg hits, vs the naive product of the legs)."""
from __future__ import annotations

import streamlit as st

import common
import helpers

hr = common.hr
ss = st.session_state
s = common.settings()

PLAYER_STATS = {"pass_yds": "Passing yards", "pass_td": "Passing TDs", "comp": "Completions", "pass_att": "Pass attempts",
                "ints": "Interceptions", "rush_yds": "Rushing yards", "rush_att": "Rush attempts", "rush_td": "Rushing TDs",
                "rec": "Receptions", "rec_yds": "Receiving yards", "targets": "Targets", "rec_td": "Receiving TDs",
                "rush_rec_yds": "Rush + rec yards", "anytime_td": "Anytime TD"}

common.page_header("Parlay builder", "Pick legs, then price the combination from the simulations: correlated legs "
                                     "(QB yards + his team winning) are counted together, not multiplied.")
g = common.pick_game("parlay")
H, A = g["home"], g["away"]
gkey = f"{g['season']}_{A}@{H}"
legs: list[str] = ss.setdefault("parlay_legs", {}).setdefault(gkey, [])

# player names: from a simulated result with players if one exists (exact names the sim uses), else rosters
base = common.lookup(common.make_request(H, A, g["season"], g["week"], {**s, "players": True},
                                         neutral=g["neutral"], playoff=g["playoff"]))


def player_names(team: str) -> list[str]:
    if base and (base.get("players") or {}).get(team):
        return [p["name"] for p in base["players"][team]]
    try:
        roster = common.team_roster(g["season"], team, g["week"])
    except Exception:
        roster = []
    return [r["name"] for r in roster if r["pos"] in ("QB", "RB", "WR", "TE") and r["status"] in ("ACT", "INA", "")]


# ---- leg builder ---------------------------------------------------------------------------------------
with st.container(border=True):
    st.markdown("**Add a leg**")
    kind = st.selectbox("Leg type", list(helpers.LEG_KINDS), format_func=helpers.LEG_KINDS.get, key="pl_kind")
    team = player = stat = None
    op, line = ">", None
    if kind in ("team_win", "team_lose", "team_cover", "team_pts", "player"):
        c1, c2 = st.columns(2)
        team = c1.selectbox("Team", [H, A], key="pl_team")
        if kind == "player":
            names = player_names(team)
            if not names:
                c2.warning("No players found for this team.")
            player = c2.selectbox("Player", names, key="pl_player") if names else None
    if kind == "team_cover":
        line = st.number_input(f"{team} spread (negative = {team} favored)", value=-3.5, step=0.5, format="%.1f", key="pl_cover")
    elif kind in ("team_pts", "total", "margin", "player"):
        c1, c2, c3 = st.columns([2, 1, 1], vertical_alignment="bottom")
        if kind == "player":
            stat = c1.selectbox("Stat", list(PLAYER_STATS), format_func=PLAYER_STATS.get, key="pl_stat")
        else:
            c1.markdown({"team_pts": f"**{team} points**", "total": "**Total points**",
                         "margin": f"**{H} margin ({H} − {A})**"}[kind])
        if stat == "anytime_td":
            op, line = ">", 0.5
            c2.markdown("scores")
        else:
            op = ">" if c2.radio("Side", ["Over", "Under"], horizontal=True, key="pl_side",
                                 label_visibility="collapsed") == "Over" else "<"
            default = {"team_pts": 20.5, "total": 44.5, "margin": 3.5, "player": 49.5}[kind]
            line = c3.number_input("Line", value=default, step=0.5, format="%.1f", key=f"pl_line_{kind}",
                                   min_value=None if kind == "margin" else 0.0)
    if st.button("Add leg", icon=":material/add:"):
        try:
            leg = helpers.leg_string(kind, team=team, op=op, line=line, player=player, stat=stat)
        except ValueError as e:
            st.error(str(e))
        else:
            if leg in legs:
                st.warning("That leg is already in the parlay.")
            else:
                legs.append(leg)
                st.rerun()

# ---- current legs --------------------------------------------------------------------------------------
st.markdown(f"##### Legs ({len(legs)})")
if not legs:
    st.caption("No legs yet. Example: BUF:win; Josh Allen:pass_yds>229.5; total>45.5")
for i, leg in enumerate(list(legs)):
    c1, c2 = st.columns([6, 1], vertical_alignment="center")
    c1.code(leg, language=None)
    if c2.button("Remove", key=f"pl_rm_{gkey}_{i}", width="stretch"):
        legs.remove(leg)
        st.rerun()
if not legs:
    st.stop()

combo = helpers.combo_string(legs)
need_players = any(not (l.split(":")[0].strip().upper() in (H, A) or l.lower().startswith(("total", "margin"))) for l in legs)
req = common.make_request(H, A, g["season"], g["week"], {**s, "players": True if need_players else s["players"]},
                          neutral=g["neutral"], playoff=g["playoff"], combos=[combo])
summ = common.lookup(req)
c1, c2, c3 = st.columns([2, 2, 2])
priced = c1.button("Price it" if summ is None else "Re-price (ignore cache)", type="primary" if summ is None else "secondary",
                   width="stretch")
if c2.button("Clear legs", width="stretch"):
    legs.clear()
    st.rerun()
c3.caption(f"{s['sims']:,} sims · seed {s['seed']}")
st.caption("Combo string (same as `gridsim run --combo`):")
st.code(combo, language=None)
if priced:
    summ = common.run(req, force=summ is not None, label=f"Pricing {A} @ {H} parlay")
    if summ is not None:
        st.rerun()
if summ is None:
    st.stop()

res = next((c for c in summ.get("combos") or [] if c.get("combo") == combo), None)
if res is None:
    st.error("The result does not contain this combo — re-price it.")
    st.stop()
n = summ["n_sims"]
j = res["joint"]
lift = res.get("correlation_lift")
st.html(common.kv_html([
    ("Joint (from sims)", common.pm_span(j["p"], j["moe"]), f"{round(j['p'] * n):,} of {n:,} sims hit every leg"),
    ("Naive product", hr.esc(hr.pct(res["naive"])), "legs treated as independent"),
    ("Correlation lift", f"×{lift:.2f}" if lift else "–",
     ("legs help each other" if lift and lift > 1.02 else "legs work against each other" if lift and lift < 0.98
      else "about independent") if lift else "a leg never hits"),
    ("Fair odds", hr.esc(res["fair_american"]), "American, no vig"),
]))
rows = [[hr.esc(l["leg"]), common.pm_span(l["p"], l["moe"])] for l in res["legs"]]
st.html('<div class="gs card"><h2>Legs</h2>' + hr.table_html(["Leg", "Hits in sims"], rows) + "</div>")
st.caption("Joint = share of simulated games where every leg hit. Lift = joint ÷ naive product: above 1 the legs "
           "tend to happen together, so the parlay is worth more than multiplying the legs suggests.")
