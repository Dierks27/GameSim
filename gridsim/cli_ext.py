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
        print(f"config.tuned.yaml promoted to config.yaml (backup: {b}); logged in tuning/history.csv")
        return 0
    out = run_tune(cfg, _seasons(args.train), int(args.validate), args.trials, sims=args.sims, workers=args.workers)
    print(f"tuning output: {out}")
    if args.apply:
        summ = json.loads((out / "summary.json").read_text())
        if summ["recommended"]:
            b = apply_tuned(cfg)
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


def register(sub) -> None:
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
