"""Scorecard: the honest live record (frozen pre-kickoff predictions graded after the games) next to the
saved backtests and the tuning history."""
from __future__ import annotations

import json

import polars as pl
import streamlit as st

import common
from gridsim import live
from gridsim.metrics import calibration_table

hr = common.hr
s = common.settings()
cfg = common.cfg()

common.page_header("Scorecard", "Live predictions are frozen before kickoff and graded after the game — the record "
                                "you can trust. Backtests and tuning runs below for comparison.")

METRICS = [("games", "Games", "%d"), ("brier", "Brier", "%.4f"), ("market_brier", "Brier (market)", "%.4f"),
           ("log_loss", "Log loss", "%.4f"), ("mae_margin", "MAE margin", "%.2f"),
           ("market_mae_margin", "MAE margin (market)", "%.2f"), ("mae_total", "MAE total", "%.2f"),
           ("crps_margin", "CRPS margin", "%.2f"), ("crps_total", "CRPS total", "%.2f")]
FMT = {label: st.column_config.NumberColumn(label, format=f) for _, label, f in METRICS}


def _row(first: dict, b: dict) -> dict:
    return {**first, **{label: b.get(k) for k, label, _ in METRICS}}


c1, _ = st.columns([2, 3])
all_seasons = c1.toggle("All seasons", key=common.keep("sc_all", False), help=f"Off = {s['season']} only")
try:
    sc = live.scorecard(cfg, None if all_seasons else s["season"])
except Exception as e:
    sc = {"games": 0}
    st.error(f"Could not read the scorecard — {type(e).__name__}: {e}")

st.markdown("#### Live record")
ov = sc.get("overall") or {}
if not ov.get("games"):
    st.info("No graded predictions yet. On the **Weekly routine** page press **Lock in predictions** before kickoff, "
            "then **Grade** after the games (Tuesday).")
else:
    def _cmp(k, mk, fmt):
        v, m = ov.get(k), ov.get(mk)
        note = f"market {fmt % m}" if m is not None else ""
        return fmt % v, note
    b_v, b_n = _cmp("brier", "market_brier", "%.4f")
    m_v, m_n = _cmp("mae_margin", "market_mae_margin", "%.2f")
    st.html(common.kv_html([("Games graded", f"{ov['games']}", ""), ("Brier (win %)", b_v, b_n + " · lower is better"),
                            ("Log loss", f"{ov['log_loss']:.4f}", ""), ("MAE margin", m_v, m_n),
                            ("MAE total", f"{ov['mae_total']:.2f}", ""),
                            ("CRPS margin / total", f"{ov['crps_margin']:.2f} / {ov['crps_total']:.2f}", "")]))
    st.markdown("##### By config version")
    st.dataframe([_row({"config": h}, b) for h, b in sc.get("by_config", {}).items()], hide_index=True, width="stretch",
                 column_config=FMT)
    st.markdown("##### By week")
    st.dataframe([_row({"week": w}, b) for w, b in sc.get("by_week", {}).items()], hide_index=True, width="stretch",
                 column_config=FMT)

card = cfg.resolve_path("predictions") / "scorecard.csv"
if card.exists():
    try:
        df = pl.read_csv(card, infer_schema_length=None)
        if not all_seasons:
            df = df.filter(pl.col("season") == s["season"])
        if df.height:
            cal = calibration_table((df["p_home"] + 0.5 * df["p_tie"]).to_numpy(), df["home_win"].to_numpy())
            st.markdown("##### Calibration (live)")
            st.plotly_chart(common.calibration_figure(cal), theme=None, config=hr.PLOTLY_CONFIG)
            st.caption("Predicted home win % in deciles vs how often the home team actually won (ties count ½). "
                       "Dots on the diagonal = calibrated; bigger dots = more games.")
    except Exception as e:
        st.warning(f"Could not chart calibration ({e}).")

# ---- backtests -----------------------------------------------------------------------------------------
st.markdown("#### Saved backtests")
bt_rows = []
for p in sorted(cfg.resolve_path(cfg.backtest["out_dir"]).glob("*/metrics.json"), key=lambda q: q.stat().st_mtime, reverse=True):
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        continue
    mk = m.get("market") or {}
    bt_rows.append({"label": m.get("label"), "config": m.get("config_hash"), "saved": common.mtime_text(p),
                    "games": m.get("games"), "sims/game": m.get("sims_per_game"),
                    "Brier": m.get("brier"), "Brier (mkt)": mk.get("brier"),
                    "Log loss": m.get("log_loss"), "Log loss (mkt)": mk.get("log_loss"),
                    "MAE margin": m.get("mae_margin"), "MAE margin (mkt)": mk.get("mae_margin"),
                    "MAE total": m.get("mae_total"), "MAE total (mkt)": mk.get("mae_total"),
                    "CRPS margin": m.get("crps_margin"), "CRPS total": m.get("crps_total")})
if bt_rows:
    f4 = st.column_config.NumberColumn(format="%.4f")
    f2 = st.column_config.NumberColumn(format="%.2f")
    st.dataframe(bt_rows, hide_index=True, width="stretch",
                 column_config={"Brier": f4, "Brier (mkt)": f4, "Log loss": f4, "Log loss (mkt)": f4, "MAE margin": f2,
                                "MAE margin (mkt)": f2, "MAE total": f2, "MAE total (mkt)": f2, "CRPS margin": f2,
                                "CRPS total": f2})
    st.caption("Walk-forward backtests (no lookahead). Compare their Brier / MAE with the live record above: a live "
               "record much worse than the backtest points to leakage or stale inputs.")
else:
    st.caption("No saved backtests yet (the Weekly routine check-up and tuning create them).")

# ---- tuning --------------------------------------------------------------------------------------------
st.markdown("#### Tuning")
tdir = cfg.resolve_path("tuning")
hist = tdir / "history.csv"
if hist.exists():
    try:
        st.markdown("##### Promoted configs (`tuning/history.csv`)")
        st.dataframe(pl.read_csv(hist, infer_schema_length=None), hide_index=True, width="stretch")
    except Exception as e:
        st.warning(f"Could not read tuning history ({e}).")
runs = []
for p in sorted(tdir.glob("*/summary.json"), reverse=True) if tdir.exists() else []:
    try:
        t = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        continue
    runs.append({"run": p.parent.name, "train": ", ".join(map(str, t.get("train_seasons") or [])),
                 "validate": t.get("validate_season"), "trials": t.get("trials"),
                 "train gain": (t.get("train") or {}).get("gain"), "validation gain": (t.get("validation") or {}).get("gain"),
                 "recommended": t.get("recommended"), "likely overfit": t.get("likely_overfit"),
                 "tuned config": t.get("config_hash_tuned")})
if runs:
    st.markdown("##### Tuning runs (`tuning/*/summary.json`)")
    st.dataframe(runs, hide_index=True, width="stretch",
                 column_config={"train gain": st.column_config.NumberColumn(format="%+.5f"),
                                "validation gain": st.column_config.NumberColumn(format="%+.5f")})
if not hist.exists() and not runs:
    st.caption("No tuning runs yet (Weekly routine → step 5).")
