"""Parlays: (1) Auto parlay — combinations of this week's Kalshi edges, one leg per game, sorted from safer to long
shot (gridsim.parlay); (2) Build your own — legs from dropdowns in one or several games; legs of the same game are
counted together from the sims (gridsim.combos), games are multiplied. READ-ONLY: numbers, never orders."""
from __future__ import annotations

import datetime as dt

import streamlit as st

import common

common.via_app(common.PARLAY_PAGE)
import helpers
from gridsim import parlay as P
from gridsim.report import american

hr = common.hr
ss = st.session_state
s = common.settings()

PLAYER_STATS = {"pass_yds": "Passing yards", "pass_td": "Passing TDs", "comp": "Completions", "pass_att": "Pass attempts",
                "ints": "Interceptions", "rush_yds": "Rushing yards", "rush_att": "Rush attempts", "rush_td": "Rushing TDs",
                "rec": "Receptions", "rec_yds": "Receiving yards", "targets": "Targets", "rec_td": "Receiving TDs",
                "rush_rec_yds": "Rush + rec yards", "anytime_td": "Anytime TD"}

st.html('<div class="gs"><h1>Parlays <span class="readonly">READ-ONLY</span></h1><p class="sub">A parlay pays only '
        "when every leg wins. <b>Auto parlay</b> puts the sides GridSim likes this week together for you, from safer "
        "to long shot. <b>Build your own</b> prices any legs you pick, in one game or across several.</p></div>")
auto_tab, build_tab = st.tabs(["Auto parlay", "Build your own"])

# ---- auto parlay ---------------------------------------------------------------------------------------
with auto_tab:
    wb = common.week_board(s["season"], s["week"])
    if not any(g.get("rows") for g in wb["games"].values()):
        st.info(common.board_empty_text(wb, "**Scan now** on the Signals page"))
    else:
        try:
            when = dt.datetime.fromisoformat(wb["ts_utc"]).astimezone().strftime("%a %b %d, %I:%M %p").replace(" 0", " ")
        except (TypeError, ValueError):
            when = "the last price check"
        c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
        with c1:
            band = common.price_filter()
        max_legs = c2.slider("Most legs in a parlay", 2, 4, key=common.keep("ap_legs", 3),
                             help="More legs = bigger payout, smaller chance. Every extra leg is one more number that "
                                  "has to be right.")
        res = common.auto_parlays(s["season"], s["week"], band, int(max_legs))
        st.caption(f"Built from Kalshi's prices as of {when}: {res['legs_considered']} legs GridSim likes in "
                   f"{res['games_with_edges']} games, {res['checked']:,} combinations checked"
                   + (" (the search stopped there: more exist)" if res.get("partial") else "") + ". One leg per game.")
        common.callout(
            "<b>How these are made</b><ul>"
            "<li>Legs are sides GridSim rates at least "
            f"{100 * float(common.cfg().scanner['min_edge']):.0f} points above their cost, in markets that are not "
            "thin. One leg per game; different games are treated as independent, so chances are multiplied.</li>"
            "<li><b>All hit</b> is the <b>adjusted</b> chance — the best estimate, built from the market's own "
            "numbers (see the box under the lists). GridSim's own, more optimistic number is next to it.</li>"
            "<li><b>Costs</b> is the legs' costs today multiplied together: what $1 of payout would cost <i>if</i> "
            "the legs could be bought one after another at these prices, each win paying for the next leg. It is a "
            "yardstick, not a way to buy. Games played at the same time cannot be bought one after another (those "
            "parlays are marked <b>same-time games</b>), and buying the legs separately is not a parlay: each leg then "
            "wins or loses on its own. Kalshi sells combinations itself, at its own price, which can be different from "
            "this number — look before you buy.</li>"
            "<li><b>Value per $1</b> is by the adjusted chance. The fees and the gap between buy and sell prices cost "
            "roughly 5 to 8% on every leg, so a parlay is worth about 10% less than it costs with two legs and about "
            "15% less with three. The lists put the least costly ones first.</li>"
            "<li>Safer / middle / long shot describe how often it hits. None of this is advice.</li></ul>", tone="warn")
        heads = {"safer": ("Safer", "hits at least about 1 time in 3"),
                 "middle": ("Middle", "hits between 1 time in 3 and 1 time in 8"),
                 "long": ("Long shot", "hits less than 1 time in 8 — big payout, usually loses")}
        any_found = False
        for key, _, lo, hi in P.TIERS:
            title, sub = heads[key]
            st.markdown(f"#### {title} <span style='font-weight:400;font-size:.8em;color:var(--muted,#898781)'>— "
                        f"{sub}</span>", unsafe_allow_html=True)
            pars = res["tiers"][key]
            if not pars:
                st.caption("Nothing in this group with these choices.")
                continue
            any_found = True
            for i, par in enumerate(pars, 1):
                with st.container(border=True):
                    st.html(common.kv_html([
                        (f"#{i} · {par['n_legs']} legs · all hit", f"{100 * par['p']:.1f}%",
                         (f"about 1 time in {1 / par['p']:.0f}" if par["p"] < 0.5 else "more often than not")
                         + f" · GridSim alone: {100 * par['p_sim']:.1f}%"),
                        ("Costs", f"{100 * par['cost']:.1f}¢", "per $1 of payout, fees included"),
                        ("$1 returns", f"${par['pays']:.2f}", "if every leg wins"),
                        ("Value per $1", f"{100 * par['ev_per_dollar']:+.0f}%",
                         f"by the adjusted chance · {100 * par['ev_sim']:+.0f}% if GridSim alone were right"),
                    ]))
                    st.markdown("\n".join(
                        f"- **{x['matchup']}** — {x['what']} · costs {100 * x['cost']:.0f}¢ · market "
                        f"{100 * x['p_market']:.0f}% · GridSim {100 * x['p_side']:.0f}% "
                        f"{common.RATING_LABEL[x['rating']].split()[0]}"
                        + (f" · [open on Kalshi]({x['url']})" if x.get("url") else "") for x in par["legs"]))
                    if not par.get("can_roll"):
                        st.caption("**Same-time games:** these legs cannot be bought one after another, so the cost and "
                                   "the return above are what a combination at these prices would be — not what "
                                   "buying the legs separately gives.")
        if not any_found:
            old = any("sim is" in c_ for g_ in wb["games"].values() for r_ in (g_.get("rows") or [])
                      for c_ in (r_.get("catches") or []))
            if old:
                st.info("The simulations behind this list are a few hours old, so GridSim is not putting parlays "
                        "together from them. The next price check re-simulates the games by itself (within about "
                        "45 minutes while GridSim is open) — or press **Scan now** on the Signals page.")
            else:
                st.info("No parlay can be built right now: fewer than two games have a side GridSim likes in a market "
                        "that is not thin, with these choices. Try **All prices**, or look again after the next price "
                        "check.")
        else:
            common.blend_note()

# ---- build your own ------------------------------------------------------------------------------------
with build_tab:
    st.caption("Pick a game, add legs, pick another game, add more. Legs in the same game are counted together from "
               "the simulations (a QB's yards and his team winning go together); different games are multiplied.")
    g = common.pick_game("parlay")
    H, A = g["home"], g["away"]
    gkey = f"{g['season']}_{A}@{H}"
    all_legs: dict = ss.setdefault("parlay_legs", {})
    games_meta: dict = ss.setdefault("parlay_games", {})
    legs: list[str] = all_legs.setdefault(gkey, [])
    games_meta[gkey] = {k: g[k] for k in ("home", "away", "season", "week", "neutral", "playoff")}

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

    with st.container(border=True):
        st.markdown(f"**Add a leg to {A} @ {H}**")
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
            line = st.number_input(f"{team} spread (negative = {team} favored)", value=-3.5, step=0.5, format="%.1f",
                                   key="pl_cover")
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

    # ---- the whole parlay: every game that has legs ----
    active = {k: v for k, v in all_legs.items() if v and k in games_meta}
    n_legs = sum(len(v) for v in active.values())
    st.markdown(f"##### Your parlay — {n_legs} leg{'s' if n_legs != 1 else ''} in {len(active)} game"
                f"{'s' if len(active) != 1 else ''}")
    if not active:
        st.caption("No legs yet. Example: BUF wins + Josh Allen over 229.5 passing yards, then another game's total.")
        st.stop()
    reqs, results = {}, {}
    for k, lg in active.items():
        m = games_meta[k]
        st.markdown(f"**{m['away']} @ {m['home']}**")
        for i, leg in enumerate(list(lg)):
            c1, c2 = st.columns([6, 1], vertical_alignment="center")
            c1.code(leg, language=None)
            if c2.button("Remove", key=f"pl_rm_{k}_{i}", width="stretch"):
                lg.remove(leg)
                st.rerun()
        combo = helpers.combo_string(lg)
        need_players = any(not (x.split(":")[0].strip().upper() in (m["home"], m["away"])
                                or x.lower().startswith(("total", "margin"))) for x in lg)
        reqs[k] = (common.make_request(m["home"], m["away"], m["season"], m["week"],
                                       {**s, "players": True if need_players else s["players"]},
                                       neutral=m["neutral"], playoff=m["playoff"], combos=[combo]), combo)
        results[k] = common.lookup(reqs[k][0])
    todo = [k for k in active if results[k] is None]
    c1, c2, c3 = st.columns([2, 2, 2])
    priced = c1.button("Price it" if todo else "Re-price (ignore cache)", type="primary" if todo else "secondary",
                       width="stretch")
    if c2.button("Clear all legs", width="stretch"):
        all_legs.clear()
        st.rerun()
    c3.caption(f"{s['sims']:,} sims per game · seed {s['seed']}")
    if priced:
        run = todo or list(active)
        bar = st.progress(0.0)
        ok = True
        for i, k in enumerate(run):
            m = games_meta[k]
            ok = common.run(reqs[k][0], force=not todo, bar=bar, label=f"Pricing {m['away']} @ {m['home']}",
                            frac=(i / len(run), (i + 1) / len(run))) is not None and ok
        bar.empty()
        if ok:
            st.rerun()
    if todo:
        st.stop()

    per_game = {}
    for k in active:
        res_k = next((c for c in results[k].get("combos") or [] if c.get("combo") == reqs[k][1]), None)
        if res_k is None:
            st.error("A result does not contain its legs — press **Re-price**.")
            st.stop()
        per_game[k] = res_k
    whole = P.combine([{"p": r["joint"]["p"], "moe": r["joint"]["moe"]} for r in per_game.values()])
    pj = whole["p"]
    st.html(common.kv_html([
        ("Every leg hits", common.pm_value(pj, whole["moe"]),
         ("GridSim alone, from the sims" + (f" · about 1 time in {1 / pj:.0f}" if 0 < pj < 0.5 else "")) if pj
         else ("a leg never hit in the sims" if any(x["p"] == 0 for r in per_game.values() for x in r["legs"])
               else "the legs never all hit together in the sims")),
        ("Fair return", f"${1 / pj:.2f} per $1" if pj else "–", "break-even payout if GridSim's chance were exactly right"),
        ("Fair odds", hr.esc(american(pj)) if 0 < pj < 1 else "–", "American, no vig"),
        ("Games", f"{len(per_game)}", "multiplied together" if len(per_game) > 1 else "one game: counted from the sims"),
    ]))
    for k, r in per_game.items():
        m = games_meta[k]
        j, lift = r["joint"], r.get("correlation_lift")
        n = results[k]["n_sims"]
        rows = [[hr.esc(x["leg"]), common.pm_span(x["p"], x["moe"])] for x in r["legs"]]
        note = ""
        if len(r["legs"]) > 1:
            note = (f' · legs multiplied would give {hr.pct(r["naive"])}'
                    + (f' (×{lift:.2f}: {"they help each other" if lift > 1.02 else "they work against each other" if lift < 0.98 else "about independent"})'
                       if lift else ""))
        st.html(f'<div class="gs card"><h2>{hr.esc(m["away"])} @ {hr.esc(m["home"])} — '
                f'{common.pm_span(j["p"], j["moe"])}<span class="muted" style="font-weight:400;font-size:13px"> · '
                f'{round(j["p"] * n):,} of {n:,} sims{hr.esc(note)}</span></h2>'
                + hr.table_html(["Leg", "Hits in sims"], rows) + "</div>")
    st.caption("Within a game, the chance is the share of simulated games where every leg hit — legs that tend to "
               "happen together are worth more than multiplying them suggests. Different games are treated as "
               "independent and multiplied. These are GridSim's own chances, from the simulations alone — not the "
               "adjusted chance on the Auto parlay tab, which starts from the market's numbers and can be quite "
               "different. The arithmetic: a parlay that costs 25¢ per dollar of payout has to hit 1 time in 4 to break "
               "even. A price below GridSim's chance is GridSim's lean — an opinion, not a measured edge.")
