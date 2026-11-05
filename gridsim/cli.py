"""gridsim command-line interface."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import polars as pl


def _season_default() -> int:
    from .data import current_season
    return current_season()


def _schedule_game(cfg, season: int, home: str, away: str, week: int | None = None):
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


def cmd_run(args) -> int:
    from .config import load_config
    from .engine import simulate
    from .model import build_model
    from .report import console, summarize, to_json

    cfg = load_config(args.config)
    season = args.season or _season_default()
    home, away = args.home.upper(), args.away.upper()
    t0 = time.time()
    game = _schedule_game(cfg, season, home, away, args.week)
    neutral = args.neutral or bool(game and game.get("location") == "Neutral")
    banners = []
    if neutral and not args.neutral:
        banners.append("neutral site detected from the schedule")
    spread, total = args.spread, args.total
    market = None
    if game and game.get("spread_line") is not None:
        market = {"spread_home": -float(game["spread_line"]), "total": game.get("total_line"), "game_id": game["game_id"]}
        if spread is None and args.market_lines:
            spread = market["spread_home"]
        if total is None and args.market_lines and market["total"] is not None:
            total = float(market["total"])

    model = build_model(cfg, season, args.through_week, verbose=True)
    players = None
    avail = None
    avail_info = None
    if not args.no_availability:
        from .availability import NameMatchError, build_availability
        try:
            avail = build_availability(cfg, season, home, away, model, week=(game or {}).get("week") or args.week,
                                       outs=args.out, ins=args.inn, overrides_path=args.overrides,
                                       game_date=(game or {}).get("gameday"), verbose=True)
        except NameMatchError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        avail_info = avail.describe()
        banners += avail.banners
    if not args.no_players:
        try:
            from .players import build_player_model
            players = build_player_model(cfg, model, season, args.through_week, home, away, avail)
        except ImportError:
            players = None
    n = args.sims or int(cfg.sim["n_sims"])
    seed = args.seed if args.seed is not None else int(cfg.sim["seed"])
    res = simulate(model, home, away, n, seed, neutral=neutral, playoff=args.playoff, players=players,
                   availability=avail)
    extra = {"banners": banners}
    if market:
        extra["market"] = market
    if avail_info:
        extra["availability"] = avail_info
    if args.prop and players is not None:
        extra["props"] = players.prop_table(res, args.prop)
    summ = summarize(res, spread, total, model.meta, cfg.hash, extra)
    summ["meta"]["total_seconds"] = round(time.time() - t0, 2)
    print(console(summ))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        to_json(summ, args.json)
        print(f"\nJSON written to {args.json}")
    if args.report:
        from .html_report import write_html
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        write_html(summ, args.report)
        print(f"HTML report written to {args.report}")
    return 0


def cmd_refresh(args) -> int:
    from .config import load_config
    from .data import refresh_all
    cfg = load_config(args.config)
    seasons = None
    if args.seasons:
        a, _, b = args.seasons.partition("-")
        seasons = list(range(int(a), int(b or a) + 1))
    refresh_all(cfg, seasons)
    try:
        from .availability import refresh_availability
        refresh_availability(cfg)
    except ImportError:
        pass
    print("data refreshed")
    return 0


def cmd_sanity(args) -> int:
    from .config import load_config
    from .sanity import format_table, league_sanity
    cfg = load_config(args.config)
    df = league_sanity(cfg, args.season, args.sims, verbose=True)
    print(format_table(df))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gridsim", description="NFL single-game Monte Carlo simulator")
    p.add_argument("--config", default=None, help="path to config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="simulate one matchup")
    r.add_argument("--home", required=True)
    r.add_argument("--away", required=True)
    r.add_argument("--sims", type=int, default=None)
    r.add_argument("--season", type=int, default=None)
    r.add_argument("--week", type=int, default=None, help="schedule week (for neutral-site / market lookup)")
    r.add_argument("--seed", type=int, default=None)
    r.add_argument("--spread", type=float, default=None, help="home perspective, negative = home favored")
    r.add_argument("--total", type=float, default=None)
    r.add_argument("--market-lines", action="store_true", help="use nflverse schedule spread/total if none given")
    r.add_argument("--neutral", action="store_true")
    r.add_argument("--playoff", action="store_true")
    r.add_argument("--through-week", type=int, default=None, help="use target-season data only through this week")
    r.add_argument("--report", default=None, help="write HTML report here")
    r.add_argument("--json", default=None, help="write JSON summary here")
    r.add_argument("--out", action="append", default=[], help='player ruled out, e.g. --out "J.Jefferson" (repeatable)')
    r.add_argument("--in", dest="inn", action="append", default=[], help="player forced active (repeatable)")
    r.add_argument("--overrides", default=None, help="overrides.yaml path (default overrides/<season>_wk<week>.yaml)")
    r.add_argument("--prop", action="append", default=[], help='"Player Name:rec_yds:64.5" (repeatable)')
    r.add_argument("--no-players", action="store_true", help="skip player attribution (faster)")
    r.add_argument("--no-availability", action="store_true", help="ignore rosters/injuries (use last game's QBs)")
    r.set_defaults(func=cmd_run)

    rf = sub.add_parser("refresh-data", help="re-download nflverse data into the local cache")
    rf.add_argument("--seasons", default=None, help="e.g. 2022-2026")
    rf.set_defaults(func=cmd_refresh)

    sn = sub.add_parser("sanity", help="league-average sanity table (sim vs actual) for a season")
    sn.add_argument("--season", type=int, default=2025)
    sn.add_argument("--sims", type=int, default=20, help="sims per game")
    sn.set_defaults(func=cmd_sanity)

    try:
        from . import cli_ext
        cli_ext.register(sub)
    except ImportError:
        pass
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
