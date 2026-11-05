"""Walk-forward backtest: every game is simulated with a model built only from data before its week.

For week w of season S the model uses S through week w-1 plus prior seasons (no lookahead). Starting QBs
are the QBs who actually started (the starter is public before kickoff); other injuries are not replayed.
Outputs per game: win probability, margin/total distribution scores (CRPS), 80% interval coverage,
closing-line comparison, and per-team component expectations vs actuals for `diagnose`.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import polars as pl

from . import data as D
from .config import Config
from .engine import S
from .metrics import crps_samples, ml_to_prob, summarize_games

# component -> (numerator key, denominator key); den None = per team-game count
COMPONENTS = {
    "points": ("pts", None), "pts_q1": ("q1", None), "pts_q2": ("q2", None), "pts_q3": ("q3", None), "pts_q4": ("q4", None),
    "plays": ("plays", None), "pass_rate": ("dropbacks", "plays"), "ypa": ("pass_gross", "att"),
    "ypc": ("rush_yds", "rush_n"), "sack_rate": ("sacks", "dropbacks"), "int_rate": ("ints", "att"),
    "fumbles_lost": ("fum", None), "third_down_pct": ("third_conv", "third_att"), "fourth_down_att": ("fourth_go", None),
    "rz_trips": ("rz_trips", None), "rz_td_pct": ("rz_tds", "rz_trips"), "fg_att": ("fga", None),
    "fg_pct": ("fgm", "fga"), "punts": ("punts", None), "kickoff_start_yl": ("ko_yl", "ko_n"),
    "punt_start_yl": ("pu_yl", "pu_n"), "penalties": ("pens", None), "top_min": ("top_min", None),
    "turnovers": ("turnovers", None),
}
COUNT_KEYS = sorted({k for v in COMPONENTS.values() for k in v if k})


def sim_counts(stats: np.ndarray, t: int) -> dict:
    x = stats[:, t]
    c = lambda k: float(x[:, S[k]].mean())  # noqa: E731
    att = c("pass_att") - c("spikes")
    return {
        "pts": c("pts"), "q1": c("q1"), "q2": c("q2"), "q3": c("q3"), "q4": c("q4"),
        "plays": c("pass_att") + c("sacks") + c("rush_att"), "dropbacks": c("pass_att") + c("sacks"),
        "att": att, "pass_gross": c("pass_yds_gross"), "rush_yds": c("rush_yds") + c("kneels"),
        "rush_n": c("rush_att") - c("kneels"), "sacks": c("sacks"), "ints": c("ints"), "fum": c("fum_lost"),
        "third_conv": c("third_conv"), "third_att": c("third_att"), "fourth_go": c("fourth_go"),
        "rz_trips": c("rz_trips"), "rz_tds": c("rz_tds"), "fga": c("fga"), "fgm": c("fgm"), "punts": c("punts"),
        "ko_yl": c("ko_start_yl"), "ko_n": c("ko_starts"), "pu_yl": c("punt_start_yl"), "pu_n": c("punt_starts"),
        "pens": c("penalties"), "top_min": c("top") / 60.0, "turnovers": c("ints") + c("fum_lost"),
    }


def actual_counts(plays: pl.DataFrame, game_ids: list[str]) -> dict:
    """{(game_id, team): counts} from real play-by-play, same definitions as the sim."""
    p = plays.filter(pl.col("game_id").is_in(game_ids) & pl.col("posteam").is_not_null())
    f = lambda c: pl.col(c).fill_null(0)  # noqa: E731
    p = p.with_columns(
        is_att=(((f("complete_pass") == 1) | (f("incomplete_pass") == 1) | (f("interception") == 1)) & (f("sack") == 0)
                & (pl.col("play_type") == "pass") & (f("two_point_attempt") == 0)),
        is_sack=(f("sack") == 1) & (pl.col("play_type") == "pass"),
        is_rush=(pl.col("play_type") == "run") & (f("two_point_attempt") == 0),
        is_kneel=(pl.col("play_type") == "qb_kneel"), is_spike=(pl.col("play_type") == "qb_spike"),
        scrim=pl.col("play_type").is_in(["pass", "run"]) & pl.col("down").is_not_null(),
        el=pl.when(pl.col("elapsed").is_not_null()).then(pl.col("elapsed")).otherwise(pl.col("quarter_seconds_remaining")).fill_null(0),
    )
    g = p.group_by(["game_id", "posteam"]).agg(
        att=pl.col("is_att").sum(), sacks=pl.col("is_sack").sum(), rush_n=pl.col("is_rush").sum(),
        kneels=pl.col("is_kneel").sum(), spikes=pl.col("is_spike").sum(),
        pass_gross=pl.when(pl.col("is_att")).then(f("yards_gained")).otherwise(0).sum(),
        rush_yds=pl.when(pl.col("is_rush")).then(f("yards_gained")).otherwise(0).sum(),
        ints=((f("interception") == 1) & pl.col("is_att")).sum(),
        fum=((f("fumble_lost") == 1) & pl.col("play_type").is_in(["pass", "run"])).sum(),
        third_att=(pl.col("scrim") & (pl.col("down") == 3)).sum(),
        third_conv=(pl.col("scrim") & (pl.col("down") == 3) & ((f("first_down") == 1) | pl.col("td_off"))).sum(),
        fourth_go=(pl.col("scrim") & (pl.col("down") == 4)).sum(),
        fga=(pl.col("play_type") == "field_goal").sum(),
        fgm=((pl.col("play_type") == "field_goal") & (pl.col("field_goal_result") == "made")).sum(),
        punts=(pl.col("play_type") == "punt").sum(),
        top=pl.col("el").sum(),
    )
    drives = (p.filter(pl.col("play_type").is_in(["pass", "run", "punt", "field_goal", "qb_kneel", "qb_spike", "no_play"])
                       & pl.col("down").is_not_null())
              .group_by(["game_id", "posteam", "fixed_drive"]).agg(rz=(pl.col("yardline_100") <= 20).any(), td=pl.col("td_off").any())
              .group_by(["game_id", "posteam"]).agg(rz_trips=pl.col("rz").sum(), rz_tds=(pl.col("rz") & pl.col("td")).sum()))
    pens = (p.filter((f("penalty") == 1) & pl.col("play_type").is_in(["pass", "run", "no_play"]) & pl.col("down").is_not_null())
            .group_by(["game_id", "penalty_team"]).agg(pens=pl.len()).rename({"penalty_team": "posteam"}))
    ko = (p.filter((pl.col("play_type") == "kickoff") & ~pl.col("kick_retained") & pl.col("rec_yl").is_not_null()
                   & ~pl.col("td_off") & ~pl.col("td_def"))
          .group_by(["game_id", "posteam"]).agg(ko_yl=pl.col("rec_yl").sum(), ko_n=pl.len()))
    pu = (p.filter((pl.col("play_type") == "punt") & ~pl.col("kick_retained") & pl.col("rec_yl").is_not_null())
          .group_by(["game_id", "defteam"]).agg(pu_yl=pl.col("rec_yl").sum(), pu_n=pl.len()).rename({"defteam": "posteam"}))
    # points by quarter from the running score
    sc = (plays.filter(pl.col("game_id").is_in(game_ids)).group_by(["game_id", "qtr"])
          .agg(pl.col("total_home_score").max().alias("h"), pl.col("total_away_score").max().alias("a"),
               pl.col("home_team").first(), pl.col("away_team").first()).sort(["game_id", "qtr"]))
    sc = sc.with_columns(dh=pl.col("h") - pl.col("h").shift(1).over("game_id").fill_null(0),
                         da=pl.col("a") - pl.col("a").shift(1).over("game_id").fill_null(0))
    out: dict = {}
    for r in g.iter_rows(named=True):
        key = (r["game_id"], r["posteam"])
        att = r["att"]
        out[key] = {
            "plays": r["att"] + r["spikes"] + r["sacks"] + r["rush_n"] + r["kneels"], "dropbacks": r["att"] + r["spikes"] + r["sacks"],
            "att": att, "pass_gross": r["pass_gross"], "rush_yds": r["rush_yds"], "rush_n": r["rush_n"], "sacks": r["sacks"],
            "ints": r["ints"], "fum": r["fum"], "third_conv": r["third_conv"], "third_att": r["third_att"],
            "fourth_go": r["fourth_go"], "fga": r["fga"], "fgm": r["fgm"], "punts": r["punts"], "top_min": r["top"] / 60.0,
            "turnovers": r["ints"] + r["fum"], "rz_trips": 0, "rz_tds": 0, "pens": 0, "ko_yl": 0, "ko_n": 0,
            "pu_yl": 0, "pu_n": 0, "q1": 0, "q2": 0, "q3": 0, "q4": 0, "pts": 0,
        }
    for frame, cols in ((drives, ["rz_trips", "rz_tds"]), (pens, ["pens"]), (ko, ["ko_yl", "ko_n"]), (pu, ["pu_yl", "pu_n"])):
        for r in frame.iter_rows(named=True):
            k = (r["game_id"], r["posteam"])
            if k in out:
                for c in cols:
                    out[k][c] = float(r[c])
    for r in sc.iter_rows(named=True):
        q = int(r["qtr"])
        for team, d in ((r["home_team"], r["dh"]), (r["away_team"], r["da"])):
            k = (r["game_id"], team)
            if k in out and d is not None:
                if q <= 4:
                    out[k][f"q{q}"] += float(d)
                out[k]["pts"] += float(d)
    return out


# --------------------------------------------------------------------------------------
# worker
# --------------------------------------------------------------------------------------
_W: dict = {}


def _plays_for(season: int, cfg: Config) -> pl.DataFrame:
    key = ("plays", season, cfg.hash)
    if key not in _W:
        from .model import load_prepared
        _W[key] = load_prepared(season, cfg)
    return _W[key]


def game_seed(seed: int, game_id: str) -> int:
    return (seed * 1_000_003 + zlib.crc32(game_id.encode())) % (2 ** 31)


def sim_week(job: dict) -> list[dict]:
    """Simulate every game in one (season, week) with a model through week-1."""
    from .engine import simulate
    from .model import build_model

    cfg = Config(job["cfg_raw"])
    season, week = job["season"], job["week"]
    plays = _plays_for(season, cfg)
    mkey = ("model", season, week, cfg.hash)
    model = _W.get(mkey)
    if model is None:
        model = build_model(cfg, season, week - 1, plays)
        _W.clear() if len(_W) > 6 else None
        _W[("plays", season, cfg.hash)] = plays
        _W[mkey] = model
    out = []
    for gm in job["games"]:
        h, a = gm["home_team"], gm["away_team"]
        if h not in model.teams or a not in model.teams:
            continue
        starters = (gm.get("home_qb"), gm.get("away_qb"))
        r = simulate(model, h, a, job["sims"], game_seed(job["seed"], gm["game_id"]),
                     neutral=gm.get("location") == "Neutral", playoff=gm.get("game_type") != "REG", starters=starters)
        hp, ap = r.col("pts", 0), r.col("pts", 1)
        mar, tot = hp - ap, hp + ap
        y_m, y_t = gm["home_score"] - gm["away_score"], gm["home_score"] + gm["away_score"]
        q = lambda x, p: float(np.percentile(x, p))  # noqa: E731
        spread = -gm["spread_line"] if gm.get("spread_line") is not None else None
        row = {
            "game_id": gm["game_id"], "season": season, "week": week, "home": h, "away": a,
            "home_score": gm["home_score"], "away_score": gm["away_score"], "margin": y_m, "total": y_t,
            "home_win": 1.0 if y_m > 0 else (0.0 if y_m < 0 else 0.5),
            "p_home": float(np.mean(mar > 0)), "p_tie": float(np.mean(mar == 0)),
            "pred_margin": float(mar.mean()), "pred_total": float(tot.mean()),
            "pred_home": float(hp.mean()), "pred_away": float(ap.mean()),
            "crps_margin": crps_samples(mar, y_m), "crps_total": crps_samples(tot, y_t),
            "crps_home": crps_samples(hp, gm["home_score"]), "crps_away": crps_samples(ap, gm["away_score"]),
            "cov_margin": float(q(mar, 10) <= y_m <= q(mar, 90)), "cov_total": float(q(tot, 10) <= y_t <= q(tot, 90)),
            "cov_home": float(q(hp, 10) <= gm["home_score"] <= q(hp, 90)), "cov_away": float(q(ap, 10) <= gm["away_score"] <= q(ap, 90)),
            "mkt_spread": spread, "mkt_margin": gm.get("spread_line"), "mkt_total": gm.get("total_line"),
            "mkt_p_home": ml_to_prob(gm.get("home_moneyline"), gm.get("away_moneyline")),
            "p_cover": float(np.mean(mar + spread > 0)) if spread is not None else None,
            "p_over": float(np.mean(tot > gm["total_line"])) if gm.get("total_line") is not None else None,
            "roof": gm.get("roof"), "div_game": gm.get("div_game"), "gametime": gm.get("gametime"),
            "weekday": gm.get("weekday"), "home_rest": gm.get("home_rest"), "away_rest": gm.get("away_rest"),
            "location": gm.get("location"), "home_qb": gm.get("home_qb"), "away_qb": gm.get("away_qb"),
            "home_qb_change": gm.get("home_qb_change"), "away_qb_change": gm.get("away_qb_change"),
            "sim_home": sim_counts(r.stats, 0), "sim_away": sim_counts(r.stats, 1),
        }
        out.append(row)
    return out


# --------------------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------------------

def game_list(cfg: Config, season: int, weeks: list[int], plays: pl.DataFrame, include_post: bool = False) -> list[dict]:
    sched = D.load_schedules(cfg)
    g = sched.filter((pl.col("season") == season) & pl.col("week").is_in(weeks) & pl.col("home_score").is_not_null())
    if not include_post:
        g = g.filter(pl.col("game_type") == "REG")
    # actual starting QBs (main passer) and whether each team changed QB from its previous game
    qb = (plays.filter((pl.col("season") == season) & pl.col("game_qb").is_not_null())
          .group_by(["game_id", "posteam"]).agg(pl.col("game_qb").first(), pl.col("week").first()))
    qb_map = {(r["game_id"], r["posteam"]): r["game_qb"] for r in qb.iter_rows(named=True)}
    prev = {}
    for r in qb.sort("week").iter_rows(named=True):
        prev.setdefault(r["posteam"], []).append((r["week"], r["game_qb"]))

    def changed(team, week, cur):
        hist = [q for w, q in prev.get(team, []) if w < week]
        return bool(hist) and cur is not None and hist[-1] != cur

    games = []
    for r in g.iter_rows(named=True):
        r = dict(r)
        r["home_qb"] = qb_map.get((r["game_id"], r["home_team"]))
        r["away_qb"] = qb_map.get((r["game_id"], r["away_team"]))
        r["home_qb_change"] = changed(r["home_team"], r["week"], r["home_qb"])
        r["away_qb_change"] = changed(r["away_team"], r["week"], r["away_qb"])
        games.append(r)
    return games


def parse_weeks(s: str) -> list[int]:
    out = []
    for part in s.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def run_backtest(cfg: Config, season: int, weeks: list[int], sims: int = 1000, seed: int = 2024,
                 workers: int | None = None, games_filter=None, verbose: bool = True,
                 include_post: bool = False) -> tuple[pl.DataFrame, dict]:
    from .model import load_prepared
    t0 = time.time()
    plays = load_prepared(season, cfg, verbose=verbose)
    games = game_list(cfg, season, weeks, plays, include_post)
    if games_filter is not None:
        games = [g for g in games if games_filter(g)]
    by_week: dict[int, list] = {}
    for gm in games:
        by_week.setdefault(gm["week"], []).append(gm)
    jobs = [{"cfg_raw": cfg.raw, "season": season, "week": w, "games": gs, "sims": sims, "seed": seed}
            for w, gs in sorted(by_week.items())]
    workers = workers or max(1, min(len(jobs), (os.cpu_count() or 2)))
    rows: list[dict] = []
    if workers <= 1 or len(jobs) <= 1:
        _W[("plays", season, cfg.hash)] = plays
        for j in jobs:
            rows += sim_week(j)
            if verbose:
                print(f"  week {j['week']}: {len(j['games'])} games ({time.time() - t0:.0f}s)", flush=True)
    else:
        import multiprocessing as mp
        # spawn (not fork): forking after polars has started its thread pool can deadlock; spawn is the
        # Windows default anyway.
        with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn")) as ex:
            for j, res in zip(jobs, ex.map(sim_week, jobs)):
                rows += res
                if verbose:
                    print(f"  week {j['week']}: {len(j['games'])} games ({time.time() - t0:.0f}s)", flush=True)
    act = actual_counts(plays, [r["game_id"] for r in rows])
    comp_rows = []
    for r in rows:
        for side, team in (("home", r["home"]), ("away", r["away"])):
            a = act.get((r["game_id"], team))
            if a is None:
                continue
            sim = r[f"sim_{side}"]
            comp_rows.append({"game_id": r["game_id"], "season": season, "week": r["week"], "team": team, "side": side,
                              **{f"sim_{k}": sim[k] for k in COUNT_KEYS}, **{f"act_{k}": float(a[k]) for k in COUNT_KEYS}})
    gdf = pl.DataFrame([{k: v for k, v in r.items() if not k.startswith("sim_")} for r in rows], infer_schema_length=None)
    cdf = pl.DataFrame(comp_rows)
    metrics = summarize_games(gdf) if gdf.height else {}
    metrics["runtime_seconds"] = round(time.time() - t0, 1)
    metrics["sims_per_game"] = sims
    return gdf, {"metrics": metrics, "components": cdf}


def save_backtest(cfg: Config, label: str, gdf: pl.DataFrame, extra: dict, out_dir: str | None = None) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    d = cfg.resolve_path(out_dir or cfg.backtest["out_dir"]) / f"{label}_{cfg.hash}_{stamp}"
    d.mkdir(parents=True, exist_ok=True)
    gdf.write_csv(d / "games.csv")
    if extra.get("components") is not None and extra["components"].height:
        extra["components"].write_csv(d / "components.csv")
    with open(d / "metrics.json", "w", encoding="utf-8") as fh:
        json.dump({"label": label, "config_hash": cfg.hash, "config": cfg.raw, **extra["metrics"]}, fh, indent=2, default=str)
    return d


def format_metrics(m: dict) -> str:
    L = [f"games {m.get('games')}  sims/game {m.get('sims_per_game')}  runtime {m.get('runtime_seconds')}s"]
    L.append(f"{'metric':<26}{'sim':>10}{'market':>10}")
    mk = m.get("market", {})
    for k in ("brier", "log_loss", "mae_margin", "mae_total"):
        L.append(f"{k:<26}{m[k]:>10.4f}{mk.get(k, float('nan')):>10.4f}")
    for k in ("crps_margin", "crps_total", "crps_points", "bias_margin", "bias_total", "corr_margin_vs_spread",
              "corr_total_vs_line", "coverage_80_margin", "coverage_80_total", "coverage_80_points",
              "sd_pred_margin", "sd_mkt_margin", "ats_pick_rate"):
        if k in m:
            L.append(f"{k:<26}{m[k]:>10.4f}")
    L.append("calibration (pred -> actual):")
    for c in m.get("calibration", []):
        L.append(f"  {c['bin']:<9} n={c['n']:<4} pred {c['pred']:.3f}  actual {c['actual']:.3f}")
    return "\n".join(L)
