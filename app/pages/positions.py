"""My positions: the position watch. You tell GridSim which Kalshi contracts you hold; it watches the price, the
injury report and its own number, and alerts you. READ-ONLY: GridSim never trades and never sees your account.
All logic is in gridsim.positions (the same code the scheduled price checks run)."""
from __future__ import annotations

import datetime as dt

import streamlit as st

import common
from gridsim import api
from gridsim import positions as pos
from gridsim.markets import Kalshi, game_markets, sim_probs

hr = common.hr
ss = st.session_state
s = common.settings()
cfg = common.cfg()

st.html('<div class="gs"><h1>My positions <span class="readonly">READ-ONLY</span></h1><p class="sub">Tell GridSim '
        "what you bought on Kalshi and it keeps watch: a sudden price move, an injury or lineup change, GridSim "
        "changing its own number — you get an alert with the facts. GridSim <b>never places orders</b> and never "
        "sees your Kalshi account; a position here is only a note you enter. What to do is always your call.</p></div>")

if ss.get("pos_msg"):
    kind, msg = ss.pop("pos_msg")
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


@st.cache_data(ttl=120, show_spinner="Reading Kalshi prices …")
def _contracts(game_id: str, home: str, away: str, gameday: str) -> dict:
    return game_markets(Kalshi(common.cfg()), {"home_team": home, "away_team": away, "gameday": gameday})


# ---- add ---------------------------------------------------------------------------------------------------------------
with st.expander("Add a position", expanded=not pos.open_positions(cfg), icon=":material/add_circle:"):
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
        try:
            mk = _contracts(g["game_id"], g["home_team"], g["away_team"], g.get("gameday"))
        except Exception as e:
            mk = None
            st.error(f"Could not read Kalshi's prices for this game ({type(e).__name__}: {e}). Try again in a minute.")
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
                ci = st.selectbox("Contract", range(len(opts)), key="pos_contract", format_func=lambda i: opts[i]["label"])
                c = opts[min(ci, len(opts) - 1)]
                side = st.radio("You bought", ["Yes", "No"], horizontal=True, key="pos_side",
                                help="Yes pays $1 if the contract's statement comes true; No pays $1 if it doesn't.")
                ask = c.get("yes_ask") if side == "Yes" else c.get("no_ask")
                what = c["label"] if side == "Yes" else f"NOT “{c['label']}”"
                st.caption(f"You hold: **{what}** — pays $1 per contract if that happens. Kalshi's price to buy it "
                           f"right now: **{_cents(ask)}**.")
                a, b = st.columns(2)
                price_c = a.number_input("Price you paid per contract (¢)", 1, 99,
                                         int(round(100 * ask)) if ask and 0 < ask < 1 else 50, key="pos_price")
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
            ss["pos_msg"] = ("ok", "Checked. " + (f"{len(alerts)} new alert(s) — see below." if alerts
                                                  else "Nothing new since the last check."))
        except Exception as e:
            ss["pos_msg"] = ("err", f"Check failed — {type(e).__name__}: {e}")
    st.rerun()
c2.caption(f"Last check: {_local(last.get('checked_utc'))}. With **Schedule GridSim** set up, positions are checked "
           "every 30 minutes, and every 10 minutes in the last "
           f"{cfg.watch.get('near_kickoff_hours', 3)} hours before kickoff, with alerts to your phone.")
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
            ("Kickoff", _local(p.get("kickoff_utc")), f"checked {_local(v.get('ts'))}"),
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
    icons = {"price": "💲 price", "injury": "🩹 lineup news", "sim": "🎲 GridSim's number", "settled": "🏁 final"}
    st.dataframe([{"when": _local(a["ts_utc"]), "what": icons.get(a["kind"], a["kind"]), "alert": a["title"],
                   "details": a["message"]} for a in al], hide_index=True, width="stretch",
                 column_config={"details": st.column_config.TextColumn(width="large")})
else:
    st.caption("None yet. An alert appears when Kalshi's price for your side moves "
               f"{100 * float(cfg.watch.get('price_jump', 0.06)):.0f}¢ or more, when a listed player's status or the "
               f"expected starting QB changes, when GridSim's chance moves {100 * float(cfg.watch.get('sim_move', 0.05)):.0f} "
               "points or more, and when the game is final.")

# ---- closed ------------------------------------------------------------------------------------------------------------
closed = [p for p in allp if p.get("status") in ("sold", "settled")]
if closed:
    st.markdown("#### Closed positions")
    total = sum(float(p.get("pnl") or 0) for p in closed)
    st.dataframe([{"game": p["matchup"], "contract": f"{p['label']} ({p['side'].title()})", "contracts": p["contracts"],
                   "paid": _cents(p["price"]),
                   "ended": ("sold at " + _cents(p.get("exit_price"))) if p["status"] == "sold" else p.get("result", ""),
                   "profit / loss": p.get("pnl"), "closed": _local(p.get("closed_utc"))}
                  for p in sorted(closed, key=lambda q: str(q.get("closed_utc")), reverse=True)],
                 hide_index=True, width="stretch",
                 column_config={"profit / loss": st.column_config.NumberColumn(format="$%+.2f")})
    st.caption(f"Total across closed positions: **{_usd(total)}** after fees. These are your own entries — GridSim "
               "doesn't know about anything you didn't add here.")
