"""Live forward predictions: the honest scoreboard.

predict-week  sims every unplayed game of a week and writes FROZEN, timestamped prediction files
              predictions/<season>/wk<NN>/<game_id>__<UTC stamp>__<config hash>.json (never overwritten).
grade         once games are final, scores the latest prediction made BEFORE kickoff for each game with the
              same metrics as the backtest (plus the closing line) and appends to predictions/scorecard.csv.
scorecard     cumulative live performance, overall and by config version.
weekly        refresh -> grade last week -> diagnose recent weeks -> (optional tune) -> predict next week.
reconstruct   after a data loss: re-sims a past week using only data from before it. Written to
              predictions/<season>/wk<NN>/reconstructed/, tagged provenance=reconstructed, graded into
              predictions/scorecard_reconstructed.csv. NEVER counted in the live scorecard (only genuinely
              pre-kickoff frozen predictions are) and never used by the signal trust gate.

Lock in, grade and weekly each end with an automatic backup of the data folder (userdata.auto_backup).
"""
from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

from .api import RunRequest, run_game, week_slate
from .config import Config
from .metrics import brier, crps_from_hist, log_loss, ml_to_prob

ET = ZoneInfo("America/New_York")


def pred_dir(cfg: Config, season: int, week: int) -> Path:
    return cfg.resolve_path("predictions") / str(season) / f"wk{week:02d}"


def kickoff_utc(game: dict) -> dt.datetime | None:
    try:
        t = game.get("gametime") or "13:00"
        local = dt.datetime.strptime(f"{game['gameday']} {t}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
        return local.astimezone(dt.timezone.utc)
    except Exception:
        return None


def predict_week(cfg: Config, season: int | None = None, week: int | None = None, sims: int | None = None,
                 verbose: bool = True, progress=None, seed: int | None = None, use_players: bool = True) -> list[Path]:
    season, week, games = week_slate(cfg, season, week)
    d = pred_dir(cfg, season, week)
    d.mkdir(parents=True, exist_ok=True)
    written = []
    now = dt.datetime.now(dt.timezone.utc)
    todo = [g for g in games if g.get("home_score") is None]
    for i, g in enumerate(todo):
        ko = kickoff_utc(g)
        if ko is not None and ko <= now:
            if verbose:
                print(f"  skip {g['game_id']}: already kicked off")
            continue
        req = RunRequest(home=g["home_team"], away=g["away_team"], season=season, week=week,
                         sims=int(sims or cfg.sim["n_sims"]), seed=int(seed if seed is not None else cfg.sim["seed"]),
                         use_players=use_players,
                         market_lines=True)
        summ = run_game(req, cfg, refresh=True)   # fresh sim; also refreshes the normal cache for This Week
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        summ["frozen"] = {"created_utc": now.isoformat(), "kickoff_utc": ko.isoformat() if ko else None,
                          "config_hash": cfg.hash, "game_id": g["game_id"]}
        path = d / f"{g['game_id']}__{stamp}__{cfg.hash}.json"
        if path.exists():
            raise FileExistsError(path)  # never overwrite a frozen prediction
        with open(path, "x", encoding="utf-8") as fh:
            json.dump(summ, fh, indent=1, default=str)
        written.append(path)
        if verbose:
            w = summ["win"]
            print(f"  {g['away_team']} @ {g['home_team']}: home win {100 * w['home']['p']:.1f}%  fair spread "
                  f"{summ['fair']['spread_home']:+.1f}  total {summ['fair']['total']:.1f} -> {path.name}", flush=True)
        if progress:
            progress((i + 1) / max(len(todo), 1))
    if written:
        from .userdata import auto_backup
        auto_backup(cfg, "lock-in")
    return written


SCORE_FIELDS = ["season", "week", "game_id", "home", "away", "home_score", "away_score", "config_hash", "pred_file",
                "created_utc", "p_home", "p_tie", "pred_margin", "pred_total", "crps_margin", "crps_total",
                "mkt_p_home", "mkt_margin", "mkt_total", "p_cover", "p_over", "home_win"]


def grade(cfg: Config, season: int, week: int, verbose: bool = True) -> list[dict]:
    from .data import load_schedules
    sched = load_schedules(cfg, force=True)
    g = sched.filter((pl.col("season") == season) & (pl.col("week") == week) & pl.col("home_score").is_not_null())
    d = pred_dir(cfg, season, week)
    card = cfg.resolve_path("predictions") / "scorecard.csv"
    done = set()
    if card.exists():
        with open(card, encoding="utf-8") as fh:
            done = {r["game_id"] for r in csv.DictReader(fh)}      # one graded row per game
    rows = []
    for gm in g.iter_rows(named=True):
        if gm["game_id"] in done:
            continue
        ko = kickoff_utc(gm)
        cands = []
        for f in sorted(d.glob(f"{gm['game_id']}__*.json")):
            p = json.loads(f.read_text())
            created = dt.datetime.fromisoformat(p["frozen"]["created_utc"])
            if ko is None or created < ko:
                cands.append((created, f, p))
        if not cands:
            if verbose:
                print(f"  {gm['game_id']}: no pre-kickoff prediction")
            continue
        created, f, p = max(cands, key=lambda x: x[0])
        rows.append(_score_row(season, week, gm, f, p))
    _append_card(card, rows)
    if verbose:
        print(f"graded {len(rows)} games for {season} week {week}")
    try:
        from .scanner import grade_signals
        n = grade_signals(cfg)
        if verbose and n:
            print(f"graded {n} market signals")
    except Exception as e:
        print(f"  signal grading skipped: {e}")
    from .userdata import auto_backup
    auto_backup(cfg, "grade")
    return rows


def _score_row(season: int, week: int, gm: dict, f: Path, p: dict) -> dict:
    y_m = gm["home_score"] - gm["away_score"]
    y_t = gm["home_score"] + gm["away_score"]
    spread = -gm["spread_line"] if gm.get("spread_line") is not None else None
    from .api import cover_from_hist
    row = {
        "season": season, "week": week, "game_id": gm["game_id"], "home": gm["home_team"], "away": gm["away_team"],
        "home_score": gm["home_score"], "away_score": gm["away_score"], "config_hash": p["frozen"]["config_hash"],
        "pred_file": f.name, "created_utc": p["frozen"]["created_utc"],
        "p_home": p["win"]["home"]["p"], "p_tie": p["win"]["tie"]["p"],
        "pred_margin": p["scores"]["margin"]["mean"], "pred_total": p["scores"]["total"]["mean"],
        "crps_margin": crps_from_hist(p["hist"]["margin"]["values"], p["hist"]["margin"]["counts"], y_m),
        "crps_total": crps_from_hist(p["hist"]["total"]["values"], p["hist"]["total"]["counts"], y_t),
        "mkt_p_home": ml_to_prob(gm.get("home_moneyline"), gm.get("away_moneyline")),
        "mkt_margin": gm.get("spread_line"), "mkt_total": gm.get("total_line"),
        "p_cover": cover_from_hist(p["hist"]["margin"], spread, "spread")["hit"] if spread is not None else None,
        "p_over": cover_from_hist(p["hist"]["total"], gm["total_line"], "total")["hit"] if gm.get("total_line") is not None else None,
        "home_win": 1.0 if y_m > 0 else (0.0 if y_m < 0 else 0.5),
    }
    return row


def _append_card(card: Path, rows: list[dict]) -> None:
    if not rows:
        return
    card.parent.mkdir(parents=True, exist_ok=True)
    from .userdata import file_lock
    with file_lock(card), open(card, "a", newline="", encoding="utf-8") as fh:
        new = fh.tell() == 0
        w = csv.DictWriter(fh, fieldnames=SCORE_FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)


def reconstruct_week(cfg: Config, season: int, week: int, sims: int | None = None, seed: int | None = None,
                     use_players: bool = True, verbose: bool = True, progress=None) -> list[Path]:
    """Re-create predictions for a past week after a data loss, using only play-by-play from before that week.
    Games that already have a genuine pre-kickoff prediction are skipped. The files are tagged
    provenance=reconstructed and kept apart: they show in reports but never count toward the live scorecard."""
    if week < 2:
        raise ValueError("week 1 can't be reconstructed (there is no earlier week of this season to stop at)")
    _, _, games = week_slate(cfg, season, week)
    d = pred_dir(cfg, season, week)
    rd = d / "reconstructed"
    rd.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    written = []
    for i, g in enumerate(games):
        ko = kickoff_utc(g)
        genuine = [f for f in d.glob(f"{g['game_id']}__*.json")
                   if ko is None or dt.datetime.fromisoformat(json.loads(f.read_text())["frozen"]["created_utc"]) < ko]
        if genuine or list(rd.glob(f"{g['game_id']}__*.json")):
            if verbose:
                print(f"  skip {g['game_id']}: already has a {'locked-in' if genuine else 'reconstructed'} prediction")
            continue
        req = RunRequest(home=g["home_team"], away=g["away_team"], season=season, week=week,
                         sims=int(sims or cfg.sim["n_sims"]), seed=int(seed if seed is not None else cfg.sim["seed"]),
                         through_week=week - 1, use_players=use_players, market_lines=True)
        summ = run_game(req, cfg, use_cache=False)
        summ["frozen"] = {"created_utc": now.isoformat(), "kickoff_utc": ko.isoformat() if ko else None,
                          "config_hash": cfg.hash, "game_id": g["game_id"], "provenance": "reconstructed",
                          "data_through_week": week - 1,
                          "note": "re-created after the fact from data before this week; not a pre-kickoff "
                                  "prediction — never counted in the live scorecard"}
        path = rd / f"{g['game_id']}__{stamp}__{cfg.hash}__reconstructed.json"
        with open(path, "x", encoding="utf-8") as fh:
            json.dump(summ, fh, indent=1, default=str)
        written.append(path)
        if verbose:
            print(f"  reconstructed {g['away_team']} @ {g['home_team']} -> {path.name}", flush=True)
        if progress:
            progress((i + 1) / max(len(games), 1))
    if written:
        grade_reconstructed(cfg, season, week, verbose=verbose)
        from .userdata import auto_backup
        auto_backup(cfg, "reconstruct")
    return written


def grade_reconstructed(cfg: Config, season: int, week: int, verbose: bool = True) -> list[dict]:
    """Score reconstructed predictions into predictions/scorecard_reconstructed.csv (kept apart from the real one)."""
    from .data import load_schedules
    sched = load_schedules(cfg)
    g = sched.filter((pl.col("season") == season) & (pl.col("week") == week) & pl.col("home_score").is_not_null())
    rd = pred_dir(cfg, season, week) / "reconstructed"
    card = cfg.resolve_path("predictions") / "scorecard_reconstructed.csv"
    done = set()
    if card.exists():
        with open(card, encoding="utf-8") as fh:
            done = {r["game_id"] for r in csv.DictReader(fh)}
    rows = []
    for gm in g.iter_rows(named=True):
        files = sorted(rd.glob(f"{gm['game_id']}__*.json"))
        if not files or gm["game_id"] in done:
            continue
        rows.append(_score_row(season, week, gm, files[-1], json.loads(files[-1].read_text())))
    _append_card(card, rows)
    if verbose and rows:
        print(f"graded {len(rows)} reconstructed games for {season} week {week} (not counted in the live scorecard)")
    return rows


def load_card(cfg: Config, name: str = "scorecard.csv") -> pl.DataFrame | None:
    """The graded record, one row per game (if a game was graded twice — e.g. after merging two installs — the later
    pre-kickoff prediction counts, the same rule grade() uses)."""
    card = cfg.resolve_path("predictions") / name
    if not card.exists():
        return None
    df = pl.read_csv(card, infer_schema_length=None)
    if df.height and "created_utc" in df.columns:
        df = df.sort("created_utc").unique(subset=["game_id"], keep="last", maintain_order=True)
    return df


def scorecard(cfg: Config, season: int | None = None) -> dict:
    df = load_card(cfg)
    if df is None:
        return {"games": 0}
    if season:
        df = df.filter(pl.col("season") == season)

    def block(x: pl.DataFrame) -> dict:
        if x.height == 0:
            return {"games": 0}
        y = x["home_win"].to_numpy().astype(float)
        p = (x["p_home"] + 0.5 * x["p_tie"]).to_numpy()
        out = {"games": x.height, "brier": brier(p, y), "log_loss": log_loss(p, y),
               "mae_margin": float(np.mean(np.abs((x["pred_margin"] - (x["home_score"] - x["away_score"])).to_numpy()))),
               "mae_total": float(np.mean(np.abs((x["pred_total"] - (x["home_score"] + x["away_score"])).to_numpy()))),
               "crps_margin": float(x["crps_margin"].mean()), "crps_total": float(x["crps_total"].mean())}
        mk = x.filter(pl.col("mkt_p_home").is_not_null())
        if mk.height:
            out["market_brier"] = brier(mk["mkt_p_home"].to_numpy(), mk["home_win"].to_numpy().astype(float))
            out["market_mae_margin"] = float(np.mean(np.abs((mk["mkt_margin"] - (mk["home_score"] - mk["away_score"])).to_numpy())))
        return out
    return {"overall": block(df), "by_config": {h: block(df.filter(pl.col("config_hash") == h))
                                                 for h in df["config_hash"].unique().to_list()},
            "by_week": {int(w): block(df.filter(pl.col("week") == w)) for w in sorted(df["week"].unique().to_list())}}


def format_scorecard(sc: dict) -> str:
    if not sc.get("overall"):
        return "no graded live predictions yet"
    L = []
    def line(name, b):
        if not b.get("games"):
            return f"{name:<16} -"
        return (f"{name:<16} n={b['games']:<4} brier {b['brier']:.4f} (mkt {b.get('market_brier', float('nan')):.4f})  "
                f"logloss {b['log_loss']:.4f}  MAE margin {b['mae_margin']:.2f} (mkt {b.get('market_mae_margin', float('nan')):.2f})  "
                f"MAE total {b['mae_total']:.2f}  CRPS m/t {b['crps_margin']:.2f}/{b['crps_total']:.2f}")
    L.append(line("overall", sc["overall"]))
    for h, b in sc["by_config"].items():
        L.append(line(f"config {h}", b))
    for w, b in sc["by_week"].items():
        L.append(line(f"week {w}", b))
    return "\n".join(L)


def weekly(cfg: Config, tune: bool = False, verbose: bool = True) -> None:
    from .backtest import run_backtest
    from .data import current_season, load_schedules, refresh_all
    from .diagnose import diagnose, write_report
    season = current_season()
    print("1/5 refresh data", flush=True)
    refresh_all(cfg, [season])
    try:
        from .availability import refresh_availability
        refresh_availability(cfg, season)
    except Exception as e:
        print(f"  availability refresh failed: {e}")
    sched = load_schedules(cfg).filter(pl.col("season") == season)
    played = sched.filter(pl.col("home_score").is_not_null())
    last_week = int(played["week"].max()) if played.height else None
    if last_week:
        print(f"2/5 grade week {last_week}", flush=True)
        grade(cfg, season, last_week, verbose)
        print(format_scorecard(scorecard(cfg, season)))
        first = max(1, last_week - 3)
        print(f"3/5 diagnose weeks {first}-{last_week}", flush=True)
        g, ex = run_backtest(cfg, season, list(range(first, last_week + 1)), sims=int(cfg.tune["sims_per_game"]), verbose=False)
        if g.height:
            path = write_report(diagnose(g, ex["components"]), cfg.resolve_path("diagnose") / f"{season}_wk{first}-{last_week}",
                                f"Diagnose {season} weeks {first}-{last_week}")
            print(f"  diagnose report: {path}")
    if tune:
        from .tune import run_tune
        print("4/5 tune", flush=True)
        run_tune(cfg, [season - 3, season - 2, season - 1], season, int(cfg.tune["trials"]))
    else:
        print("4/5 tune skipped (pass --tune)")
    print("5/5 predict next week", flush=True)
    predict_week(cfg, season, None, verbose=verbose)
    from .userdata import auto_backup
    auto_backup(cfg, "weekly")
