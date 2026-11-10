"""Phase 3+ CLI subcommands: backtest, diagnose, tune, predict-week, grade, scorecard, weekly."""
from __future__ import annotations

import json
from pathlib import Path


def _seasons(s: str) -> list[int]:
    a, _, b = s.partition("-")
    return list(range(int(a), int(b or a) + 1))


def cmd_backtest(args) -> int:
    from .backtest import format_metrics, parse_weeks, run_backtest, save_backtest
    from .config import load_config
    cfg = load_config(args.config)
    g, ex = run_backtest(cfg, args.season, parse_weeks(args.weeks), sims=args.sims, seed=args.seed,
                         workers=args.workers, include_post=args.include_post)
    print(format_metrics(ex["metrics"]))
    d = save_backtest(cfg, f"{args.season}_w{args.weeks}", g, ex)
    print(f"saved to {d}")
    return 0


def cmd_diagnose(args) -> int:
    import polars as pl

    from .backtest import run_backtest
    from .config import load_config
    from .diagnose import diagnose, load_backtest_dirs, write_report
    cfg = load_config(args.config)
    if args.from_dirs:
        games, comp = load_backtest_dirs([Path(p) for p in args.from_dirs])
        label = "saved_backtests"
    else:
        frames, comps = [], []
        for s in _seasons(args.seasons):
            g, ex = run_backtest(cfg, s, list(range(args.first_week, args.last_week + 1)), sims=args.sims, workers=args.workers)
            frames.append(g)
            comps.append(ex["components"])
        games, comp = pl.concat(frames, how="diagonal_relaxed"), pl.concat(comps, how="diagonal_relaxed")
        label = f"{args.seasons}_w{args.first_week}-{args.last_week}"
    diag = diagnose(games, comp)
    out = cfg.resolve_path("diagnose") / f"{label}_{cfg.hash}"
    path = write_report(diag, out, f"Diagnose — {label} (config {cfg.hash})")
    print("Top 3 things most likely wrong:")
    for i, s in enumerate(diag["summary"], 1):
        print(f"  {i}. {s}")
    print("\nComponents (sorted by |bias| in SE):")
    for c in diag["components"][:12]:
        print(f"  {'*' if c['flag'] else ' '} {c['component']:<18} sim {c['sim']:8.3f}  actual {c['actual']:8.3f}  "
              f"bias {c['bias']:+8.3f}  ({c['t']:+.1f} SE)")
    print(f"\nreport: {path}")
    return 0


def cmd_tune(args) -> int:
    from .config import load_config
    from .tune import apply_tuned, run_tune
    cfg = load_config(args.config)
    if args.apply and not args.train:
        b = apply_tuned(cfg)
        print(f"tuned values written to your settings, config.local.yaml (backup: {b}); logged in tuning/history.csv")
        return 0
    out = run_tune(cfg, _seasons(args.train), int(args.validate), args.trials, sims=args.sims, workers=args.workers,
                   sampling_only=args.sampling_only)
    print(f"tuning output: {out}")
    if args.apply:
        summ = json.loads((out / "summary.json").read_text())
        if summ["recommended"]:
            b = apply_tuned(load_config(args.config))       # the settings as they are now, not as at the start
            print(f"promoted (backup: {b})")
        else:
            print("not promoted: validation did not improve")
    return 0


def cmd_predict(args) -> int:
    from .config import load_config
    from .live import predict_week
    cfg = load_config(args.config)
    paths = predict_week(cfg, args.season, args.week, sims=args.sims)
    print(f"{len(paths)} frozen predictions written")
    return 0


def cmd_grade(args) -> int:
    from .config import load_config
    from .live import format_scorecard, grade, scorecard
    cfg = load_config(args.config)
    grade(cfg, args.season, args.week)
    print(format_scorecard(scorecard(cfg, args.season)))
    return 0


def cmd_where(args) -> int:
    from .config import load_config
    from .resources import memory_gb, worker_count
    from .userdata import status
    cfg = load_config(args.config)
    st = status(cfg)
    print(f"data folder:    {st['data_dir']}")
    print(f"backups folder: {st['backup_dir']}  ({len(st['backups'])} backups"
          + (f", newest {st['backups'][0].name}" if st["backups"] else "") + ")")
    print("contents:       " + ", ".join(f"{v} {k}" for k, v in st["counts"].items()))
    total, avail = memory_gb()
    if total:
        print(f"memory:         {total:.0f} GB total, {avail or 0:.0f} GB free -> tuning would use "
              f"{worker_count(cfg)} worker(s)")
    return 0


def cmd_backup(args) -> int:
    from .config import load_config
    from .userdata import backup
    print(f"backed up to {backup(load_config(args.config), 'manual')}")
    return 0


def cmd_restore(args) -> int:
    from .config import load_config
    from .userdata import format_report, restore
    cfg = load_config(args.config)
    src = args.zip
    if args.latest or not src:
        from .userdata import status
        src = status(cfg)["restore_candidate"]      # the lost folder's newest backup if there is one, else the newest
        if src is None:
            print("no backups found")
            return 1
    print(format_report(restore(src, cfg)))
    from .userdata import status
    st = status(cfg)
    print("data folder OK — the 'data looks lost' warning is cleared." if not st["warn_empty"] else
          f"some backups still hold data this folder doesn't have — next one: {st['restore_candidate']}")
    return 0


def cmd_import(args) -> int:
    from .config import load_config
    from .userdata import format_report, import_folder
    print(format_report(import_folder(args.folder, load_config(args.config))))
    return 0


def cmd_reconstruct(args) -> int:
    from .config import load_config
    from .live import reconstruct_week
    paths = reconstruct_week(load_config(args.config), args.season, args.week, sims=args.sims)
    print(f"{len(paths)} reconstructed predictions written (marked reconstructed; not counted in the live scorecard)")
    return 0


def cmd_backfill(args) -> int:
    from .config import load_config
    from .scanner import backfill_closing_prices
    backfill_closing_prices(load_config(args.config), args.season, args.week)
    return 0


def cmd_scorecard(args) -> int:
    from .config import load_config
    from .live import format_scorecard, scorecard
    print(format_scorecard(scorecard(load_config(args.config), args.season)))
    return 0


def cmd_weekly(args) -> int:
    from .config import load_config
    from .live import weekly
    weekly(load_config(args.config), tune=args.tune)
    return 0


def cmd_impact(args) -> int:
    from .api import RunRequest, player_impact
    from .config import load_config
    cfg = load_config(args.config)
    req = RunRequest(home=args.home, away=args.away, season=args.season, sims=args.sims, seed=args.seed, market_lines=True)
    r = player_impact(req, args.player, cfg, qb=args.qb)
    H = r["home"]
    what = f"{args.player} OUT" + (f", {args.qb} starts" if args.qb else "")
    print(f"=== {r['away']} @ {H}: impact of {what} ({r['n_sims']} sims each, same seed) ===")
    for k, lab in (("home_win", f"{H} win %"), ("margin", f"{H} margin"), ("total", "total")):
        a, b, d = r["with"][k], r["without"][k], r["delta"][k]
        f = (lambda x: f"{100 * x:.1f}%") if k == "home_win" else (lambda x: f"{x:+.2f}" if k == "margin" else f"{x:.2f}")
        print(f"  {lab:<14} with {f(a):>8}   without {f(b):>8}   change {100 * d if k == 'home_win' else d:+.2f}{' pts' if k == 'home_win' else ''}")
    print("  usage shifts (per game):")
    for s in r["usage_shifts"]:
        print(f"    {s['team']} {s['player']:<22} targets {s['d_targets']:+.2f}  carries {s['d_carries']:+.2f}  "
              f"rec yds {s['d_rec_yds']:+.1f}  rush yds {s['d_rush_yds']:+.1f}  pass yds {s['d_pass_yds']:+.1f}")
    return 0


def cmd_scan(args) -> int:
    from .config import load_config
    from .scanner import scan
    cfg = load_config(args.config)
    sigs = scan(cfg, args.season, args.week, notify_on=args.notify, sims=args.sims)
    strong = sum(s["confidence"] == "strong" for s in sigs)
    mod = sum(s["confidence"] == "moderate" for s in sigs)
    print(f"{len(sigs)} candidates: {strong} strong, {mod} moderate, {len(sigs) - strong - mod} watchlist "
          f"(READ-ONLY: gridsim never places orders)")
    return 0


def cmd_watch(args) -> int:
    import datetime as dt

    from .config import load_config
    from .positions import watch
    from . import positions as _pos
    cfg = load_config(args.config)
    alerts = watch(cfg, notify_on=args.notify, near_only=args.near, verbose=False)
    for a in alerts:
        print(f"{dt.datetime.now():%Y-%m-%d %H:%M} watch [{a['kind']}] {a['title']} — {a['message']}")
    for pr in _pos.LAST_PROBLEMS.values():              # a watch that could not see must not look healthy
        print(f"{dt.datetime.now():%Y-%m-%d %H:%M} watch: {pr['matchup']}: {pr['what']}")
    return 0


def cmd_signals(args) -> int:
    from .config import load_config
    from .scanner import grade_signals, signals_report
    cfg = load_config(args.config)
    grade_signals(cfg)
    print(signals_report(cfg, args.season))
    return 0


def cmd_snapshot(args) -> int:
    from .config import load_config
    from .scanner import snapshot_markets
    print(f"saved {snapshot_markets(load_config(args.config), args.season, args.week)}")
    return 0


def cmd_slate(args) -> int:
    from .config import load_config
    from .slate import run_slate, write_slate_html
    cfg = load_config(args.config)
    res = run_slate(cfg, args.season, args.week, sims=args.sims, workers=args.workers)
    if res:
        season = res[0].get("request", {}).get("season")
        week = res[0].get("request", {}).get("week")
        path = cfg.resolve_path(args.report or f"out/slate_{season}_wk{week}.html")
        print(f"slate report: {write_slate_html(res, path, f'Slate {season} week {week}')}")
    return 0


def cmd_season(args) -> int:
    import json as _json

    from .config import load_config
    from .season import format_season, season_sim
    cfg = load_config(args.config)
    from .data import current_season
    res = season_sim(cfg, args.season or current_season(), n_seasons=args.n, sims_per_game=args.sims)
    print(format_season(res))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(_json.dumps(res, indent=1))
    return 0


def register(sub) -> None:
    sl = sub.add_parser("slate", help="sim every game of a week, one combined report")
    sl.add_argument("--season", type=int, default=None)
    sl.add_argument("--week", type=int, default=None)
    sl.add_argument("--sims", type=int, default=None)
    sl.add_argument("--workers", type=int, default=None)
    sl.add_argument("--report", default=None)
    sl.set_defaults(func=cmd_slate)
    se = sub.add_parser("season", help="playoff / division / seed odds from the remaining schedule")
    se.add_argument("--season", type=int, default=None)
    se.add_argument("--n", type=int, default=2000, help="season simulations")
    se.add_argument("--sims", type=int, default=200, help="sims per remaining game")
    se.add_argument("--json", default=None)
    se.set_defaults(func=cmd_season)

    sc = sub.add_parser("scan", help="check open Kalshi NFL markets against the latest sims (read-only)")
    sc.add_argument("--season", type=int, default=None)
    sc.add_argument("--week", type=int, default=None)
    sc.add_argument("--sims", type=int, default=None)
    sc.add_argument("--notify", action="store_true", help="push alerts (ntfy / Discord / Windows toast)")
    sc.set_defaults(func=cmd_scan)
    wa = sub.add_parser("watch", help="check the positions you entered (price jumps, injury changes, sim changes); "
                                      "read-only, never trades")
    wa.add_argument("--notify", action="store_true", help="push alerts (ntfy / Discord / Windows toast)")
    wa.add_argument("--near", action="store_true",
                    help="only games kicking off within watch.near_kickoff_hours (for the 10-minute scheduled task)")
    wa.set_defaults(func=cmd_watch)
    sg = sub.add_parser("signals", help="signal ledger record by market type and confidence")
    sg.add_argument("--season", type=int, default=None)
    sg.set_defaults(func=cmd_signals)
    sn = sub.add_parser("snapshot-markets", help="save current Kalshi prices (closing-line value)")
    sn.add_argument("--season", type=int, default=None)
    sn.add_argument("--week", type=int, default=None)
    sn.set_defaults(func=cmd_snapshot)

    im = sub.add_parser("impact", help="what-if: game with vs without a player (or a QB swap)")
    im.add_argument("--home", required=True)
    im.add_argument("--away", required=True)
    im.add_argument("--player", required=True, help='player ruled out, e.g. "J.Jefferson" (TEAM:Name if ambiguous)')
    im.add_argument("--qb", default=None, help="starting QB to use instead (QB swap)")
    im.add_argument("--season", type=int, default=None)
    im.add_argument("--sims", type=int, default=2000)
    im.add_argument("--seed", type=int, default=42)
    im.set_defaults(func=cmd_impact)

    b = sub.add_parser("backtest", help="walk-forward backtest (no lookahead)")
    b.add_argument("--season", type=int, required=True)
    b.add_argument("--weeks", default="3-18")
    b.add_argument("--sims", type=int, default=1000)
    b.add_argument("--seed", type=int, default=2024)
    b.add_argument("--workers", type=int, default=None)
    b.add_argument("--include-post", action="store_true")
    b.set_defaults(func=cmd_backtest)

    d = sub.add_parser("diagnose", help="where is the sim wrong (HTML + JSON report)")
    d.add_argument("--seasons", default="2024-2025")
    d.add_argument("--first-week", type=int, default=3)
    d.add_argument("--last-week", type=int, default=18)
    d.add_argument("--sims", type=int, default=300)
    d.add_argument("--workers", type=int, default=None)
    d.add_argument("--from-dirs", nargs="*", default=None, help="saved backtest directories instead of re-running")
    d.set_defaults(func=cmd_diagnose)

    t = sub.add_parser("tune", help="Optuna walk-forward tuning (writes config.tuned.yaml)")
    t.add_argument("--train", default=None, help="e.g. 2022-2024")
    t.add_argument("--validate", default=None, help="held-out season, e.g. 2025")
    t.add_argument("--trials", type=int, default=150)
    t.add_argument("--sims", type=int, default=None)
    t.add_argument("--workers", type=int, default=None)
    t.add_argument("--apply", action="store_true", help="promote config.tuned.yaml (alone: promote the last run)")
    t.add_argument("--sampling-only", action="store_true",
                   help="tune only sampling-time knobs (reuses built models across trials; ~2x faster)")
    t.set_defaults(func=cmd_tune)

    p = sub.add_parser("predict-week", help="frozen pre-kickoff predictions for every game of a week")
    p.add_argument("--season", type=int, default=None)
    p.add_argument("--week", type=int, default=None)
    p.add_argument("--sims", type=int, default=None)
    p.set_defaults(func=cmd_predict)

    g = sub.add_parser("grade", help="score frozen predictions once games are final")
    g.add_argument("--season", type=int, required=True)
    g.add_argument("--week", type=int, required=True)
    g.set_defaults(func=cmd_grade)

    s = sub.add_parser("scorecard", help="cumulative live performance")
    s.add_argument("--season", type=int, default=None)
    s.set_defaults(func=cmd_scorecard)

    w = sub.add_parser("weekly", help="refresh, grade, diagnose, (tune), predict")
    w.add_argument("--tune", action="store_true")
    w.set_defaults(func=cmd_weekly)

    wh = sub.add_parser("where", help="where your data and backups are, and what they hold")
    wh.set_defaults(func=cmd_where)
    bk = sub.add_parser("backup", help="zip your data folder to the backup folder now")
    bk.set_defaults(func=cmd_backup)
    rs = sub.add_parser("restore", help="put a backup back (merges: nothing current is lost)")
    rs.add_argument("zip", nargs="?", default=None, help="backup ZIP (default with --latest: the newest)")
    rs.add_argument("--latest", action="store_true")
    rs.set_defaults(func=cmd_restore)
    ip = sub.add_parser("import", help="bring in data from an old GridSim folder (merges, never overwrites)")
    ip.add_argument("folder")
    ip.set_defaults(func=cmd_import)
    rc = sub.add_parser("reconstruct", help="after a data loss: re-create a past week's predictions (marked "
                                            "reconstructed; never counted in the live scorecard)")
    rc.add_argument("--season", type=int, required=True)
    rc.add_argument("--week", type=int, required=True)
    rc.add_argument("--sims", type=int, default=None)
    rc.set_defaults(func=cmd_reconstruct)
    bf = sub.add_parser("backfill-prices", help="after a data loss: fill missing Kalshi closing prices from Kalshi's "
                                                "price history (marked backfilled)")
    bf.add_argument("--season", type=int, default=None)
    bf.add_argument("--week", type=int, default=None)
    bf.set_defaults(func=cmd_backfill)
