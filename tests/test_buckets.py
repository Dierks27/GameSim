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
