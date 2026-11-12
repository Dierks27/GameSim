"""My positions: the position watch. You tell GridSim which Kalshi contracts you hold; it watches the price, the
injury report and its own number, and alerts you. READ-ONLY: GridSim never trades and never sees your account.
All logic is in gridsim.positions (the same code the scheduled price checks run)."""
from __future__ import annotations

import datetime as dt

import streamlit as st

import common

common.via_app(common.POSITIONS_PAGE)
from gridsim import api
from gridsim import positions as pos
from gridsim.markets import sim_probs

hr = common.hr
ss = st.session_state
s = common.settings()
cfg = common.cfg()

st.html('<div class="gs"><h1>My positions <span class="readonly">READ-ONLY</span></h1><p class="sub">Tell GridSim '
        "what you bought on Kalshi and it keeps watch: a sudden price move, an injury or lineup change, GridSim "
        "changing its own number — you get an alert with the facts. GridSim <b>never places orders</b> and never "
        "sees your Kalshi account; a position here is only a note you enter. What to do is always your call.</p></div>")

msg_box = st.container()          # always present, so the widgets below keep their place (and stay open)
if ss.get("pos_msg"):
    kind, msg = ss.pop("pos_msg")
    with msg_box:
        (st.success if kind == "ok" else st.error)(msg)


def _local(ts: str | None) -> str:
    try:
        return dt.datetime.fromisoformat(str(ts)).astimezone().strftime("%a %b %d, %I:%M %p").replace(" 0", " ")
    except (TypeError, ValueError):
        return "—"


def _cents(x) -> str:
    return "—" if x is None else f"{100 * float(x):.1f}".rstrip("0").rstrip(".") + "¢"


def _pct(x) -> str:
    return "—" if x is None else f"{100 * float(x):.0f}%"


def _usd(x) -> str:
    return "—" if x is None else f"{'+' if x >= 0 else '−'}${abs(float(x)):,.2f}"


# ---- add ---------------------------------------------------------------------------------------------------------------
with st.expander("Add a position", expanded=bool(ss.get("pos_adding")) or not pos.open_positions(cfg),
                 icon=":material/add_circle:"):
    try:
        season, week, games = common.slate(s["season"], s["week"])
        now = dt.datetime.now(dt.timezone.utc)
        from gridsim.live import kickoff_utc
        games = [g for g in games if g.get("home_score") is None and (kickoff_utc(g) is None or kickoff_utc(g) > now)]
    except Exception as e:
        games = []
        st.warning(f"Schedule not available ({e}). Open **Weekly routine** and press **Refresh data**.")
    if not games:
        st.caption("No upcoming games in the selected week (change the week in the sidebar Settings box).")
    else:
        gkey = common.keep("pos_game", 0)
        if ss[gkey] not in range(len(games)):
            ss[gkey] = 0
        gi = st.selectbox("Game", range(len(games)), key=gkey,
                          format_func=lambda i: f"{games[i]['away_team']} @ {games[i]['home_team']} · "
                                                f"{games[i].get('weekday', '')} {games[i].get('gametime', '')}")
        g = games[gi]
        mk = common.kalshi_contracts(g["game_id"], g["home_team"], g["away_team"], g.get("gameday"))
        if mk.get("error"):
            st.error(f"Could not read Kalshi's prices for this game ({mk['error']}).")
            st.button("Try again", key="pos_retry", icon=":material/refresh:")      # (any click reads them again)
            mk = None
        if mk:
            kinds = {"Who wins (moneyline)": "moneyline", "Spread": "spread", "Total points": "total"}
            kind = kinds[st.radio("Market", list(kinds), horizontal=True, key="pos_kind")]
            # GridSim's number right now, from the same cached sim the price checks use (filled in at the first
            # check if there is none yet)
            summ = api.cached_summary(api.RunRequest(home=g["home_team"], away=g["away_team"], season=season,
                                                     week=week, market_lines=True), cfg)
            rows = sim_probs(summ, mk) if summ else None
            if kind == "moneyline":
                opts = [{"market": kind, "label": f"{t} wins", **q} for t, q in mk["moneyline"].items()]
            elif kind == "spread":
                opts = [{"market": kind, "label": f"{q['team']} wins by {q['line']}+", **q} for q in mk["spread"]]
            else:
                opts = [{"market": kind, "label": f"over {q['line']}", **q} for q in mk["total"]]
            if not opts:
                st.caption("Kalshi lists no contracts of this type for this game right now.")
            else:
                # every widget below is keyed by what it belongs to (game, market, contract, side): with one shared
                # key the box kept showing the previous game's contract and the first price it ever held
                labels = [o["label"] for o in opts]
                pick = st.selectbox("Contract", labels, key=f"pos_contract_{g['game_id']}_{kind}")
                c = opts[labels.index(pick)] if pick in labels else opts[0]
                side = st.radio("You bought", ["Yes", "No"], horizontal=True, key=f"pos_side_{c['ticker']}",
                                help="Yes pays $1 if the contract's statement comes true; No pays $1 if it doesn't.")
                ask = c.get("yes_ask") if side == "Yes" else c.get("no_ask")
                what = c["label"] if side == "Yes" else f"NOT “{c['label']}”"
                st.caption(f"You hold: **{what}** — pays $1 per contract if that happens. Kalshi's price to buy it "
                           f"right now: **{_cents(ask)}**.")
                a, b = st.columns(2)
                price_c = a.number_input("Price you paid per contract (¢)", 1, 99,
                                         min(99, max(1, int(round(100 * ask)))) if ask and 0 < ask < 1 else 50,
                                         key=f"pos_price_{c['ticker']}_{side}",
                                         help="Starts at Kalshi's current price for this contract and side; change it "
                                              "to what you actually paid.")
                n = b.number_input("How many contracts", 1, 100000, 10, key="pos_n")
                p_entry = None
                if rows:
                    r = next((x for x in rows if x.get("ticker") == c.get("ticker")), None)
                    if r:
                        p_entry = r["p_yes"] if side == "Yes" else 1 - r["p_yes"]
                if st.button("Add to my positions", type="primary", icon=":material/visibility:"):
                    try:
                        game = {**g, "season": season, "week": week}
                        pos.add_position(cfg, game=game, market=kind, ticker=c["ticker"], label=c["label"],
                                         side=side.upper(), price=price_c / 100, contracts=int(n),
                                         p_side_entry=p_entry, url=c.get("url"))
                        ss["pos_msg"] = ("ok", f"Watching {c['label']} ({side}) ×{int(n)} at {price_c}¢. "
                                               "You'll be alerted about price jumps, lineup news and changes in "
                                               "GridSim's number.")
                        ss["pos_adding"] = True          # keep the form open: people usually enter several
                        st.rerun()
                    except Exception as e:
                        st.error(f"Not added — {e}")

# ---- open --------------------------------------------------------------------------------------------------------------
allp = pos.load_positions(cfg)
openp = [p for p in allp if p.get("status") == "open"]
last = pos.last_view(cfg)
st.markdown("#### Open positions")
c1, c2 = st.columns([2, 3], vertical_alignment="center")
if c1.button("Check now", icon=":material/refresh:", width="stretch", disabled=not openp):
    with st.spinner("Checking prices, injuries and GridSim's numbers …"):
        try:
            alerts = pos.watch(cfg, notify_on=False, verbose=False)
            probs = dict(pos.LAST_PROBLEMS)
            bad = [b for k, b in probs.items() if not k.startswith("_")]
            if "_busy" in probs:
                ss["pos_msg"] = ("err", "Not checked: the scheduled check is running right now. Press **Check now** "
                                        "again in a minute.")
            elif "_alerts" in probs:
                ss["pos_msg"] = ("err", "Checked — " + probs["_alerts"]["what"])
            elif bad or "_state" in probs:
                ss["pos_msg"] = ("err", "Checked, but not everything went through: "
                                 + "; ".join(f"{b['matchup']}: {b['what']}" for b in probs.values())
                                 + ". Numbers that could not be read are from the last successful check."
                                 + (f" {len(alerts)} new alert(s) — see below." if alerts else ""))
            else:
                ss["pos_msg"] = ("ok", "Checked. " + (f"{len(alerts)} new alert(s) — see below." if alerts
                                                      else "Nothing new since the last check."))
        except Exception as e:
            ss["pos_msg"] = ("err", f"Check failed — {type(e).__name__}: {e}")
    st.rerun()
c2.caption(f"Last check: {_local(last.get('checked_utc'))}. With the background jobs on (Settings > Schedule), positions are checked "
           "every 30 minutes, and every 10 minutes in the last "
           f"{cfg.watch.get('near_kickoff_hours', 3)} hours before kickoff, with alerts to your phone.")
for pr in (last.get("problems") or {}).values():
    st.warning(f"At the last check, {pr['matchup']}: {pr['what']}. Numbers for that game are from the last successful "
               "check.", icon=":material/cloud_off:")
if not openp:
    st.caption("No open positions. Add one above after you buy a contract on Kalshi.")
for p in openp:
    v = last["positions"].get(p["id"], {})
    with st.container(border=True):
        st.markdown(f"**{p['matchup']} — {p['label']} ({p['side'].title()}) × {p['contracts']}**"
                    + (f" · [open on Kalshi]({p['url']})" if p.get("url") else ""))
        st.html(common.kv_html([
            ("You paid", _cents(p["price"]), f"${p['price'] * p['contracts']:,.2f} in total, before fees"),
            ("Kalshi now", _cents(v.get("mid")), f"selling now ≈ {_cents(v.get('sell'))} each"),
            ("If sold now", _usd(v.get("if_sold_now")), "after Kalshi's fees on both trades"),
            ("GridSim's chance", _pct(v.get("p_side")),
             f"when you added it: {_pct(p.get('p_side_entry'))} · break-even ≈ {_pct(p['price'])}"),
            ("Kickoff", _local(p.get("kickoff_utc")), f"prices read {_local(v.get('ts'))}"),
        ]))
        a, b, c = st.columns([2, 2, 3])
        with a.popover("I sold it", icon=":material/sell:"):
            sp = st.number_input("Price you sold at (¢)", 0, 100, int(round(100 * (v.get("sell") or p["price"]))),
                                 key=f"pos_sell_{p['id']}")
            if st.button("Record the sale", key=f"pos_sellgo_{p['id']}", type="primary"):
                r = pos.close_position(cfg, p["id"], sp / 100)
                ss["pos_msg"] = ("ok", f"Recorded: sold at {sp}¢, {_usd(r['pnl'])} after fees." if r else "Already closed.")
                st.rerun()
        with b.popover("Remove", icon=":material/delete:"):
            st.caption("Only for a position entered by mistake — it disappears without a trace.")
            if st.button("Yes, remove it", key=f"pos_del_{p['id']}"):
                pos.delete_position(cfg, p["id"])
                st.rerun()

# ---- alerts ------------------------------------------------------------------------------------------------------------
st.markdown("#### Recent alerts")
al = pos.recent_alerts(cfg, 40)
if al:
    icons = {"price": "💲 Price", "injury": "🩹 Lineup news", "sim": "🎲 GridSim's number", "settled": "🏁 Final"}
    for i, a in enumerate(al):
        title = str(a["title"]).removeprefix("GridSim watch: ")
        with st.expander(f"{_local(a['ts_utc'])} · {icons.get(a['kind'], a['kind'])} · {title}", expanded=i < 3):
            st.text(a["message"])          # plain text: "$" and "%" in an alert must not be read as formatting
            if a.get("url"):
                st.markdown(f"[open on Kalshi]({a['url']})")
else:
    st.caption("None yet. An alert appears when Kalshi's price for your side moves "
               f"{100 * float(cfg.watch.get('price_jump', 0.06)):.0f}¢ or more, when the expected starting QB changes "
               "or a player who matters changes status, when GridSim re-simulates and its chance moves "
               f"{100 * float(cfg.watch.get('sim_move', 0.05)):.0f} points or more beyond its margin of error, and "
               "when the game is final.")

# ---- closed ------------------------------------------------------------------------------------------------------------
closed = [p for p in allp if p.get("status") in ("sold", "settled")]
if closed:
    st.markdown("#### Closed positions")
    total = sum(float(p.get("pnl") or 0) for p in closed)
    ended = {"won": "won (paid $1 each)", "lost": "lost (paid $0)", "tie": "tie (paid 50¢ each)"}
    common.table([{"game": p["matchup"], "contract": f"{p['label']} ({p['side'].title()})", "contracts": p["contracts"],
                   "paid": _cents(p["price"]),
                   "ended": ("sold at " + _cents(p.get("exit_price"))) if p["status"] == "sold"
                   else ended.get(p.get("result"), p.get("result", "")),
                   "profit / loss": _usd(p.get("pnl")), "closed": _local(p.get("closed_utc"))}
                  for p in sorted(closed, key=lambda q: str(q.get("closed_utc")), reverse=True)],
                 right=("paid", "profit / loss"), height=460)
    st.caption(f"Total across closed positions: **{_usd(total)}** after fees. These are your own entries — GridSim "
               "doesn't know about anything you didn't add here.")
