"""League-average sanity check (slow): sim a full season of matchups vs real averages."""
import pytest

from gridsim.sanity import format_table, league_sanity

TOL = {"points": 0.05, "plays": 0.04, "pass_rate": 0.04, "sack_rate": 0.08, "int_rate": 0.10, "punts": 0.08,
       "fg_pct": 0.04, "fga": 0.10, "comp_pct": 0.03, "ypa": 0.04, "ypc": 0.05, "third_pct": 0.05, "rz_td_pct": 0.06,
       "turnovers": 0.10, "total_yds": 0.05, "drives": 0.05, "ko_start": 0.04, "punt_start": 0.04}


@pytest.mark.slow
def test_league_sanity(cfg):
    df = league_sanity(cfg, 2025, sims_per_game=8)
    print("\n" + format_table(df))
    bad = []
    for r in df.iter_rows(named=True):
        tol = TOL.get(r["metric"])
        if tol is not None and abs(r["rel_err"]) > tol:
            bad.append(f"{r['metric']}: {100 * r['rel_err']:.1f}% (tol {100 * tol:.0f}%)")
    assert not bad, "sanity metrics out of tolerance: " + "; ".join(bad)
