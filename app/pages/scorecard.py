"""Scorecard: the honest live record (frozen pre-kickoff predictions graded after the games) next to the
saved backtests and the tuning history."""
from __future__ import annotations

import json

import polars as pl
import streamlit as st

import common

common.via_app(common.SCORECARD_PAGE)
from gridsim import live, userdata
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
FMT = {label: f for _, label, f in METRICS}
SHORT = {"Brier (market)": "Brier (mkt)", "MAE margin (market)": "MAE margin (mkt)"}
SC_HELP = {"Brier": "Error of the win chances: 0 is perfect, 0.25 is a coin flip. Lower is better.",
           "Brier (market)": "The same for the closing betting line.", "Brier (mkt)": "The same for the closing betting line.",
           "Log loss": "Another error measure for win chances; punishes confident misses harder. Lower is better.",
           "MAE margin": "Average miss of the predicted margin, in points.", "MAE total": "Average miss of the predicted total, in points.",
           "MAE margin (market)": "Average miss of the closing spread, in points.",
           "CRPS margin": "Error of the whole predicted margin range (not just its middle). Lower is better.",
           "CRPS total": "Error of the whole predicted total range. Lower is better."}


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
    st.caption("Each code is one version of GridSim's model and settings, oldest first. A new code appears after an "
               "update that changes the model, after you use tuned settings, or after you change a setting (other than "
               "backups, PC resources, the position watch or how the app shows things). "
               "Versions are scored separately here; the other tables on this page count all of them together.")
    common.table([_row({"config": h}, b) for h, b in sc.get("by_config", {}).items()], fmt=FMT, labels=SHORT, help=SC_HELP)
    st.markdown("##### By week")
    common.table([_row({"week": w}, b) for w, b in sc.get("by_week", {}).items()], fmt=FMT, labels=SHORT, help=SC_HELP)

df = live.load_card(cfg)
if df is not None:
    try:
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

rdf = live.load_card(cfg, "scorecard_reconstructed.csv")
if rdf is not None:
    try:
        if not all_seasons:
            rdf = rdf.filter(pl.col("season") == s["season"])
        if rdf.height:
            st.markdown("##### Reconstructed predictions (not counted)")
            st.caption("Re-created after a data loss from data before each week (Settings → Your data). Shown for "
                       "reference only: they are **not** in the record above, because they were not made before "
                       "kickoff.")
            common.table(rdf.select(["season", "week", "home", "away", "home_score", "away_score", "p_home",
                                     "pred_margin", "pred_total"]),
                         labels={"home_score": "home pts", "away_score": "away pts", "p_home": "home win",
                                 "pred_margin": "margin (pred.)", "pred_total": "total (pred.)"},
                         fmt={"p_home": lambda v: f"{100 * v:.0f}%", "pred_margin": "%+.1f", "pred_total": "%.1f"}, height=420)
    except Exception as e:
        st.warning(f"Could not read reconstructed predictions ({e}).")

# ---- by lock-in day ------------------------------------------------------------------------------------
by_day = live.scorecard_by_lockin(cfg, None if all_seasons else s["season"])
st.markdown("#### By lock-in day")
if by_day:
    rows_d = []
    for b in by_day:
        fin = b.get("final_same_games") or {}
        rows_d.append({"locked in on": b["day"], "games": b["games"], "hours before": b.get("hours_before"),
                       "Brier": b.get("brier"), "Brier (final)": fin.get("brier"),
                       "MAE margin": b.get("mae_margin"), "MAE margin (final)": fin.get("mae_margin"),
                       "MAE total": b.get("mae_total"), "MAE total (final)": fin.get("mae_total"),
                       "Brier (line)": b.get("market_brier")})
    f4, f2 = "%.4f", "%.2f"
    common.table(rows_d, fmt={"Brier": f4, "Brier (final)": f4, "Brier (line)": f4, "MAE margin": f2,
                              "MAE margin (final)": f2, "MAE total": f2, "MAE total (final)": f2, "hours before": "%.0f"},
                 help={**SC_HELP, "hours before": "How long before kickoff these were locked in (the middle one).",
                       "Brier (final)": "Your LAST lock-in before kickoff, on the same games.",
                       "MAE margin (final)": "Your last lock-in before kickoff, on the same games.",
                       "MAE total (final)": "Your last lock-in before kickoff, on the same games.",
                       "Brier (line)": "The closing betting line, on the same games."})
    st.caption("Every lock-in you made before kickoff, graded and grouped by the day you made it. Lower is better. "
               "Each day is compared with your final lock-in on the very same games, so you can see how much the "
               "extra days of news (injury reports, lineups) improve the numbers — or whether Tuesday's are just as "
               "good. With only a few weeks of games these differences are mostly noise.")
else:
    st.caption("Appears after the first Grade of a week you locked in (Weekly routine → Grade). Lock in on more than "
               "one day (e.g. Tuesday and Friday) to compare them.")

# ---- backtests -----------------------------------------------------------------------------------------
st.markdown("#### Saved backtests")
bt_rows = []
_bt = [(p, shipped) for d, shipped in userdata.run_dirs(cfg, cfg.backtest["out_dir"]) for p in d.glob("*/metrics.json")]
for p, shipped in sorted(_bt, key=lambda q: (q[1], -q[0].stat().st_mtime)):
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        continue
    mk = m.get("market") or {}
    def pair(a, b, f="%.3f"):          # GridSim's number and the closing line's, side by side in one cell
        return "—" if a is None else (f % a) + ("" if b is None else " · " + (f % b))
    bt_rows.append({"label": m.get("label") + (" (example)" if shipped else ""),
                    "config": m.get("config_hash"), "saved": common.mtime_text(p),
                    "games": m.get("games"), "sims": m.get("sims_per_game"),
                    "Brier": pair(m.get("brier"), mk.get("brier")), "Log loss": pair(m.get("log_loss"), mk.get("log_loss")),
                    "MAE margin": pair(m.get("mae_margin"), mk.get("mae_margin"), "%.2f"),
                    "MAE total": pair(m.get("mae_total"), mk.get("mae_total"), "%.2f"),
                    "CRPS margin": m.get("crps_margin"), "CRPS total": m.get("crps_total")})
if bt_rows:
    common.table(bt_rows, fmt={"CRPS margin": "%.2f", "CRPS total": "%.2f"},
                 right=("Brier", "Log loss", "MAE margin", "MAE total"),
                 help={**SC_HELP, "Brier": "GridSim · the closing line. " + SC_HELP["Brier"],
                       "Log loss": "GridSim · the closing line. " + SC_HELP["Log loss"],
                       "MAE margin": "GridSim · the closing line. " + SC_HELP["MAE margin"],
                       "MAE total": "GridSim · the closing line. " + SC_HELP["MAE total"]},
                 hide={"config": 2, "saved": 2, "CRPS margin": 2, "CRPS total": 2, "sims": 3}, height=460)
    st.caption("Where a cell has two numbers, the first is GridSim and the second the closing betting line. "
               "“(example)” marks runs that came with GridSim.")
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
        common.table(pl.read_csv(hist, infer_schema_length=None),
                     labels={"promoted_at": "promoted", "config_hash": "config", "previous_hash": "previous",
                             "validate_season": "tested on", "val_objective": "score", "val_log_loss": "log loss",
                             "val_crps_margin": "CRPS margin", "val_crps_total": "CRPS total"},
                     fmt={"val_objective": "%.4f", "val_log_loss": "%.4f", "val_crps_margin": "%.2f", "val_crps_total": "%.2f"})
    except Exception as e:
        st.warning(f"Could not read tuning history ({e}).")
runs = []
_tr = [(p, shipped) for d, shipped in userdata.run_dirs(cfg, "tuning") for p in sorted(d.glob("*/summary.json"), reverse=True)]
for p, shipped in _tr:
    try:
        t = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        continue
    runs.append({"run": p.parent.name + (" (example)" if shipped else ""), "train": ", ".join(map(str, t.get("train_seasons") or [])),
                 "validate": t.get("validate_season"), "trials": t.get("trials"),
                 "train gain": (t.get("train") or {}).get("gain"), "validation gain": (t.get("validation") or {}).get("gain"),
                 "recommended": t.get("recommended"), "likely overfit": t.get("likely_overfit"),
                 "tuned config": t.get("config_hash_tuned")})
if runs:
    st.markdown("##### Tuning runs (`tuning/*/summary.json`)")
    common.table(runs, fmt={"train gain": "%+.4f", "validation gain": "%+.4f"}, sign=("train gain", "validation gain"),
                 labels={"validate": "tested on", "validation gain": "test gain", "likely overfit": "overfit?",
                         "tuned config": "config"},
                 help={"train gain": "How much better the tuned settings scored on the seasons they were fitted to.",
                       "validation gain": "How much better on a season they never saw: the number that counts.",
                       "likely overfit": "Better on the fitted seasons but not on the unseen one."},
                 hide={"tuned config": 2, "trials": 3, "train": 3}, height=420)
if not hist.exists() and not runs:
    st.caption("No tuning runs yet (Weekly routine → step 5).")
