"""Diagnose: browse saved diagnose reports (diagnose/*/diagnose.json, written by `gridsim diagnose` and
`gridsim weekly`): what is most likely wrong, component bias, residual groups, worst misses, calibration."""
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

import common
import helpers
from gridsim import userdata

hr = common.hr
cfg = common.cfg()

common.page_header("Diagnose", "Where is the sim wrong? Every bias is measured in standard errors so noise is not "
                               "mistaken for a problem. The diagnosis never changes code or config.")

# yours (data folder) first, newest first; then the example reports shipped with GridSim
SHIPPED: set[str] = set()
cands = []
for name, pat in (("diagnose", "*/diagnose.json"), ("tuning", "*/diagnose_validation/diagnose.json")):
    for d_, shipped in userdata.run_dirs(cfg, name):
        found = list(d_.glob(pat)) if d_.exists() else []
        if shipped:
            SHIPPED.update(str(p) for p in found)
        cands += found
cands.sort(key=lambda p: (str(p) in SHIPPED, -p.stat().st_mtime))
if not cands:
    st.info("No check-up reports yet. Open **Weekly routine** and press **Run check-up**.")
    st.stop()


def _label(p: Path) -> str:
    parts = p.parent.parts
    i = max((k for k, x in enumerate(parts) if x in ("diagnose", "tuning")), default=len(parts) - 1)
    rel = "/".join(parts[i:])
    return f"{rel} · {common.mtime_text(p)}" + (" · example shipped with GridSim" if str(p) in SHIPPED else "")


ss = st.session_state
labels = [str(p) for p in cands]
key = common.keep("dg_pick", labels[0])
if ss[key] not in labels:
    ss[key] = labels[0]
pick = Path(st.selectbox("Report", labels, key=key, format_func=lambda x: _label(Path(x))))
try:
    d = json.loads(pick.read_text(encoding="utf-8"))
except (OSError, ValueError) as e:
    st.error(f"Could not read {pick}: {e}")
    st.stop()

# ---- top 3 ---------------------------------------------------------------------------------------------
items = "".join(f"<li>{hr.esc(x)}</li>" for x in d.get("summary") or [])
common.callout(f"<h2>Top 3 things most likely wrong</h2><ol>{items}</ol>", tone="warn")

html_path = pick.with_name("diagnose.html")
c1, c2 = st.columns([2, 3], vertical_alignment="center")
if html_path.exists():
    c1.download_button("Download HTML report", data=lambda: html_path.read_bytes(),
                       file_name=f"{pick.parent.name}_diagnose.html", mime="text/html", on_click="ignore",
                       width="stretch", icon=":material/download:")
    c2.caption(f"Full report with interactive charts: `{html_path}` — open the downloaded file in any browser.")

# ---- headline metrics ----------------------------------------------------------------------------------
m = d.get("metrics") or {}
mk = m.get("market") or {}
cov = d.get("coverage") or {}
tiles = [("Games", f"{m.get('games', '–')}", "")]
for k, lab, f in (("brier", "Brier", "%.4f"), ("log_loss", "Log loss", "%.4f"), ("mae_margin", "MAE margin", "%.2f"),
                  ("mae_total", "MAE total", "%.2f")):
    if m.get(k) is not None:
        tiles.append((lab, f % m[k], f"market {f % mk[k]}" if mk.get(k) is not None else ""))
for k, lab in (("coverage_80_margin", "80% margin cover"), ("coverage_80_total", "80% total cover")):
    if cov.get(k) is not None:
        tiles.append((lab, f"{100 * cov[k]:.0f}%", "target 80%"))
st.html(common.kv_html(tiles))

tabs = st.tabs(["Components", "Residual groups", "Worst misses", "Calibration", "All metrics"])
with tabs[0]:
    comp = d.get("components") or []
    if comp:
        st.plotly_chart(common.tvalue_figure(comp), theme=None, config=hr.PLOTLY_CONFIG)
        st.caption("Orange = flagged: |bias| > 2 standard errors (bootstrap over team-games). Dotted lines at ±2 SE.")
        rows = [[hr.esc(c["component"]), f"{c['sim']:.3g}", f"{c['actual']:.3g}", f"{c['bias']:+.3g}",
                 f"{100 * c.get('rel_bias', 0):+.1f}%", f"{c['t']:+.1f}", f'<span class="ink2">{hr.esc(c.get("points_to", ""))}</span>']
                for c in comp]
        st.html('<div class="gs card lastleft"><h2>Component scorecard</h2>'
                + hr.table_html(["Component", "Sim", "Actual", "Bias", "Rel.", "t (SE)", "Points to"], rows,
                                hl={i for i, c in enumerate(comp) if helpers.is_flag(c.get("flag"))}, wrap=True) + "</div>")
with tabs[1]:
    res = d.get("residuals") or []
    if res:
        rows = [[hr.esc(r["group"]), hr.esc(r["metric"]), str(r["n"]), f"{r['mean_error']:+.2f}", f"{r['se']:.2f}",
                 f"{r['t']:+.1f}"] for r in res]
        st.html('<div class="gs card"><h2>Residual breakdown</h2><p class="muted" style="font-size:12px;margin:-6px 0 8px">'
                "error = predicted − actual (points); highlighted = more than 2 SE from zero</p>"
                + '<div class="tall">' + hr.table_html(["Group", "Metric", "n", "Mean error", "SE", "t"], rows,
                                                         hl={i for i, r in enumerate(res) if helpers.is_flag(r.get("flag"))})
                + "</div></div>")
    else:
        st.caption("No residual groups with enough games.")
with tabs[2]:
    wm = d.get("worst_misses") or []
    if wm:
        rows = [[hr.esc(w["score"]), hr.esc(w["pred"]), hr.pct(w.get("p_home"), 0),
                 hr.esc(w.get("market_margin") if w.get("market_margin") is not None else "–"), f"{w['miss']:.1f}"] for w in wm]
        st.html('<div class="gs card"><h2>Worst misses</h2>' + hr.table_html(
            ["Final", "Sim (mean points)", "P(home)", "Market home margin", "Miss"], rows, wrap=True)
            + '<p class="muted" style="font-size:12px;margin:8px 0 0">Miss = |margin error| + ½ |total error|. '
              "Component detail for each game is in diagnose.json.</p></div>")
with tabs[3]:
    cal = d.get("calibration") or []
    if cal:
        st.plotly_chart(common.calibration_figure(cal), theme=None, config=hr.PLOTLY_CONFIG)
        rows = [[hr.esc(c["bin"]), str(c["n"]), hr.pct(c["pred"]), hr.pct(c["actual"])] for c in cal]
        st.html('<div class="gs">' + hr.table_html(["Bin", "Games", "Predicted", "Actual"], rows) + "</div>")
with tabs[4]:
    rows = []
    for k, v in m.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            rows.append({"metric": k, "sim": v, "market": mk.get(k)})
    st.dataframe(rows, hide_index=True, width="stretch",
                 column_config={"sim": st.column_config.NumberColumn(format="%.4f"),
                                "market": st.column_config.NumberColumn(format="%.4f")})
