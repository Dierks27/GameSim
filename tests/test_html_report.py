"""HTML report: built from a synthetic summary, so no network or play-by-play data is needed."""
import json
import re

import numpy as np

from gridsim.engine import NS, S, SimResults
from gridsim.html_report import fmt_line, fmt_pm, margin_figure, total_figure, write_html
from gridsim.report import summarize


def _summary(**kw):
    rng = np.random.default_rng(3)
    n = 400
    st = np.zeros((n, 2, NS))
    st[:, 0, S["pts"]] = rng.integers(10, 35, n)
    st[:, 1, S["pts"]] = rng.integers(7, 31, n)
    res = SimResults("HOM", "AWY", st, {"neutral": False, "playoff": False, "seed": 11})
    extra = {"banners": ["test banner"], "market": {"spread_home": -2.5, "total": 41.5, "home_ml": -140, "away_ml": 120}}
    return summarize(res, kw.get("spread", -2.5), kw.get("total", 41.5), {"window_seasons": [2025]}, "abc123", extra)


def test_write_html_contains_matchup_and_win_pct(tmp_path):
    s = json.loads(json.dumps(_summary(), default=float))   # round-trip like the cached JSON the GUI reads
    out = tmp_path / "r.html"
    write_html(s, out)
    text = out.read_text(encoding="utf-8")
    assert "AWY @" in text and "HOM" in text
    w = s["win"]["home"]
    assert f"{100 * w['p']:.1f}%" in text and f"± {100 * w['moe']:.1f}" in text
    assert "test banner" in text and "abc123" in text
    # self-contained: plotly.js is inline and nothing is loaded from a CDN
    assert "Plotly.newPlot" in text and len(text) > 1_000_000
    assert not re.search(r"<script[^>]*\bsrc=", text) and not re.search(r"<link[^>]*\bhref=", text)


def test_write_html_without_lines_or_market(tmp_path):
    s = _summary(spread=None, total=None)
    s.pop("market")
    out = tmp_path / "r2.html"
    write_html(s, out)
    assert "Market vs simulation" not in out.read_text(encoding="utf-8")


def test_formatters_and_figures():
    assert fmt_line(-2.5) == "-2.5" and fmt_line(3) == "+3.0" and fmt_line(0) == "PK"
    assert fmt_pm({"p": 0.613, "moe": 0.011}) == "61.3% ± 1.1"
    s = _summary()
    assert len(margin_figure(s, -2.5).data) >= 2
    assert len(total_figure(s, None).data) == 1
