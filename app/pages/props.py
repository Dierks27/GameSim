"""Player props: the per-sim distribution of any player stat, with a movable line. Over / under is counted
exactly from the stored per-sim histogram (api.cover_from_hist) — moving the line never re-simulates."""
from __future__ import annotations

import streamlit as st

import common

common.via_app(common.PROPS_PAGE)
import helpers
from gridsim.api import cover_from_hist
from gridsim.report import american, moe

hr = common.hr
ss = st.session_state
s = common.settings()

PROP_STATS = {
    "pass_yds": "Passing yards", "pass_att": "Pass attempts", "comp": "Completions", "pass_td": "Passing TDs",
    "ints": "Interceptions", "rush_yds": "Rushing yards", "rush_att": "Rush attempts", "rush_td": "Rushing TDs",
    "rec": "Receptions", "rec_yds": "Receiving yards", "targets": "Targets", "rec_td": "Receiving TDs",
    "rush_rec_yds": "Rush + rec yards", "anytime_td": "Anytime TD",
}
DEFAULT_STAT = {"QB": "pass_yds", "RB": "rush_yds", "FB": "rush_yds", "WR": "rec_yds", "TE": "rec_yds"}

common.page_header("Player props", "Per-sim distribution of any player stat. Move the line: over / under is counted "
                                   "from the stored simulations, no re-sim.")
g = common.pick_game("props")
H, A = g["home"], g["away"]
req = common.make_request(H, A, g["season"], g["week"], {**s, "players": True}, neutral=g["neutral"], playoff=g["playoff"])
summ = common.lookup(req)

if summ is None:
    st.info(f"No result yet for {A} @ {H} with player projections at {s['sims']:,} sims, seed {s['seed']}.")
    if st.button("Simulate with players", type="primary"):
        if common.run(req) is not None:
            st.rerun()
    st.stop()

players = summ.get("players") or {}
if not players:
    st.warning("This result has no player projections.")
    if st.button("Re-simulate with players", type="primary"):
        if common.run(req, force=True) is not None:
            st.rerun()
    st.stop()

n = summ["n_sims"]
has_hist = any("hist" in p for plist in players.values() for p in plist)
c1, c2 = st.columns([3, 2], vertical_alignment="center")
with c1:
    st.caption(f"{A} @ {H} · {n:,} sims · seed {summ['meta'].get('seed')} · config {summ['meta'].get('config_hash')}")
with c2:
    if st.button("Re-simulate (ignore cache)", width="stretch",
                 help="Fresh run with current data; needed for exact numbers on results cached before per-sim histograms."):
        if common.run(req, force=True) is not None:
            st.rerun()
if not has_hist:
    st.warning("This cached result predates per-player histograms, so over / under below is **approximate "
               "(percentile interpolation)**. Re-simulate for exact numbers.")

# ---- pick team / player / stat -----------------------------------------------------------------------
teams = list(players)
tkey = common.keep("props_team", teams[0])
if ss[tkey] not in teams:
    ss[tkey] = teams[0]
c1, c2, c3 = st.columns([1, 2, 2])
team = c1.selectbox("Team", teams, key=tkey)
plist = players[team]
names = [p["name"] for p in plist]
pkey = common.keep("props_player", names[0])
if ss[pkey] not in names:
    ss[pkey] = names[0]
by_name = {p["name"]: p for p in plist}
name = c2.selectbox("Player", names, key=pkey, format_func=lambda nm: f"{nm} · {by_name[nm].get('pos') or '?'}")
p = by_name[name]
st_ = p.get("stats", {})
hist_all = p.get("hist") or {}
if has_hist:
    avail = [k for k in PROP_STATS if k in hist_all] + ["anytime_td"]
else:
    avail = [k for k in PROP_STATS if k in st_ and st_[k].get("mean", 0) > 0.05] + ["anytime_td"]
avail = list(dict.fromkeys(avail))
skey = common.keep("props_stat", DEFAULT_STAT.get(p.get("pos"), avail[0]))
if ss[skey] not in avail:
    ss[skey] = DEFAULT_STAT.get(p.get("pos")) if DEFAULT_STAT.get(p.get("pos")) in avail else avail[0]
stat = c3.selectbox("Stat", avail, key=skey, format_func=lambda k: PROP_STATS[k])
label = PROP_STATS[stat]

# ---- anytime TD: a single probability ------------------------------------------------------------------
if stat == "anytime_td":
    pt = float(p.get("anytime_td") or 0.0)
    m = moe(pt, n)
    st.html(common.kv_html([("Anytime TD", common.pm_value(pt, m), common.close_note(pt, m, f"fair {american(pt)}")),
                            ("No TD", hr.esc(hr.pct(1 - pt)), f"fair {american(1 - pt)}")]))
    st.caption("Share of sims in which the player scored a rushing or receiving touchdown.")
else:
    hist = hist_all.get(stat)
    if hist is None and has_hist:                      # the stat never happened in any sim: all zeros
        hist = {"values": [0], "counts": [n]}
    pct_stats = st_.get(stat)
    if hist is None and pct_stats is None:
        st.info(f"{label} needs per-sim histograms — re-simulate this game to get them.")
        st.stop()
    med = (pct_stats or {}).get("p50")
    if med is None and hist:
        cum, half = 0, sum(hist["counts"]) / 2
        for v, c in zip(hist["values"], hist["counts"]):
            cum += c
            if cum >= half:
                med = v
                break
    hi_val = max(hist["values"]) if hist else float(pct_stats["p90"]) * 1.6 + 1
    hi = float(max(1.5, hi_val + 0.5))
    k = f"{common.req_key(req)}_{p.get('id') or name}_{stat}"
    canon = common.keep(f"propline_{k}", min(hi, helpers.default_prop_line(med or 0)))
    ss[canon] = float(min(max(0.0, float(ss[canon])), hi))
    ss["props_sl"], ss["props_ni"] = ss[canon], ss[canon]

    def _from(src: str) -> None:
        ss[canon] = float(ss[src])

    c1, c2 = st.columns([4, 1], vertical_alignment="bottom")
    c1.slider(f"Line — {name} {label}", 0.0, hi, step=0.5, format="%.1f", key="props_sl", on_change=_from, args=("props_sl",))
    c2.number_input("Line", 0.0, hi, step=0.5, format="%.1f", key="props_ni", on_change=_from, args=("props_ni",))
    line = float(ss[canon])

    if hist is not None:
        r = cover_from_hist(hist, line, "total")       # value > line = over, == line = push
        over, under, push = r["hit"], r["miss"], r["push"]
        m_over, m_under = r["moe"], moe(under, n)
        tiles = [(f"Over {line:g}", common.pm_value(over, m_over), common.close_note(over, m_over, f"fair {american(over)}")),
                 (f"Under {line:g}", common.pm_value(under, m_under),
                  common.close_note(under, m_under, f"fair {american(under)}"))]
        if push > 0:
            tiles.append(("Push", hr.esc(hr.pct(push)), ""))
        if pct_stats:
            tiles.append(("Median [p10–p90]", f"{pct_stats['p50']:.0f} <small class='muted'>[{pct_stats['p10']:.0f}–"
                                              f"{pct_stats['p90']:.0f}]</small>", f"mean {pct_stats['mean']:.1f}"))
        st.html(common.kv_html(tiles))
        st.plotly_chart(common.stat_figure(hist, line, label), theme=None, config=hr.PLOTLY_CONFIG, key=f"pfig_{k}")
        st.caption(f"Exact: counted from the {n:,} simulated games (no re-sim). ± = 95% simulation margin of error; "
                   f"within it of 50% is flagged “{hr.CLOSE_TEXT}”.")
        # quick ladder of nearby lines
        iqr = float((pct_stats or {}).get("p75", 2) - (pct_stats or {}).get("p25", 0))
        step = max(1.0, round(iqr / 4))
        center = helpers.default_prop_line(med or 0)
        rows = []
        for L in [center + (i - 3) * step for i in range(7)]:
            if L < 0:
                continue
            rr = cover_from_hist(hist, L, "total")
            rows.append([f"{L:g}", common.pm_span(rr["hit"], rr["moe"]), hr.pct(rr["miss"]), american(rr["hit"])])
        with st.expander("Nearby lines"):
            st.html('<div class="gs">' + hr.table_html(["Line", "Over", "Under", "Fair over"], rows,
                                                         hl={i for i, r_ in enumerate(rows) if r_[0] == f"{line:g}"}) + "</div>")
    else:
        over = helpers.approx_over_from_percentiles(pct_stats, line)
        st.html(common.kv_html([(f"Over {line:g}", f"≈ {hr.esc(hr.pct(over))}", "approximate (percentile interpolation)"),
                                (f"Under {line:g}", f"≈ {hr.esc(hr.pct(1 - over))}", "approximate (percentile interpolation)"),
                                ("Median [p10–p90]", f"{pct_stats['p50']:.0f} <small class='muted'>[{pct_stats['p10']:.0f}–"
                                                     f"{pct_stats['p90']:.0f}]</small>", f"mean {pct_stats['mean']:.1f}")]))
        st.plotly_chart(common.percentile_figure(pct_stats, line, label), theme=None, config=hr.PLOTLY_CONFIG)
        st.caption("Approximate (percentile interpolation): this result stores only p10/p25/p50/p75/p90 for the "
                   "player, so the over / under is read off a straight-line CDF through them. Re-simulate for exact.")

# ---- market prices -------------------------------------------------------------------------------------
st.markdown("##### Kalshi prices for this player")
krows = helpers.prop_market_rows((summ.get("kalshi") or {}).get("rows", []), name)
if krows:
    rows = [[hr.esc(r["label"]), common.pm_span(r["p_yes"], r["moe"]),
             f"{100 * (r.get('yes_bid') or 0):.0f}/{100 * (r.get('yes_ask') or 0):.0f}¢",
             f"{r.get('volume') or 0:,.0f}",
             (f"{hr.esc(r['side'])} {100 * r['edge']:+.1f}" if r.get("edge") is not None else "–")] for r in krows]
    st.html('<div class="gs">' + hr.table_html(["Market", "Sim YES", "Bid/ask", "Volume", "Best side / edge"], rows) + "</div>")
else:
    st.caption(f"No Kalshi markets mentioning {name} in this result (only game-level markets are fetched so far). "
               "Read-only: gridsim never places orders.")

if summ.get("props"):
    with st.expander("Props requested on the command line"):
        st.html('<div class="gs">' + hr.players_html({"props": summ["props"]}) + "</div>")
