"""Signals: Kalshi opportunity scanner (gridsim.scanner.scan), the current signals / watchlist, and the
signal ledger's live record. READ-ONLY: gridsim alerts, it never places orders."""
from __future__ import annotations

import datetime as dt

import polars as pl
import streamlit as st

import common
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
           f"at {s['sims']:,} sims (sidebar) · no notifications are sent from here. Can take several minutes.")
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
        out.append({"confidence": x.get("confidence"), "matchup": x.get("matchup") or _matchup_from_id(x.get("game_id")),
                    "label": x.get("label"), "side": x.get("side"),
                    "edge (pts)": round(100 * float(x["edge"]), 1) if x.get("edge") not in (None, "") else None,
                    "price (¢)": round(100 * float(x["price"])) if x.get("price") not in (None, "") else None,
                    "depth ($)": round(float(x["depth_dollars"])) if x.get("depth_dollars") not in (None, "") else None,
                    "reason": x.get("reason"), "url": _link(x)})
    return out


COLS = {"url": st.column_config.LinkColumn("Kalshi", display_text="open"),
        "edge (pts)": st.column_config.NumberColumn(format="%+.1f"),
        "reason": st.column_config.TextColumn(width="large")}

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

st.markdown("#### Current signals")
if source:
    st.caption(f"From the {source}.")
if not current:
    st.info("No scan results yet — press **Scan now**.")
else:
    live = [x for x in current if x.get("confidence") in ("strong", "moderate")]
    watch = [x for x in current if x.get("confidence") not in ("strong", "moderate")]
    if live:
        st.dataframe(_rows(live), hide_index=True, width="stretch", column_config=COLS)
    else:
        st.caption("Nothing passes every rule right now (edge, MOE, depth, spread, volume, freshness, trust).")
    st.markdown(f"##### Watchlist ({len(watch)})")
    st.caption("Clears the edge threshold but fails at least one rule (shown in the reason) — including market types "
               f"without a positive record yet (≥ {cfg.scanner['trust_min_graded']} graded signals with positive CLV).")
    if watch:
        st.dataframe(_rows(watch), hide_index=True, width="stretch", column_config=COLS, height=min(420, 38 + 35 * len(watch)))

# ---- record --------------------------------------------------------------------------------------------
st.markdown("#### Ledger record")
try:
    text = scanner.signals_report(cfg, s["season"])
    trust = scanner.trust_table(cfg)
except Exception as e:
    text, trust = "", {}
    st.error(f"Could not read the signal ledger — {type(e).__name__}: {e}")
rows, trust_line = helpers.parse_signals_report(text)
if rows:
    st.dataframe([{"market": r["market"], "confidence": r["confidence"], "graded": r["n"], "hit %": r["hit_pct"],
                   "avg CLV": r["avg_clv"], "P&L (units)": r["pnl"]} for r in rows], hide_index=True, width="stretch",
                 column_config={"avg CLV": st.column_config.NumberColumn(format="%+.3f"),
                                "P&L (units)": st.column_config.NumberColumn(format="%+.2f")})
else:
    st.caption(text or "no graded signals yet")
if trust:
    st.markdown("##### Trust by market type")
    st.dataframe([{"market": k, "graded": v["n"], "avg CLV": v["avg_clv"], "P&L (units)": v["pnl"],
                   "status": "trusted" if v["trusted"] else "watchlist"} for k, v in trust.items()],
                 hide_index=True, width="stretch",
                 column_config={"avg CLV": st.column_config.NumberColumn(format="%+.3f"),
                                "P&L (units)": st.column_config.NumberColumn(format="%+.2f")})
st.caption(f"A market type may alert only after {cfg.scanner['trust_min_graded']} graded signals with positive average "
           "closing-line value and non-negative P&L. Signals are graded when you press **Grade** on the Weekly routine page after the games.")

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
