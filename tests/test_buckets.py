import itertools

import polars as pl

from gridsim import buckets as B


def test_scalar_matches_vectorized():
    rows = list(itertools.product([1, 2, 3, 4], [1, 2, 3, 6, 7, 10, 11, 25], [1, 5, 6, 20, 21, 49, 50, 79, 80, 99],
                                  [-20, -9, -8, -1, 0, 1, 8, 9, 30], [(1, 1500, 2700), (2, 120, 1920), (2, 121, 1921),
                                                                      (4, 100, 100), (4, 290, 290), (4, 600, 600)]))
    df = pl.DataFrame([{"down": d, "ydstogo": t, "yardline_100": y, "score_differential": s, "qtr": q[0],
                        "half_seconds_remaining": q[1], "game_seconds_remaining": q[2]} for d, t, y, s, q in rows])
    v = B.add_bucket_columns(df)
    for r in v.iter_rows(named=True):
        assert r["dist_b"] == B.dist_b(r["ydstogo"])
        assert r["zone_b"] == B.zone_b(r["yardline_100"])
        assert r["score_b"] == B.score_b(r["score_differential"])
        assert r["time_b"] == B.time_b(r["qtr"], r["half_seconds_remaining"], r["game_seconds_remaining"])


def test_zone_edges():
    assert [B.zone_b(y) for y in (99, 80, 79, 50, 49, 21, 20, 6, 5, 1)] == [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]


def test_team_levels_never_mix_field_zones():
    """Team pools keep red zone 20-6 and goal-to-go 5-1 apart at every level (no all-field fallback): a row from
    the 15 applied at the 3 would turn any 3+ yard gain into a TD."""
    from gridsim import buckets as B
    assert all("fz" in lvl for lvl in B.TEAM_LEVELS)
    assert [B.fz_b(y) for y in (99, 21, 20, 6, 5, 1)] == [0, 0, 1, 1, 2, 2]
    import polars as pl
    df = B.add_bucket_columns(pl.DataFrame({"ydstogo": [10, 10, 10, 5, 1], "yardline_100": [75, 21, 20, 5, 1],
                                            "score_differential": [0] * 5, "qtr": [1] * 5, "down": [1] * 5,
                                            "half_seconds_remaining": [900] * 5, "game_seconds_remaining": [3000] * 5}))
    assert df["fz"].to_list() == [0, 0, 1, 2, 2]


def test_kickoff_era_for_seasons_before_the_first_listed_one():
    """2022 and earlier are old-rules seasons: they must not fall to the newest era (touchback at the 35)."""
    from gridsim.config import load_config
    from gridsim.model import kickoff_era, kickoff_era_seasons
    cfg = load_config()
    eras = {int(k): v for k, v in cfg.kickoff_rules["eras"].items()}
    first = min(eras)
    assert kickoff_era(first - 1, cfg) == eras[first] == kickoff_era(first - 3, cfg)
    assert kickoff_era(max(eras) + 1, cfg) == cfg.kickoff_rules["default_era"]
    for s, e in eras.items():
        assert kickoff_era(s, cfg) == e
    assert kickoff_era_seasons(first - 1, cfg, [first - 3, first - 2, first - 1, first]) == [first - 3, first - 2, first - 1]
