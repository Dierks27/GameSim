"""Signals: Kalshi opportunity scanner (gridsim.scanner.scan), the current signals / watchlist, and the
signal ledger's live record. READ-ONLY: gridsim alerts, it never places orders."""
from __future__ import annotations

import datetime as dt

import polars as pl
import streamlit as st

import common

common.via_app(common.SIGNALS_PAGE)
import helpers
from gridsim import scanner
from gridsim.markets import kalshi_url

hr = common.hr
ss = st.session_state
s = common.settings()
cfg = common.cfg()

st.html('<div class="gs"><h1>Signals <span class="readonly">READ-ONLY</span></h1><p class="sub">Open Kalshi NFL '
        "markets vs the latest sims, after fees, with liquidity and trust checks. gridsim <b>never places orders</b> "
        "and never uses exchange account keys — it only reads public prices and tells you.</p></div>")

sig_dir = cfg.resolve_path("signals")
ledger_p, graded_p = sig_dir / "ledger.csv", sig_dir / "graded.csv"

# ---- scan ----------------------------------------------------------------------------------------------
c1, c2 = st.columns([2, 3], vertical_alignment="center")
go = c1.button("Scan now", type="primary", width="stretch", icon=":material/radar:")
c2.caption(f"{'Current week' if s['week'] is None else 'Week ' + str(s['week'])} of {s['season']} · re-sims stale games "
           f"at {s['sims']:,} sims (sidebar) · no notifications are sent from here, and positions you hold are "
           "checked on **My positions**, not here. Can take several minutes.")
if go:
    with st.status("Scanning Kalshi markets …", expanded=True) as status:
        st.write("Pricing every upcoming game of the week against its sim (re-simulating games whose sim is "
                 f"older than {cfg.scanner['max_sim_age_hours']} h).")
        bar = st.progress(0.0, text="starting …")
        log = st.empty()
        try:
            with common.capture_output(log, keep_lines=8):
                sigs = scanner.scan(cfg, s["season"], s["week"], notify_on=False, sims=s["sims"], verbose=True,
                                    progress=lambda f, t: bar.progress(min(max(f, 0.0), 1.0), text=t))
            ss["signals_scan"] = {"ts": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "signals": sigs}
            strong = sum(x["confidence"] == "strong" for x in sigs)
            mod = sum(x["confidence"] == "moderate" for x in sigs)
            status.update(label=f"Scan done: {len(sigs)} candidates — {strong} strong, {mod} moderate, "
                                f"{len(sigs) - strong - mod} watchlist", state="complete", expanded=False)
        except Exception as e:
            status.update(label="Scan failed", state="error", expanded=True)
            st.error(f"Scan failed — {type(e).__name__}: {e}")


def _matchup_from_id(gid: str) -> str:
    parts = str(gid or "").split("_")
    return f"{parts[2]} @ {parts[3]}" if len(parts) >= 4 else str(gid or "")


def _link(x: dict) -> str | None:
    """The game's own Kalshi page (rebuilt from the contract code, so older rows with series-only links work too)."""
    u = x.get("url") or ""
    if u.count("/") >= 6:                       # already https://kalshi.com/markets/<series>/<slug>/<event>
        return u
    return kalshi_url(None, x.get("ticker")) or (u or None)


def _rows(sigs: list[dict]) -> list[dict]:
    out = []
    for x in sigs:
        out.append({"level": x.get("confidence"), "game": x.get("matchup") or _matchup_from_id(x.get("game_id")),
                    "contract": x.get("label"), "side": x.get("side"),
                    "edge": round(100 * float(x["edge"]), 1) if x.get("edge") not in (None, "") else None,
                    "price": round(100 * float(x["price"])) if x.get("price") not in (None, "") else None,
                    "depth": round(float(x["depth_dollars"])) if x.get("depth_dollars") not in (None, "") else None,
                    "why": x.get("reason"), "Kalshi": _link(x)})
    return out


ALERT = dict(fmt={"edge": "%+.1f", "price": "%.0f¢", "depth": "$%.0f"}, link={"Kalshi": "open ↗"}, sign=("edge",),
             nowrap=("level", "game"),
             hide={"depth": 2, "side": 3},
             labels={"edge": "lean"},
             help={"edge": "GridSim's own chance minus the cost, in points — the same number as the lean column in the "
                           "table above. The price check goes by GridSim alone.",
                   "depth": "Dollars offered at that price.", "why": "The price check's own notes."})

scan_res = ss.get("signals_scan")
source = None
if scan_res:
    current, source = scan_res["signals"], f"scan at {scan_res['ts']} (this session)"
elif ledger_p.exists():
    try:
        led = pl.read_csv(ledger_p, infer_schema_length=None)
        last = led["ts_utc"].max()
        current = list(led.filter(pl.col("ts_utc") == last).iter_rows(named=True))
        source = f"last scan in the ledger, {str(last)[:16].replace('T', ' ')} UTC"
    except Exception as e:
        current = []
        st.warning(f"Could not read the ledger ({e}).")
else:
    current = []

# ---- find edges: every game of the week, filtered your way ----------------------------------------------
st.markdown("#### Edges and leans this week")
wb = common.week_board(s["season"], s["week"])
all_rows = [r for g_ in wb["games"].values() for r in (g_.get("rows") or [])]
if not all_rows:
    st.info(common.board_empty_text(wb, "**Scan now** above"))
else:
    try:
        when = dt.datetime.fromisoformat(wb["ts_utc"]).astimezone().strftime("%a %b %d, %I:%M %p").replace(" 0", " ")
    except (TypeError, ValueError):
        when = "the last price check"
    st.caption(f"Kalshi's prices as of {when}, against the sims the scheduled price check uses. Every side of every "
               "contract is listed, not only the best one, so cheap long-shot sides are here too.")
    f1, f2, f3, f4 = st.columns([4, 2, 2, 2], vertical_alignment="bottom")
    with f1:
        band = common.price_filter()
    matchups = ["All games"] + sorted({r["matchup"] for r in all_rows})
    gkey = common.keep("sig_game", "All games")
    if ss[gkey] not in matchups:
        ss[gkey] = "All games"
    game_f = f2.selectbox("Game", matchups, key=gkey)
    kinds = {"all": "All types", **common.MARKET_LABEL}
    kind = f3.selectbox("Type", list(kinds), format_func=kinds.get, key=common.keep("sig_kind", "all"))
    rate = {"lean": "Edges and GridSim's leans (🟢 🟡 🟠 ⚪)", "edge": "Edges after adjusting only (🟢 🟡 🟠)",
            "signal": "Passes every rule (🟢)", "all": "Everything"}
    rkey = common.keep("sig_rate", "lean")
    if ss[rkey] not in rate:
        ss[rkey] = "lean"
    rating_f = f4.selectbox("Show", list(rate), format_func=rate.get, key=rkey)
    want = {"lean": common.EDGE_RATINGS + ("lean",), "edge": common.EDGE_RATINGS, "signal": ("signal",),
            "all": tuple(common.RATING_LABEL)}[rating_f]
    shown = [r for r in all_rows if common.in_band(r, band) and (game_f == "All games" or r["matchup"] == game_f)
             and (kind == "all" or r["market"] == kind) and r["rating"] in want]
    # cleanest first; leans whose price you can really get before the ones with a catch
    shown.sort(key=lambda r: (scanner.RATING_RANK[r["rating"]], bool(r.get("hard")), -r["edge"] if r["rating"] != "lean" else 0,
                              -r["lean"]))
    st.caption(f"{len(shown)} of {len(all_rows)} sides match.")
    if shown:
        common.board(shown, game=True)
    common.blend_note()
    if band == "under50":
        common.longshot_note(s["season"])
    missing = [f"{g_['matchup']} ({g_['note']})" for g_ in wb["games"].values() if g_.get("rows") is None]
    if missing:
        st.caption("Not listed: " + "; ".join(missing) + ".")

with st.expander("The alert list: what the last price check flagged", expanded=False):
    st.caption("The price check's own list (the best side of each contract only). It goes by **GridSim's own number "
               "alone** — the **lean** of the table above, not the adjusted edge — so where a note says “edge” or "
               "“EV” it means that lean. A contract that passes every rule here is what reaches your phone; in the "
               "table above the same contract can still be a ⚪ Lean, because 🟢 there needs an edge after adjusting. "
               "The price check also tests the lean with a questionable QB out and in, and re-simulates borderline "
               "games with more sims.")
    if source:
        st.caption(f"From the {source}.")
    if not current:
        st.info("No scan results yet — press **Scan now**.")
    else:
        live = [x for x in current if x.get("confidence") in ("strong", "moderate")]
        watch = [x for x in current if x.get("confidence") not in ("strong", "moderate")]
        if live:
            common.table(_rows(live), **ALERT)
        else:
            st.caption("Nothing passes every rule right now (lean, margin of error, depth, spread, volume, freshness, record).")
        st.markdown(f"##### Watchlist ({len(watch)})")
        st.caption(f"GridSim's lean is at least {100 * float(cfg.scanner['min_edge']):.0f} points but at least one rule "
                   "fails (shown under “why”) — including market types "
                   f"without a positive record yet (≥ {cfg.scanner['trust_min_graded']} graded games with positive CLV).")
        if watch:
            common.table(_rows(watch), height=460, **ALERT)

# ---- record --------------------------------------------------------------------------------------------
REC_HELP = {"avg CLV": "Closing-line value: how far Kalshi's price moved toward GridSim's side by kickoff, in dollars "
                       "per contract. Positive = the market came GridSim's way.",
            "P&L (units)": "Profit or loss if 1 unit had been put on every one of them, after fees.",
            "P&L (units, per game)": "Profit or loss with 1 unit per game (a game's contracts averaged), after fees.",
            "won %": "How many of them won.", "needed %": "How many had to win to break even (the average cost).",
            "graded": "Contracts with a result."}
st.markdown("#### Ledger record")
try:
    text = scanner.signals_report(cfg, s["season"])
    trust = scanner.trust_table(cfg)
except Exception as e:
    text, trust = "", {}
    st.error(f"Could not read the signal ledger — {type(e).__name__}: {e}")
rows, trust_line = helpers.parse_signals_report(text)
if rows:
    common.table([{"market": r["market"], "level": r["confidence"], "graded": r["n"], "won %": r["hit_pct"],
                   "avg CLV": r["avg_clv"], "P&L (units)": r["pnl"]} for r in rows],
                 fmt={"won %": "%.0f", "avg CLV": "%+.3f", "P&L (units)": "%+.2f"}, sign=("avg CLV", "P&L (units)"), help=REC_HELP)
else:
    st.caption(text or "no graded signals yet")
try:
    by_day = scanner.signals_by_day(cfg, s["season"])
except Exception as e:
    by_day = []
    st.caption(f"(by-day record unavailable: {e})")
if by_day:
    st.markdown("##### By the day the signal appeared")
    common.table(by_day, fmt={"hit %": "%.0f", "avg CLV": "%+.3f", "P&L (units)": "%+.2f"},
                 labels={"hit %": "won %"}, sign=("avg CLV", "P&L (units)"), help=REC_HELP)
    st.caption("Is it better to act on Tuesday or wait for Friday? Each signal counts once per day it was seen. Avg "
               "CLV = how far Kalshi's price moved toward GridSim's side by kickoff (positive = the market came your "
               "way). Watchlist items are included, so this builds up even before any market type is trusted.")
try:
    by_price = scanner.record_by_price(cfg, s["season"])
except Exception as e:
    by_price = []
    st.caption(f"(by-price record unavailable: {e})")
if by_price:
    st.markdown("##### By the price paid: long shots against favourites")
    common.table(by_price, fmt={"won %": "%.0f", "needed %": "%.0f", "avg CLV": "%+.3f", "P&L (units)": "%+.2f"},
                 sign=("avg CLV", "P&L (units)"), help=REC_HELP)
    st.caption("Did the cheap contracts GridSim leaned to hold up? **Won %** against **needed %** (the average cost: "
               "what had to win to break even). Each contract counts once; all confidence levels and model versions "
               "are counted together, so read it as a rough guide until there are many games.")
if trust:
    st.markdown("##### Trust by market type")
    common.table([{"market": k, "graded games": v["n"], "contracts": v.get("contracts"), "avg CLV": v["avg_clv"],
                   "P&L (units, per game)": v["pnl"],
                   "status": "trusted" if v["trusted"] else "watchlist"} for k, v in trust.items()],
                 fmt={"avg CLV": "%+.3f", "P&L (units, per game)": "%+.2f"}, sign=("avg CLV", "P&L (units, per game)"),
                 help=REC_HELP)
st.caption(f"A market type may alert only after {cfg.scanner['trust_min_graded']} graded games with positive average "
           "closing-line value and non-negative P&L. A game counts once per market type — its many spread or total "
           "lines are nearly the same bet, and a contract seen at every 30-minute price check is still one signal "
           "(so this table's P&L averages a game's contracts and is smaller than the Ledger record's total). "
           "Signals are graded automatically after the games (or with **Grade** on the Weekly routine page).")

if graded_p.exists():
    try:
        g_rows = list(pl.read_csv(graded_p, infer_schema_length=None).iter_rows(named=True))
        pnl_fig, clv_fig = common.signal_history_figures(g_rows)
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("##### Cumulative P&L (flat 1 unit)")
            st.plotly_chart(pnl_fig, theme=None, config=hr.PLOTLY_CONFIG)
        with c2:
            st.markdown("##### Average closing-line value")
            st.plotly_chart(clv_fig, theme=None, config=hr.PLOTLY_CONFIG)
    except Exception as e:
        st.warning(f"Could not chart graded signals ({e}).")
else:
    st.caption("P&L and CLV charts appear once `signals/graded.csv` exists (after the first graded week).")
