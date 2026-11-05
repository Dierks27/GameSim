"""League-level sanity check: sim every game of a season and compare aggregate rates to reality.

Definitions are matched between sim and data:
  plays      pass attempts (incl. spikes) + sacks + rushes (incl. scrambles, kneels)
  pass rate  (pass attempts + sacks) / plays
  sack rate  sacks / (pass attempts + sacks);  INT rate  INT / pass attempts
"""
from __future__ import annotations

import numpy as np
import polars as pl

from . import data as D
from .engine import S, simulate
from .model import build_model, load_prepared

METRICS = ["points", "plays", "pass_rate", "sack_rate", "int_rate", "comp_pct", "ypa", "ypc", "punts", "fga",
           "fg_pct", "tds", "rz_trips", "rz_td_pct", "third_pct", "fourth_go", "turnovers", "pass_yds_net",
           "rush_yds", "total_yds", "drives", "ko_start", "punt_start", "penalties", "two_pt_att"]


def actual_team_games(plays: pl.DataFrame, sched: pl.DataFrame, season: int) -> dict[str, float]:
    p = plays.filter((pl.col("season") == season) & (pl.col("season_type") == "REG") & pl.col("posteam").is_not_null())
    f = lambda c: pl.col(c).fill_null(0)  # noqa: E731
    p = p.with_columns(
        is_att=((f("complete_pass") == 1) | (f("incomplete_pass") == 1) | (f("interception") == 1)) & (f("sack") == 0)
        & (pl.col("play_type") == "pass") & (f("two_point_attempt") == 0),
        is_spike=(pl.col("play_type") == "qb_spike"),
        is_sack=(f("sack") == 1) & (pl.col("play_type") == "pass"),
        is_rush=((pl.col("play_type") == "run") | (pl.col("play_type") == "qb_kneel")) & (f("two_point_attempt") == 0),
    )
    g = p.group_by(["game_id", "posteam"]).agg(
        att=(pl.col("is_att") | pl.col("is_spike")).sum(), sacks=pl.col("is_sack").sum(), rush=pl.col("is_rush").sum(),
        ints=((f("interception") == 1) & pl.col("is_att")).sum(),
        fum=((f("fumble_lost") == 1) & pl.col("play_type").is_in(["pass", "run"])).sum(),
        punts=(pl.col("play_type") == "punt").sum(), fga=(pl.col("play_type") == "field_goal").sum(),
        fgm=((pl.col("play_type") == "field_goal") & (pl.col("field_goal_result") == "made")).sum(),
        tds=(f("touchdown") == 1).and_(pl.col("td_team") == pl.col("posteam")).sum(),
        third_att=((pl.col("down") == 3) & pl.col("play_type").is_in(["pass", "run"])).sum(),
        third_conv=((pl.col("down") == 3) & pl.col("play_type").is_in(["pass", "run"]) & ((f("first_down") == 1) | (f("touchdown") == 1))).sum(),
        pass_yds=(pl.when(pl.col("play_type") == "pass").then(f("yards_gained")).otherwise(0)).sum(),
        rush_yds=(pl.when(pl.col("play_type").is_in(["run", "qb_kneel"])).then(f("yards_gained")).otherwise(0)).sum(),
        two=(f("two_point_attempt") == 1).sum(),
        comp=((f("complete_pass") == 1) & pl.col("is_att")).sum(),
        gross=(pl.when(pl.col("is_att")).then(f("yards_gained")).otherwise(0)).sum(),
        rush_only=(pl.when(pl.col("play_type") == "run").then(f("yards_gained")).otherwise(0)).sum(),
        rush_n=(pl.col("play_type") == "run").sum(),
        fourth_go=((pl.col("down") == 4) & pl.col("play_type").is_in(["pass", "run"])).sum(),
    )
    sc = p.filter(pl.col("play_type").is_in(["pass", "run", "punt", "field_goal", "qb_kneel", "qb_spike", "no_play"])
                  & pl.col("down").is_not_null())
    drives = sc.group_by(["game_id", "posteam", "fixed_drive"]).agg(
        rz=(pl.col("yardline_100") <= 20).any(), td=pl.col("td_off").any())
    pen = p.filter((f("penalty") == 1) & pl.col("play_type").is_in(["pass", "run", "no_play"]) & pl.col("down").is_not_null()).height
    ko = p.filter((pl.col("play_type") == "kickoff") & ~pl.col("kick_retained") & pl.col("rec_yl").is_not_null()
                  & ~pl.col("td_off") & ~pl.col("td_def") & (pl.col("yardline_100") == 35))
    pu = p.filter((pl.col("play_type") == "punt") & ~pl.col("kick_retained") & pl.col("rec_yl").is_not_null())
    s = sched.filter((pl.col("season") == season) & (pl.col("game_type") == "REG") & pl.col("home_score").is_not_null())
    n_tg = 2 * s.height
    pts = float((s["home_score"].sum() + s["away_score"].sum()) / n_tg)
    tot = {c: float(g[c].sum()) for c in g.columns if c not in ("game_id", "posteam")}
    ng = g.height
    plays_n = tot["att"] + tot["sacks"] + tot["rush"]
    return {
        "points": pts, "plays": plays_n / ng, "pass_rate": (tot["att"] + tot["sacks"]) / plays_n,
        "sack_rate": tot["sacks"] / (tot["att"] + tot["sacks"]), "int_rate": tot["ints"] / tot["att"],
        "punts": tot["punts"] / ng, "fga": tot["fga"] / ng, "fg_pct": tot["fgm"] / max(tot["fga"], 1),
        "tds": tot["tds"] / ng, "third_pct": tot["third_conv"] / tot["third_att"],
        "turnovers": (tot["ints"] + tot["fum"]) / ng, "pass_yds_net": tot["pass_yds"] / ng,
        "rush_yds": tot["rush_yds"] / ng, "total_yds": (tot["pass_yds"] + tot["rush_yds"]) / ng,
        "penalties": pen / ng, "two_pt_att": tot["two"] / ng,
        "comp_pct": tot["comp"] / tot["att"], "ypa": tot["gross"] / tot["att"], "ypc": tot["rush_only"] / tot["rush_n"],
        "rz_trips": drives["rz"].sum() / ng, "rz_td_pct": float((drives["rz"] & drives["td"]).sum() / drives["rz"].sum()),
        "fourth_go": tot["fourth_go"] / ng, "drives": drives.height / ng,
        "ko_start": float(ko["rec_yl"].mean()), "punt_start": float(pu["rec_yl"].mean()),
    }


def sim_team_games(stats: np.ndarray) -> dict[str, float]:
    """stats: (n_games, 2, NS) stacked over all simulated games."""
    x = stats.reshape(-1, stats.shape[-1])
    c = lambda k: float(x[:, S[k]].sum())  # noqa: E731
    n = x.shape[0]
    att, sacks, rush = c("pass_att"), c("sacks"), c("rush_att")
    plays = att + sacks + rush
    pyds = c("pass_yds_gross") - c("sack_yds")
    return {
        "points": c("pts") / n, "plays": plays / n, "pass_rate": (att + sacks) / plays,
        "sack_rate": sacks / (att + sacks), "int_rate": c("ints") / att, "punts": c("punts") / n,
        "fga": c("fga") / n, "fg_pct": c("fgm") / max(c("fga"), 1), "tds": c("tds") / n,
        "third_pct": c("third_conv") / c("third_att"), "turnovers": (c("ints") + c("fum_lost")) / n,
        "pass_yds_net": pyds / n, "rush_yds": c("rush_yds") / n, "total_yds": (pyds + c("rush_yds")) / n,
        "penalties": c("penalties") / n, "two_pt_att": c("two_att") / n,
        "comp_pct": c("comp") / (att - c("spikes")), "ypa": c("pass_yds_gross") / (att - c("spikes")),
        "ypc": (c("rush_yds") + c("kneels")) / (rush - c("kneels")),
        "rz_trips": c("rz_trips") / n, "rz_td_pct": c("rz_tds") / max(c("rz_trips"), 1),
        "fourth_go": c("fourth_go") / n, "drives": c("drives") / n,
        "ko_start": c("ko_start_yl") / max(c("ko_starts"), 1), "punt_start": c("punt_start_yl") / max(c("punt_starts"), 1),
    }


def league_sanity(cfg, season: int, sims_per_game: int = 20, seed: int = 7, max_games: int | None = None,
                  verbose: bool = False) -> pl.DataFrame:
    plays = load_prepared(season, cfg, verbose=verbose)
    sched = D.load_schedules(cfg)
    model = build_model(cfg, season, None, plays)
    games = sched.filter((pl.col("season") == season) & (pl.col("game_type") == "REG") & pl.col("home_score").is_not_null())
    if max_games:
        games = games.head(max_games)
    chunks = []
    for i, gm in enumerate(games.iter_rows(named=True)):
        r = simulate(model, gm["home_team"], gm["away_team"], sims_per_game, seed + i,
                     neutral=(gm["location"] == "Neutral"))
        chunks.append(r.stats)
    sim = sim_team_games(np.concatenate(chunks))
    act = actual_team_games(plays, sched, season)
    rows = [{"metric": k, "sim": sim[k], "actual": act[k], "rel_err": (sim[k] - act[k]) / act[k] if act[k] else 0.0}
            for k in METRICS]
    return pl.DataFrame(rows)


def format_table(df: pl.DataFrame) -> str:
    lines = [f"{'metric':<14}{'sim':>10}{'actual':>10}{'diff%':>8}"]
    for r in df.iter_rows(named=True):
        lines.append(f"{r['metric']:<14}{r['sim']:>10.3f}{r['actual']:>10.3f}{100 * r['rel_err']:>7.1f}%")
    return "\n".join(lines)
