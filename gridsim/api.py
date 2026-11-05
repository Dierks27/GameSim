"""Front-end-neutral service layer. The CLI and the GUI both call these functions, so the same inputs and
seed always give identical numbers in both. No modeling logic lives in the front ends.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from .config import Config, load_config

_MODEL_CACHE: dict = {}


@dataclass
class RunRequest:
    home: str
    away: str
    season: int | None = None
    week: int | None = None
    sims: int | None = None
    seed: int | None = None
    spread: float | None = None
    total: float | None = None
    market_lines: bool = False
    neutral: bool = False
    playoff: bool = False
    through_week: int | None = None
    outs: list[str] = field(default_factory=list)
    ins: list[str] = field(default_factory=list)
    overrides_path: str | None = None
    props: list[str] = field(default_factory=list)
    use_availability: bool = True
    use_players: bool = True
    state: str | None = None          # live game state, e.g. "Q3 8:42 MIN 17 GB 13 MIN ball 2nd&6 at GB 34"
    combos: list[str] = field(default_factory=list)

    def key(self, cfg_hash: str) -> str:
        blob = json.dumps({**self.__dict__, "cfg": cfg_hash}, sort_keys=True, default=str)
        return hashlib.sha1(blob.encode()).hexdigest()[:16]


def get_model(cfg: Config, season: int, through_week: int | None, verbose: bool = False):
    from .model import build_model, load_prepared
    key = (cfg.hash, season, through_week)
    m = _MODEL_CACHE.get(key)
    if m is None:
        plays_key = ("plays", season)
        plays = _MODEL_CACHE.get(plays_key)
        if plays is None:
            plays = load_prepared(season, cfg, verbose=verbose)
            _MODEL_CACHE[plays_key] = plays
        m = build_model(cfg, season, through_week, plays, verbose=verbose)
        _MODEL_CACHE[key] = m
    return m


def schedule_game(cfg: Config, season: int, home: str, away: str, week: int | None = None) -> dict | None:
    from .data import load_schedules
    try:
        s = load_schedules(cfg)
    except Exception:
        return None
    g = s.filter((pl.col("season") == season) & (pl.col("home_team") == home) & (pl.col("away_team") == away))
    if week is not None:
        g = g.filter(pl.col("week") == week)
    if g.height == 0:
        return None
    unplayed = g.filter(pl.col("home_score").is_null())
    return (unplayed if unplayed.height else g).sort("week").row(0, named=True)


def week_slate(cfg: Config, season: int | None = None, week: int | None = None) -> tuple[int, int, list[dict]]:
    """Games of a week (default: the first week with an unplayed game)."""
    from .data import current_season, load_schedules
    season = season or current_season()
    s = load_schedules(cfg).filter((pl.col("season") == season))
    if week is None:
        un = s.filter(pl.col("home_score").is_null())
        week = int(un["week"].min()) if un.height else int(s["week"].max())
    g = s.filter(pl.col("week") == week).sort(["gameday", "gametime"])
    return season, week, list(g.iter_rows(named=True))


def cache_path(req: RunRequest, cfg: Config) -> Path:
    """Disk cache location for a request. The key includes the config hash and the contents of the week's
    overrides file, so editing overrides (or the config) never returns a stale result."""
    from .availability import default_overrides_path
    from .data import current_season
    season = req.season or current_season()
    week = req.week
    if week is None:
        g = schedule_game(cfg, season, req.home.upper(), req.away.upper())
        week = (g or {}).get("week")
    ovp = Path(req.overrides_path) if req.overrides_path else default_overrides_path(cfg, season, week)
    digest = hashlib.sha1(ovp.read_bytes()).hexdigest()[:8] if ovp.exists() else "none"
    return cfg.resolve_path("out/cache") / f"{req.key(cfg.hash + digest)}.json"


def cached_summary(req: RunRequest, cfg: Config) -> dict | None:
    p = cache_path(req, cfg)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return None


def run_game(req: RunRequest, cfg: Config | None = None, progress=None, verbose: bool = False,
             use_cache: bool = True, refresh: bool = False) -> dict:
    """use_cache: read and write the disk cache. refresh: skip reading it but still write the new result."""
    """Simulate one matchup and return the summary dict (see report.summarize)."""
    from .availability import build_availability
    from .data import current_season
    from .engine import simulate
    from .report import summarize

    cfg = cfg or load_config()
    t0 = time.time()
    season = req.season or current_season()
    home, away = req.home.upper(), req.away.upper()
    cpath = cache_path(req, cfg)
    if use_cache and not refresh and cpath.exists() and not req.outs and not req.ins and req.season is not None:
        return json.loads(cpath.read_text())

    game = schedule_game(cfg, season, home, away, req.week)
    neutral = req.neutral or bool(game and game.get("location") == "Neutral")
    banners = []
    if neutral and not req.neutral:
        banners.append("neutral site detected from the schedule")
    spread, total = req.spread, req.total
    market = None
    if game and game.get("spread_line") is not None:
        market = {"spread_home": -float(game["spread_line"]), "total": game.get("total_line"), "game_id": game["game_id"],
                  "home_ml": game.get("home_moneyline"), "away_ml": game.get("away_moneyline")}
        if spread is None and req.market_lines:
            spread = market["spread_home"]
        if total is None and req.market_lines and market["total"] is not None:
            total = float(market["total"])

    model = get_model(cfg, season, req.through_week, verbose=verbose)
    avail = None
    avail_info = None
    if req.use_availability:
        avail = build_availability(cfg, season, home, away, model, week=(game or {}).get("week") or req.week,
                                   outs=req.outs, ins=req.ins, overrides_path=req.overrides_path,
                                   game_date=(game or {}).get("gameday"), verbose=verbose)
        avail_info = avail.describe()
        banners += avail.banners
    players = None
    if req.use_players:
        try:
            from .players import build_player_model
            players = build_player_model(cfg, model, season, req.through_week, home, away, avail)
        except ImportError:
            players = None
    n = req.sims or int(cfg.sim["n_sims"])
    seed = req.seed if req.seed is not None else int(cfg.sim["seed"])
    start_state = None
    if req.state:
        from .state import parse_state
        start_state = parse_state(req.state, home, away)
        banners.append(f"live state: {req.state} (box-score stats count from this point; scores are final scores)")
    res = simulate(model, home, away, n, seed, neutral=neutral, playoff=req.playoff, players=players,
                   availability=avail, progress=progress, start_state=start_state)
    extra = {"banners": banners, "game": {k: game.get(k) for k in ("game_id", "gameday", "gametime", "week", "roof",
                                                                  "stadium", "location")} if game else None}
    if market:
        extra["market"] = market
    if avail_info:
        extra["availability"] = avail_info
    if req.props and players is not None:
        extra["props"] = players.prop_table(res, req.props)
    if req.combos:
        from .combos import evaluate
        extra["combos"] = [evaluate(c, res, players) for c in req.combos]
    summ = summarize(res, spread, total, model.meta, cfg.hash, extra)
    summ["meta"]["total_seconds"] = round(time.time() - t0, 2)
    summ["request"] = {**req.__dict__, "season": season}
    if use_cache or refresh:
        cpath.parent.mkdir(parents=True, exist_ok=True)
        from .report import to_json
        to_json(summ, cpath)
        summ = json.loads(cpath.read_text())
    return summ


def cover_from_hist(hist: dict, line: float, kind: str) -> dict:
    """Exact hit/push/miss for any line from a stored margin or total histogram."""
    vals, counts = hist["values"], hist["counts"]
    n = sum(counts)
    hit = push = 0
    for v, c in zip(vals, counts):
        x = v + line if kind == "spread" else v - line
        if x > 0:
            hit += c
        elif x == 0:
            push += c
    from .report import moe
    p = hit / n if n else 0.0
    return {"line": line, "hit": p, "push": push / n if n else 0.0, "miss": 1 - p - (push / n if n else 0.0), "moe": moe(p, n)}
