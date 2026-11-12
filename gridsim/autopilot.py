"""Autopilot: when GridSim is started, do whatever is due — nothing to remember, nothing to press.

In order: (1) refresh the data when it is old, (2) grade finished games against the predictions locked in for them
and grade the Kalshi signals, (3) lock in predictions for the coming games (once early in the week, once more after
Friday evening's final injury reports), (4) check Kalshi's prices when the last check is old, (5) the weekly
check-up once a week is complete. Each step is skipped when it is not due, so a second start a minute later does
nothing. The same library calls as the Weekly routine page and the scheduled tasks; READ-ONLY towards Kalshi.

The app starts it in the background (`start_if_due`) when it is opened and, through a timer (`ensure_ticker`),
every few minutes for as long as it stays running; it shows `status()`. `gridsim autopilot` runs it in the foreground. One run at a time (a pid file); every step is tried even when an earlier one failed.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import polars as pl

from .config import Config

PRICE_MAX_AGE_MIN = 45          # the scheduled price check runs every 30 minutes: only step in when that is not happening
STEPS = [("data", "Getting the latest data"), ("grade", "Grading finished games"),
         ("lockin", "Locking in predictions for the coming games"), ("prices", "Checking Kalshi's prices"),
         ("checkup", "Running the weekly check-up")]


class StepFailed(Exception):
    """A step ran without an error but did not get its job done; the text is plain English, shown as it is."""


def _dir(cfg: Config) -> Path:
    d = cfg.resolve_path("logs") / "autopilot"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _read(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(p: Path, obj) -> None:
    tmp = p.with_name(f"{p.name}.{os.getpid()}.partial")
    tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    for wait in (0, 0.2, 0.5, 1):
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            time.sleep(wait)
    tmp.unlink(missing_ok=True)


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        try:
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                               creationflags=0x08000000)
        except OSError:
            return False
        return str(pid) in (r.stdout or "") and "python" in (r.stdout or "").lower()
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split()[2] != "Z"
    except OSError:
        return True


def running(cfg: Config) -> bool:
    info = _read(_dir(cfg) / "run.pid", None)
    if not info:
        return False
    try:
        # a run that has been going for 3 hours is a leftover (the PC was switched off mid-run): not "running"
        age = (_now() - dt.datetime.fromisoformat(info["started_utc"])).total_seconds()
        return age < 3 * 3600 and pid_alive(int(info["pid"]))
    except (KeyError, TypeError, ValueError):
        return False


def status(cfg: Config) -> dict:
    """The last (or current) run: {"state": "running" | "done" | "never", "started_utc", "finished_utc", "steps":
    [{"key", "what", "state": "waiting" | "running" | "done" | "failed" | "skipped", "detail"}]}."""
    st = _read(_dir(cfg) / "status.json", {"state": "never", "steps": []})
    if st.get("state") == "running" and not running(cfg):
        st["state"] = "done"                 # it stopped without saying so (PC switched off, window closed)
        st["interrupted"] = True
    return st


# ---- what is due ----------------------------------------------------------------------------------------------------

def _season_sched(cfg: Config):
    from .data import current_season, load_schedules
    season = current_season()
    return season, load_schedules(cfg).filter(pl.col("season") == season)


def _data_due(cfg: Config, season: int, sched=None) -> str | None:
    from .data import _cache_dir, _is_stale
    d = _cache_dir(cfg)
    names = ["schedules.parquet", f"injuries_{season}.parquet", f"rosters_weekly_{season}.parquet",
             f"depth_charts_{season}.parquet"]
    # this season's plays exist only once a game has been played (early September: nothing to download yet)
    if sched is None or sched.filter(pl.col("home_score").is_not_null()).height:
        names.insert(0, f"pbp_{season}.parquet")
    for name in names:
        p = d / name
        if not p.exists():
            return f"{name} has not been downloaded yet"
        if _is_stale(p, season, cfg):
            return f"the data is more than {float(cfg.data['refresh_hours']):g} hours old"
    return None


def _grade_due(cfg: Config, season: int, sched) -> list[int]:
    """Weeks with a finished game that has a locked-in prediction and no grade yet."""
    import csv

    from .live import pred_dir
    card = cfg.resolve_path("predictions") / "scorecard.csv"
    done = set()
    if card.exists():
        with open(card, encoding="utf-8") as fh:
            done = {r["game_id"] for r in csv.DictReader(fh)}
    weeks = []
    fin = sched.filter(pl.col("home_score").is_not_null())
    for wk in sorted(set(fin["week"].to_list())):
        d = pred_dir(cfg, season, int(wk))
        if not d.exists():
            continue
        have = {f.name.split("__")[0] for f in d.glob("*__*.json")}
        if any(g in have and g not in done for g in fin.filter(pl.col("week") == wk)["game_id"].to_list()):
            weeks.append(int(wk))
    return weeks


def _signals_due(cfg: Config, sched) -> list[str]:
    """Finished games whose Kalshi signals have not been graded since they finished."""
    if not (cfg.resolve_path("signals") / "ledger.csv").exists():
        return []
    seen = set(_read(_dir(cfg) / "signals_graded.json", []))
    return [g for g in sched.filter(pl.col("home_score").is_not_null())["game_id"].to_list() if g not in seen]


def _week(cfg: Config) -> tuple[int, int]:
    from .api import week_slate
    season, week, _ = week_slate(cfg)
    return season, week


def _friday_cut(now: dt.datetime) -> dt.datetime:
    """This NFL week's Friday 19:00 US Eastern (the week runs Tuesday to Monday): after it, the final injury
    reports are out and predictions are locked in once more."""
    from .live import ET
    t = now.astimezone(ET)
    since_tue = (t.weekday() - 1) % 7                       # Tuesday = 0 ... Monday = 6
    fri = (t - dt.timedelta(days=since_tue) + dt.timedelta(days=3)).replace(hour=19, minute=0, second=0, microsecond=0)
    return fri.astimezone(dt.timezone.utc)


def _lockin_due(cfg: Config, now: dt.datetime) -> str | None:
    from .api import week_slate
    from .live import kickoff_utc, pred_dir
    season, week, games = week_slate(cfg)
    coming = [g for g in games if g.get("home_score") is None and (kickoff_utc(g) is None or kickoff_utc(g) > now)]
    if not coming:
        return None
    d = pred_dir(cfg, season, week)
    newest: dict[str, dt.datetime] = {}
    for f in d.glob("*__*.json") if d.exists() else []:
        try:
            gid, stamp = f.name.split("__")[:2]
            t = dt.datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        newest[gid] = max(newest.get(gid, t), t)
    if any(g["game_id"] not in newest for g in coming):
        return f"week {week} has games with no locked-in prediction yet"
    cut = _friday_cut(now)
    if now >= cut and any(newest[g["game_id"]] < cut for g in coming):
        return "the final injury reports are out (Friday evening): one more lock-in"
    return None


def _prices_due(cfg: Config, now: dt.datetime) -> str | None:
    from .api import week_slate
    from .live import kickoff_utc
    from .scanner import latest_snapshot
    season, week, games = week_slate(cfg)
    if not any(g.get("home_score") is None and (kickoff_utc(g) is None or kickoff_utc(g) > now) for g in games):
        return None
    snap = latest_snapshot(cfg, season, week)
    if snap is None:
        return "no Kalshi prices saved for this week yet"
    age = (now.timestamp() - snap.stat().st_mtime) / 60
    return f"the last price check was {age:.0f} minutes ago" if age > PRICE_MAX_AGE_MIN else None


def _checkup_due(cfg: Config, season: int, sched) -> tuple[int, int] | None:
    """(first week, last week) when the last COMPLETE regular-season week has no check-up report yet. (The week being
    played does not hide it: someone who opens GridSim only at weekends still gets last week's check-up.)"""
    if "game_type" in sched.columns:
        sched = sched.filter(pl.col("game_type") == "REG")   # the check-up replays regular-season games only
    if not sched.height:
        return None
    done = (sched.group_by("week").agg(pl.col("home_score").is_null().sum().alias("open"))
            .filter(pl.col("open") == 0)["week"].to_list())
    if not done:
        return None
    last = int(max(done))
    first = max(1, last - 3)
    if (cfg.resolve_path("diagnose") / f"{season}_wk{first}-{last}").exists():
        return None
    if f"{season}_wk{first}-{last}" in _read(_dir(cfg) / "checkup_none.json", []):
        return None                                         # tried before: there was nothing to check
    return first, last


def due(cfg: Config, now: dt.datetime | None = None) -> list[dict]:
    """What needs doing right now: [{"key", "what", "why"}], in the order it will be done. Cheap (no network)."""
    now = now or _now()
    out = []
    try:
        season, sched = _season_sched(cfg)
    except Exception as e:                                   # nothing downloaded yet
        return [{"key": "data", "what": STEPS[0][1], "why": f"no schedule yet ({type(e).__name__})"}]
    why = _data_due(cfg, season, sched)
    if why:
        out.append({"key": "data", "why": why})
    wk = _grade_due(cfg, season, sched)
    if wk or _signals_due(cfg, sched):
        out.append({"key": "grade", "why": ("week " + ", ".join(map(str, wk)) + " has finished games to grade") if wk
                    else "Kalshi signals of finished games to grade"})
    for key, fn in (("lockin", _lockin_due), ("prices", _prices_due)):
        try:
            why = fn(cfg, now)
        except Exception as e:
            why = None if key == "prices" else f"could not check ({type(e).__name__})"
        if why:
            out.append({"key": key, "why": why})
    if _checkup_due(cfg, season, sched):
        out.append({"key": "checkup", "why": "last week is complete and has no check-up yet"})
    names = dict(STEPS)
    return [{**x, "what": names[x["key"]]} for x in out]


# ---- doing it -------------------------------------------------------------------------------------------------------

def _do(cfg: Config, key: str) -> str:
    """Run one step if it is (still) due; returns a short note for the status. Raises on failure."""
    from .config import load_config
    cfg = load_config()                                      # (a step before may have changed nothing, but be current)
    now = _now()
    if key == "data":
        from .availability import refresh_availability
        from .data import current_season, refresh_all
        season = current_season()
        try:
            sched = _season_sched(cfg)[1]
        except Exception:
            sched = None
        if not _data_due(cfg, season, sched):
            return "up to date"
        err = None
        try:
            refresh_all(cfg, [season])
        except Exception as e:                               # e.g. this season's plays are not published yet
            err = e
        refresh_availability(cfg, season)                    # rosters / depth charts / injuries: whatever happened above
        try:
            sched = _season_sched(cfg)[1]
        except Exception:
            sched = None
        if _data_due(cfg, season, sched):
            raise StepFailed("the latest data could not be downloaded (no internet connection, or the data source is "
                             "not answering) — GridSim keeps using the data it already has"
                             + (f" [{type(err).__name__}]" if err else ""))
        return "downloaded"
    season, sched = _season_sched(cfg)
    if key == "grade":
        from .live import grade
        from .scanner import grade_signals
        weeks = _grade_due(cfg, season, sched)
        n = sum(len(grade(cfg, season, wk, verbose=False)) for wk in weeks)
        todo = _signals_due(cfg, sched)
        m = 0
        if todo:
            m = grade_signals(cfg)
            fin = sched.filter(pl.col("home_score").is_not_null())["game_id"].to_list()
            _write(_dir(cfg) / "signals_graded.json", fin)
        if not weeks and not todo:
            return "nothing to grade"
        return f"{n} game(s) graded" + (f", {m} Kalshi signal(s)" if m else "")
    if key == "lockin":
        from .live import pred_dir, predict_week
        why = _lockin_due(cfg, now)
        if not why:
            return "already locked in"
        # a scheduled lock-in (or another GridSim) may be half-way through: its files appear one by one
        d = pred_dir(cfg, *_week(cfg))
        newest = max((f.stat().st_mtime for f in d.glob("*__*.json")), default=0) if d.exists() else 0
        if "no locked-in prediction" in why and time.time() - newest < 15 * 60:
            return "a lock-in is already under way"
        cut = _friday_cut(now)
        if now >= cut:
            # A lock-in made after Friday evening is "the one after the final injury reports", and is not repeated:
            # it must be made WITH them. The data step leaves files younger than data.refresh_hours alone.
            from .availability import refresh_availability
            from .data import _cache_dir
            inj = _cache_dir(cfg) / f"injuries_{season}.parquet"
            before = inj.stat().st_mtime if inj.exists() else None
            if before is None or before < cut.timestamp():
                refresh_availability(cfg, season)
                after = inj.stat().st_mtime if inj.exists() else None
                if (after is None or after == before) and "final injury reports" in why:
                    raise StepFailed("the injury report could not be downloaded, so the lock-in after Friday's final "
                                     "injury reports was not made; it is tried again at the next start")
        return f"{len(predict_week(cfg, verbose=False))} game(s) locked in"
    if key == "prices":
        from .scanner import scan
        if not _prices_due(cfg, now):
            return "checked recently"
        sigs = scan(cfg, notify_on=True, verbose=False)
        if _prices_due(cfg, _now()):
            raise StepFailed("Kalshi's prices could not be fetched (no internet connection, or Kalshi is not "
                             "answering) — the prices shown are still the ones from the last check that worked")
        live = sum(s["confidence"] in ("strong", "moderate") for s in sigs)     # only these can reach the phone
        return f"prices saved: {live} signal(s), {len(sigs) - live} on the watchlist"
    if key == "checkup":
        from .backtest import run_backtest
        from .diagnose import diagnose, write_report
        span = _checkup_due(cfg, season, sched)
        if not span:
            return "already done"
        first, last = span
        g, ex = run_backtest(cfg, season, list(range(first, last + 1)), sims=int(cfg.tune["sims_per_game"]), verbose=False)
        if not g.height:
            done = _read(_dir(cfg) / "checkup_none.json", [])
            _write(_dir(cfg) / "checkup_none.json", (done + [f"{season}_wk{first}-{last}"])[-20:])
            return "already done"                            # (nothing to check: remembered, not asked again)
        write_report(diagnose(g, ex["components"]), cfg.resolve_path("diagnose") / f"{season}_wk{first}-{last}",
                     f"Diagnose {season} weeks {first}-{last}")
        return f"weeks {first}-{last}"
    raise ValueError(key)


def run(cfg: Config, verbose: bool = True) -> dict:
    """Do everything that is due, in order. Returns the final status. One run at a time."""
    d = _dir(cfg)
    held = _read(d / "run.pid", None) or {}
    mine = held.get("pid") == os.getpid() and held.get("reserved")           # (the app wrote it when it started us)
    if running(cfg) and not mine:
        if verbose:
            print("autopilot: already running")
        return status(cfg)
    started = _now().isoformat(timespec="seconds")
    _write(d / "run.pid", {"pid": os.getpid(), "started_utc": started})
    todo = {x["key"]: x["why"] for x in due(cfg)}
    # the data comes first and can make more work (new results to grade, a new week): every later step is asked
    # again when its turn comes, so all of them are listed and the ones with nothing to do say so
    st = {"state": "running", "started_utc": started, "finished_utc": None, "pid": os.getpid(),
          "steps": [{"key": k, "what": what, "state": "waiting", "detail": todo.get(k, "")} for k, what in STEPS]}
    _write(d / "status.json", st)
    try:
        for step in st["steps"]:
            step["state"] = "running"
            _write(d / "status.json", st)
            if verbose:
                print(f"autopilot: {step['what']} ...", flush=True)
            try:
                note = _do(cfg, step["key"])
                quiet = note in ("up to date", "nothing to grade", "already locked in", "checked recently", "already done",
                                 "a lock-in is already under way")
                step["state"], step["detail"] = ("skipped" if quiet else "done"), note
            except StepFailed as e:                          # (its text is already plain English)
                step["state"], step["detail"] = "failed", str(e)
            except Exception as e:                           # one failed step must not stop the others
                step["state"], step["detail"] = "failed", f"{type(e).__name__}: {str(e)[:160]}"
            if verbose:
                print(f"  {step['state']}: {step['detail']}", flush=True)
            _write(d / "status.json", st)
    finally:
        st["state"], st["finished_utc"] = "done", _now().isoformat(timespec="seconds")
        _write(d / "status.json", st)
        (d / "run.pid").unlink(missing_ok=True)
    return st


_LAST_LOOK = 0.0


def start_if_due(cfg: Config, min_gap_s: float = 600.0) -> str:
    """Called by the app: start a background run when something is due and none is running. Looks at most once
    every min_gap_s per app process. Returns "running", "started", "nothing" or "waiting" (looked recently)."""
    global _LAST_LOOK
    if running(cfg):
        return "running"
    if time.time() - _LAST_LOOK < min_gap_s:
        return "waiting"
    _LAST_LOOK = time.time()
    if not due(cfg):
        return "nothing"
    root = Path(__file__).resolve().parent.parent
    log = open(_dir(cfg) / "autopilot.log", "a", encoding="utf-8")
    log.write(f"\n=== {dt.datetime.now():%Y-%m-%d %H:%M} autopilot\n")
    log.flush()
    # Windows: no console window, keeps going if the GridSim window is closed, below-normal priority.
    # 0x01000000 = CREATE_BREAKAWAY_FROM_JOB: the installed program ends everything the app started when its window
    # is closed (a job object) — except what asks to be left out, so that a half-finished lock-in is not cut off.
    # Where that is not allowed Windows refuses the start: then it is started without.
    kw = ({"creationflags": 0x08000000 | 0x00000200 | 0x00004000 | 0x01000000} if sys.platform == "win32"
          else {"start_new_session": True})
    cmd = [sys.executable, "-m", "gridsim", "autopilot"]
    try:
        p = subprocess.Popen(cmd, cwd=str(root), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kw)
    except OSError:
        if "creationflags" not in kw:
            raise
        kw["creationflags"] &= ~0x01000000
        p = subprocess.Popen(cmd, cwd=str(root), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kw)
    # counted as running from this moment (the child takes a second to say so itself): never two at once
    _write(_dir(cfg) / "run.pid", {"pid": getattr(p, "pid", os.getpid()), "reserved": True,
                                   "started_utc": _now().isoformat(timespec="seconds")})
    return "started"


# ---- while GridSim stays open ---------------------------------------------------------------------------------------

_TICKER: threading.Thread | None = None
_TICKER_STOP: threading.Event | None = None
_TICKER_LOCK = threading.Lock()
TICK_S = 300.0                 # how often a running GridSim asks "is anything due?" on its own


def _tick(interval_s: float, stop: threading.Event) -> None:
    from .config import load_config
    while not stop.wait(interval_s):
        try:
            start_if_due(load_config(), min_gap_s=max(interval_s - 1.0, 0.0))
        except Exception:      # a bad moment (a file being replaced, no disk) must not end the timer
            pass


def ensure_ticker(interval_s: float = TICK_S) -> bool:
    """Start, once per GridSim process, a timer that looks every few minutes whether something has become due and
    starts it — so a GridSim that is left running keeps itself up to date without a click, a page load or a
    restart (also while its window is closed or the PC was asleep: the next tick after waking picks it up).
    Returns True when this call started the timer."""
    global _TICKER, _TICKER_STOP
    with _TICKER_LOCK:
        if _TICKER is not None and _TICKER.is_alive():
            return False
        _TICKER_STOP = threading.Event()
        _TICKER = threading.Thread(target=_tick, args=(interval_s, _TICKER_STOP), name="gridsim-autopilot", daemon=True)
        _TICKER.start()
        return True


def stop_ticker(timeout: float = 5.0) -> None:
    """End the timer (tests; a clean shutdown)."""
    global _TICKER
    with _TICKER_LOCK:
        if _TICKER_STOP is not None:
            _TICKER_STOP.set()
        if _TICKER is not None:
            _TICKER.join(timeout)
        _TICKER = None
