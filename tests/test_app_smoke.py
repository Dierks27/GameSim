"""GUI smoke tests: every page compiles, and the pure helpers in app/helpers.py (combo / live-state string
builders, config.yaml comment parser + line-preserving writer, report parsers) behave. No network, no
Streamlit server."""
from __future__ import annotations

import hashlib
import py_compile
import shutil
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
sys.path.insert(0, str(APP))

import helpers  # noqa: E402

PAGES = sorted((APP / "pages").glob("*.py"))


@pytest.mark.parametrize("path", [APP / "streamlit_app.py", APP / "common.py", APP / "helpers.py", *PAGES],
                         ids=lambda p: p.name)
def test_compiles(path, tmp_path):
    py_compile.compile(str(path), cfile=str(tmp_path / (path.name + "c")), doraise=True)


def test_all_pages_registered():
    nav = (APP / "streamlit_app.py").read_text(encoding="utf-8")
    common = (APP / "common.py").read_text(encoding="utf-8")
    for p in PAGES:
        assert f'"pages/{p.name}"' in common, f"{p.name} has no page constant in common.py"
    for const in ("WEEK_PAGE", "GAME_PAGE", "PROPS_PAGE", "PARLAY_PAGE", "WHATIF_PAGE", "SIGNALS_PAGE",
                  "SCORECARD_PAGE", "DIAGNOSE_PAGE", "SETTINGS_PAGE"):
        assert f"st.Page(common.{const}" in nav


# ---- combo legs -----------------------------------------------------------------------------------------

def test_leg_strings_match_combo_syntax():
    L = helpers.leg_string
    assert L("team_win", "min") == "MIN:win"
    assert L("team_lose", "GB") == "GB:lose"
    assert L("team_cover", "BUF", line=-3.5) == "BUF:cover-3.5"
    assert L("team_cover", "BUF", line=3) == "BUF:cover+3"
    assert L("team_pts", "KC", op="<", line=20.5) == "KC:pts<20.5"
    assert L("total", op=">", line=44.5) == "total>44.5"
    assert L("margin", op="<", line=-3.5) == "margin<-3.5"
    assert L("player", player="J.Jefferson", stat="rec_yds", line=79.5) == "J.Jefferson:rec_yds>79.5"
    assert L("player", player="Josh Allen", stat="anytime_td", line=0.5) == "Josh Allen:anytime_td>0.5"
    combo = helpers.combo_string(["MIN:win", " ", "total>44.5", "J.Jefferson:rec_yds>79.5"])
    assert combo == "MIN:win; total>44.5; J.Jefferson:rec_yds>79.5"
    for bad in (dict(kind="team_win", team="MINN"), dict(kind="total", line=None), dict(kind="total", line=-1),
                dict(kind="player", player="A:B", stat="rec", line=1), dict(kind="player", player="X", stat="rec", line=-1),
                dict(kind="nope"), dict(kind="total", op="=", line=3)):
        with pytest.raises(ValueError):
            L(**bad)
    with pytest.raises(ValueError):
        helpers.combo_string(["", "  "])


def test_leg_strings_parse_with_library_regexes():
    """The library's leg parser accepts every team/total leg the builder makes (no sims needed)."""
    import re
    for leg in ("total>44.5", "margin<-3.5"):
        assert re.match(r"(?i)^(total|margin)\s*([<>])\s*(-?[\d.]+)$", leg)
    for leg in ("BUF:cover-3.5", "BUF:cover+3"):
        assert re.match(r"^cover\s*([+-]?[\d.]+)$", leg.split(":")[1])
    assert re.match(r"^pts\s*([<>])\s*([\d.]+)$", helpers.leg_string("team_pts", "KC", line=20.5).split(":")[1])


# ---- live state -----------------------------------------------------------------------------------------

def test_state_string_round_trips_through_library_parser():
    from gridsim.state import parse_state
    s = helpers.state_string("Q3", 8, 42, "GB", 13, "MIN", 17, "MIN", down=2, distance=6, spot_side="GB", spot_yard=34,
                             home_timeouts=3, away_timeouts=2)
    assert s == "Q3 8:42 GB 13 MIN 17 MIN ball 2nd&6 at GB 34 timeouts 3/2"
    p = parse_state(s, "GB", "MIN")
    assert p == {"qtr": 3, "clock": 522.0, "score": [13, 17], "timeouts": [3, 2], "pos": 1, "yl": 34, "down": 2, "togo": 6}
    # own side -> yards to goal is 100 - yard; goal to go; midfield
    p = parse_state(helpers.state_string("Q4", 1, 55, "KC", 20, "BUF", 24, "KC", 1, 10, "KC", 25), "KC", "BUF")
    assert (p["pos"], p["yl"], p["togo"]) == (0, 75, 10)
    p = parse_state(helpers.state_string("Q2", 0, 30, "KC", 3, "BUF", 0, "BUF", 1, "goal", "KC", 4), "KC", "BUF")
    assert (p["pos"], p["yl"], p["togo"]) == (1, 4, 4)
    p = parse_state(helpers.state_string("OT", 6, 10, "DET", 20, "GB", 20, "GB", 3, 2, None, 50, 1, 2), "DET", "GB")
    assert (p["qtr"], p["yl"], p["timeouts"]) == (5, 50, [1, 2])
    k = helpers.state_string("Q1", 15, 0, "DET", 0, "GB", 0, "DET", kickoff=True, receives2h="GB")
    assert k == "Q1 15:00 DET 0 GB 0 kickoff DET timeouts 3/3 receives2h GB"
    p = parse_state(k, "DET", "GB")
    assert p["kickoff"] == 0 and p["receives2h"] == 1
    for bad in (dict(quarter="Q5"), dict(seconds=60), dict(minutes=16), dict(possession="NYJ"), dict(down=5),
                dict(spot_yard=51), dict(home_timeouts=4), dict(distance=0)):
        args = dict(quarter="Q3", minutes=5, seconds=0, home="GB", home_score=0, away="MIN", away_score=0,
                    possession="GB", down=1, distance=10, spot_side="GB", spot_yard=25)
        args.update(bad)
        with pytest.raises(ValueError):
            helpers.state_string(**args)


# ---- props helpers --------------------------------------------------------------------------------------

def test_approx_over_from_percentiles():
    st = {"p10": 20, "p25": 40, "p50": 60, "p75": 80, "p90": 100}
    f = helpers.approx_over_from_percentiles
    assert f(st, 60) == pytest.approx(0.5)
    assert f(st, 40) == pytest.approx(0.75)
    assert f(st, 50) == pytest.approx(0.625)
    assert f(st, 1000) == 0.0 and f(st, -1000) == 1.0
    # discrete stat with tied percentiles (TDs): monotone and inside [0, 1]
    td = {"p10": 0, "p25": 0, "p50": 0, "p75": 1, "p90": 2}
    vals = [f(td, x) for x in (0.5, 1.5, 2.5)]
    assert vals == sorted(vals, reverse=True) and all(0 <= v <= 1 for v in vals)
    assert helpers.default_prop_line(63.0) == 63.5 and helpers.default_prop_line(0) == 0.5


def test_histogram_over_under_is_exact():
    """The props page reads over / under from the per-sim histogram with the library's cover_from_hist."""
    from gridsim.api import cover_from_hist
    hist = {"values": [0, 10, 20, 30], "counts": [1, 2, 3, 4]}
    r = cover_from_hist(hist, 15.5, "total")
    assert r["hit"] == pytest.approx(0.7) and r["miss"] == pytest.approx(0.3) and r["push"] == 0
    r = cover_from_hist(hist, 20, "total")
    assert r["hit"] == pytest.approx(0.4) and r["push"] == pytest.approx(0.3)


def test_prop_market_rows():
    rows = [{"label": "Justin Jefferson 80+ receiving yards"}, {"label": "MIN wins"}, {"label": "J. Jeff 1+ TD"}]
    assert helpers.prop_market_rows(rows, "Justin Jefferson") == rows[:1]
    assert helpers.prop_market_rows([], "X") == []
    assert helpers.prop_market_rows(rows, "Nobody Here") == []


def test_signal_history_figures():
    sys.path.insert(0, str(APP))
    import common
    rows = [{"ts_utc": "2026-10-01T12:00:00", "market": "total", "pnl": 0.9, "clv": 0.02},
            {"ts_utc": "2026-10-02T12:00:00", "market": "total", "pnl": -1.0, "clv": None},
            {"ts_utc": "2026-10-01T13:00:00", "market": "moneyline", "pnl": -1.0, "clv": -0.01}]
    pnl, clv = common.signal_history_figures(rows)
    names = {t.name: t for t in pnl.data}
    assert set(names) == {"moneyline", "total"}
    assert list(names["total"].y) == pytest.approx([0.9, -0.1])
    assert list({t.name: t for t in clv.data}["total"].y) == pytest.approx([2.0, 2.0])


# ---- report text parsers --------------------------------------------------------------------------------

def test_parse_signals_report():
    text = ("market    confidence    n   hit%  avg CLV  P&L (units)\n"
            "moneyline watchlist     12   58.3    0.012         1.25\n"
            "total     moderate       3   33.3   -0.020        -1.10\n"
            "trust: moneyline: watchlist (n=12, CLV +0.012)")
    rows, trust = helpers.parse_signals_report(text)
    assert rows == [{"market": "moneyline", "confidence": "watchlist", "n": 12, "hit_pct": 58.3, "avg_clv": 0.012, "pnl": 1.25},
                    {"market": "total", "confidence": "moderate", "n": 3, "hit_pct": 33.3, "avg_clv": -0.02, "pnl": -1.1}]
    assert trust.startswith("moneyline")
    assert helpers.parse_signals_report("no graded signals yet") == ([], "")


def test_readme_schedule_section():
    sec = helpers.readme_section((ROOT / "README.md").read_text(encoding="utf-8"), "## Scheduling")
    assert sec is None or "weekly" in sec
    assert helpers.readme_section("# x\n## Other\nbody", "## Scheduling") is None
    assert helpers.readme_section("## Scheduling on Windows\nA\n## Next\nB") == "A"
    assert helpers.is_flag("True") and helpers.is_flag(True) and not helpers.is_flag("False") and not helpers.is_flag(False)


# ---- config.yaml parser + line-preserving writer --------------------------------------------------------

def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_config_parser_matches_yaml():
    text = (ROOT / "config.yaml").read_text(encoding="utf-8")
    entries = helpers.parse_config(text)
    raw = yaml.safe_load(text)
    assert {e["section"] for e in entries} == set(raw)
    editable = [e for e in entries if e["editable"]]
    assert len(editable) > 40
    for e in entries:
        if e["kind"] != "section":
            assert helpers.get_path(raw, e["path"]) == e["value"], e["path"]
    by = {e["path"]: e for e in entries}
    assert by[("sim", "n_sims")]["kind"] == "int"
    assert "margin of error" not in by[("sim", "n_sims")]["comment"]
    assert by[("data", "refresh_hours")]["comment"].startswith("re-download")
    assert by[("markets", "kalshi", "base_url")]["value"].startswith("https://")     # ':' inside an unquoted value
    assert by[("data", "season_weights")]["kind"] == "list" and not by[("data", "season_weights")]["editable"]


def test_config_rewrite_preserves_everything_else(tmp_path):
    src = ROOT / "config.yaml"
    before_sha = _sha(src)
    p = tmp_path / "config.yaml"
    shutil.copy2(src, p)
    orig_text = p.read_text(encoding="utf-8")
    changes = {("sim", "k_outcome"): 75, ("sim", "w_off"): 0.55, ("data", "include_postseason"): False,
               ("home_field", "tilt"): 1e-05, ("markets", "kalshi", "base_url"): "https://example.test/v2",
               ("scanner", "schedule_note"): 'it\'s "quoted": fine # not a comment'}
    bak = helpers.save_config(p, changes)
    assert bak.name == "config.yaml.bak" and bak.read_text(encoding="utf-8") == orig_text
    new_text = p.read_text(encoding="utf-8")
    old_lines, new_lines = orig_text.splitlines(), new_text.splitlines()
    assert len(old_lines) == len(new_lines)
    changed = [i for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b]
    assert len(changed) == len(changes)
    for i in changed:                                    # the inline comment survives on every edited line
        if "#" in old_lines[i].split(":", 1)[1] and "schedule_note" not in old_lines[i]:
            assert old_lines[i][old_lines[i].index(" #"):].strip() == new_lines[i][new_lines[i].index(" #"):].strip()
    from gridsim.config import load_config
    cfg = load_config(p)
    assert cfg.sim["k_outcome"] == 75 and isinstance(cfg.sim["k_outcome"], int)
    assert cfg.sim["w_off"] == 0.55 and cfg.data["include_postseason"] is False
    assert cfg.home_field["tilt"] == pytest.approx(1e-05) and isinstance(cfg.home_field["tilt"], float)
    assert cfg.markets["kalshi"]["base_url"] == "https://example.test/v2"
    assert cfg.scanner["schedule_note"] == 'it\'s "quoted": fine # not a comment'
    assert cfg.hash != load_config(src).hash
    # writing the original values back restores the file byte-for-byte
    orig = load_config(src)
    helpers.save_config(p, {("sim", "k_outcome"): orig.sim["k_outcome"], ("sim", "w_off"): orig.sim["w_off"],
                            ("data", "include_postseason"): orig.data["include_postseason"],
                            ("home_field", "tilt"): orig.home_field["tilt"],
                            ("markets", "kalshi", "base_url"): orig.markets["kalshi"]["base_url"],
                            ("scanner", "schedule_note"): orig.scanner["schedule_note"]})
    assert p.read_text(encoding="utf-8") == orig_text
    assert _sha(src) == before_sha                        # the real config.yaml was never touched


def test_config_rewrite_keeps_crlf_and_rejects_bad_edits(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_bytes(b"# head\r\nsim:\r\n  n: 5        # five\r\n  f: 1.5\r\n  s: abc\r\n  l: [1, 2]\r\n")
    helpers.save_config(p, {("sim", "n"): 7, ("sim", "s"): "x: y"})
    assert p.read_bytes() == b'# head\r\nsim:\r\n  n: 7        # five\r\n  f: 1.5\r\n  s: "x: y"\r\n  l: [1, 2]\r\n'
    # single-quoted value with an escaped quote and a '#' inside, plus a real comment after it
    q = tmp_path / "q.yaml"
    q.write_text("a:\n  s: 'it''s # in'   # real comment\n  t: 3\n", encoding="utf-8")
    e = {x["path"]: x for x in helpers.parse_config(q.read_text(encoding="utf-8"))}[("a", "s")]
    assert e["value"] == "it's # in" and e["comment"] == "real comment"
    helpers.save_config(q, {("a", "s"): "don't"})
    assert q.read_text(encoding="utf-8") == "a:\n  s: 'don''t'       # real comment\n  t: 3\n"
    text = p.read_text(encoding="utf-8")
    for bad in ({("sim", "l"): [3]}, {("sim", "nope"): 1}, {("sim", "n"): "seven"}, {("sim", "n"): 1.5},
                {("sim", "f"): float("nan")}, {("sim", "s"): "two\nlines"}):
        with pytest.raises(ValueError):
            helpers.rewrite_config(text, bad)
    assert helpers.rewrite_config(text, {}) == text


def test_the_version_is_a_release_date_and_the_same_everywhere():
    import datetime as dt
    import re

    import gridsim
    y, m, d = (int(x) for x in gridsim.__version__.split("."))
    dt.date(y, m, d)                                               # a real date
    toml = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version = "(.+)"$', toml, re.M).group(1) == gridsim.__version__
    assert "gridsim.__version__" in (APP / "streamlit_app.py").read_text(encoding="utf-8")
    assert "gridsim.__version__" in (ROOT / "Start GridSim.bat").read_text(encoding="utf-8")


# ---- tables that fit ---------------------------------------------------------------------------------------

def test_tables_wrap_text_keep_numbers_on_one_line_and_escape_what_they_show():
    import common
    html = common.table_html(
        [{"what": "NE wins <b>", "costs": 44.0, "edge": -2.5, "lean": 9.1, "note": "", "ok": True, "Kalshi": "https://k/x?a=1&b=2"},
         {"what": "under 44.5", "costs": 51.0, "edge": 0.0, "lean": -3.0, "note": None, "ok": False, "Kalshi": None}],
        fmt={"costs": "%.0f¢", "edge": "%+.1f", "lean": "%+.1f"}, link={"Kalshi": "open ↗"}, sign=("edge", "lean"),
        help={"lean": 'GridSim\'s "own" view'}, hide={"note": 2}, labels={"what": "you buy"})
    assert "NE wins &lt;b&gt;" in html and "<b>" not in html                      # what a cell shows is never markup
    assert '<td class="n" data-label="costs">44¢</td>' in html and '<td class="n neg" data-label="edge">-2.5</td>' in html
    assert '<td class="n pos" data-label="lean">+9.1</td>' in html and '<td class="n" data-label="edge">+0.0</td>' in html
    assert '<td class="t" data-label="you buy">NE wins' in html and '<th class="t">you buy</th>' in html
    assert 'title="GridSim&#x27;s &quot;own&quot; view"' in html                  # the heading explains itself
    assert 'class="t w2 empty" data-label="note">—<' in html and ">yes<" in html and ">no<" in html
    assert '<a href="https://k/x?a=1&amp;b=2" target="_blank" rel="noopener">open ↗</a>' in html
    assert common.table_html([]) == "" and common.table_html(None) == ""
    import polars as pl
    assert ">3<" in common.table_html(pl.DataFrame({"a": [3]}))                    # a data frame works too


def test_no_page_uses_the_grid_that_scrolls_sideways():
    """Every table goes through common.table / common.board (they fit the page); the page itself has no fixed width."""
    for p in [APP / "streamlit_app.py", APP / "common.py", *PAGES]:
        src = p.read_text(encoding="utf-8")
        assert "st.dataframe(" not in src and "st.data_editor(" not in src, p.name
    css = (APP / "common.py").read_text(encoding="utf-8")
    assert "max-width: none" in css and "max-width: 1180px" not in css
