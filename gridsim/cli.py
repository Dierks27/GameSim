"""gridsim command-line interface."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path



def _season_default() -> int:
    from .data import current_season
    return current_season()


def cmd_run(args) -> int:
    from .api import RunRequest, run_game
    from .availability import NameMatchError
    from .config import load_config
    from .report import console, to_json

    cfg = load_config(args.config)
    req = RunRequest(home=args.home, away=args.away, season=args.season or _season_default(), week=args.week,
                     sims=args.sims, seed=args.seed, spread=args.spread, total=args.total,
                     market_lines=args.market_lines, neutral=args.neutral, playoff=args.playoff,
                     through_week=args.through_week, outs=args.out, ins=args.inn, overrides_path=args.overrides,
                     props=args.prop, use_availability=not args.no_availability, use_players=not args.no_players,
                     state=args.state, combos=args.combo)
    try:
        summ = run_game(req, cfg, verbose=True, use_cache=False)
    except NameMatchError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
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
    from . import __version__
    p.add_argument("--version", action="version", version=f"gridsim {__version__}")
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
    r.add_argument("--state", default=None, help='live state, e.g. "Q3 8:42 MIN 17 GB 13 MIN ball 2nd&6 at GB 34 timeouts 2/3"')
    r.add_argument("--combo", action="append", default=[], help='"MIN:win; J.Jefferson:rec_yds>79.5; total>44.5" (repeatable)')
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


def _log_when_windowless() -> None:
    """Started by pythonw.exe (Windows scheduled tasks): there is no console, so write output to
    logs/scheduled.log in the data folder instead of losing it."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    import datetime as dt

    from .config import data_dir
    (data_dir() / "logs").mkdir(parents=True, exist_ok=True)
    fh = open(data_dir() / "logs" / "scheduled.log", "a", encoding="utf-8", buffering=1)
    head = f"\n=== {dt.datetime.now():%Y-%m-%d %H:%M} gridsim {' '.join(sys.argv[1:])}\n"
    if "watch" in sys.argv[1:2]:
        # the 10-minute position watch stays silent unless something happened; the first thing it does write gets
        # the usual header in front, so every line in the log can be traced to a task and a time
        fh = _HeaderOnFirstWrite(fh, head)
    else:
        fh.write(head)
    sys.stdout = sys.stderr = fh


class _HeaderOnFirstWrite:
    def __init__(self, fh, head: str):
        self._fh, self._head = fh, head

    def write(self, text):
        if text and self._head:
            self._fh.write(self._head)
            self._head = ""
        return self._fh.write(text)

    def __getattr__(self, name):
        return getattr(self._fh, name)


def _startup_checks(quiet: bool = False) -> None:
    """Move data from the code folder into the data folder (older versions kept it there) and warn loudly if the data
    folder is empty while backups exist (a fresh install that should be restored, not started fresh)."""
    try:
        from .config import load_config
        from .userdata import ensure, format_report, status
        rep = ensure()
        if rep.get("added") or rep.get("records added"):
            print("moved your data into the data folder: " + format_report(rep), file=sys.stderr)
        if rep.get("error"):
            print(f"(could not move data from the program folder: {rep['error']})", file=sys.stderr)
        st = status(load_config())
        if st["warn_empty"] and not quiet:
            bar = "!" * 78
            print(f"{bar}\n  Your GridSim data looks LOST: a backup holds predictions or records that the data folder\n"
                  f"  ({st['data_dir']}) doesn't have — best one: {st['restore_candidate']}.\n"
                  "  Automatic backups are paused until you decide. To get your predictions and records back:\n"
                  f"    gridsim restore \"{st['restore_candidate']}\"     (or GridSim -> Settings -> Your data)\n"
                  f"{bar}", file=sys.stderr)
    except Exception as e:  # never block a command
        print(f"(data folder check skipped: {e})", file=sys.stderr)


def _has_open_positions(args) -> bool:
    try:
        import json

        from .config import load_config
        p = load_config(getattr(args, "config", None)).resolve_path("positions") / "positions.json"
        if not p.exists():
            # (older versions kept data in the program folder: let the normal start-up move it over first)
            from .config import REPO_ROOT
            return (REPO_ROOT / "positions" / "positions.json").exists()
        return any(x.get("status") == "open" for x in json.loads(p.read_text(encoding="utf-8")).get("positions", []))
    except Exception:
        return True         # unsure: take the normal path


# Commands about the PROGRAM itself (its scheduled jobs, its updates). The installer and the uninstaller run them, so
# they must not create, move or write anything in the user's data folder: no data check, and their windowless output
# goes to the program's own state folder.
PROGRAM_COMMANDS = ("schedule", "update")


def _program_log_when_windowless() -> None:
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        import datetime as dt

        from .installed import state_dir
        fh = open(state_dir() / "program.log", "a", encoding="utf-8", buffering=1, errors="backslashreplace")
        # (the same kind of header as in scheduled.log, so every line can be traced to a command and a time)
        fh.write(f"\n=== {dt.datetime.now():%Y-%m-%d %H:%M} gridsim {' '.join(sys.argv[1:])}\n")
    except OSError:
        import os
        fh = open(os.devnull, "w")
    sys.stdout = sys.stderr = fh


def main(argv: list[str] | None = None) -> int:
    first = next((a for a in (sys.argv[1:] if argv is None else argv) if not a.startswith("-")), None)
    if first in PROGRAM_COMMANDS:
        _program_log_when_windowless()
        args = build_parser().parse_args(argv)
        try:
            return int(args.func(args) or 0)
        except Exception:
            import traceback
            traceback.print_exc()
            return 1
    _log_when_windowless()
    args = build_parser().parse_args(argv)
    if args.cmd == "watch" and not _has_open_positions(args):
        return 0            # runs every 10 minutes: nothing to watch means nothing to do (not even the data check)
    _startup_checks(quiet=args.cmd in ("restore", "import", "watch"))
    try:
        return int(args.func(args) or 0)
    except Exception:
        if sys.stdout is not sys.__stdout__:   # windowless run: make sure the traceback lands in the log
            import traceback
            traceback.print_exc()
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
