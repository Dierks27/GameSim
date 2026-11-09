"""Your data folder: backups, restore, and bringing in data from an old GridSim folder.

Two kinds of files:
  re-downloadable  data/cache (nflverse), out/cache (sims), weather / market caches   -> stay in the code folder
  irreplaceable    predictions/, signals/ (ledger, graded, Kalshi price snapshots), overrides/, tuning/,
                   backtests/, diagnose/, logs/, config.local.yaml, config.tuned.yaml, regime_changes.yaml, .env
                   -> the data folder (config.data_dir(), default <user folder>/GridSim Data)

backup()        zips the data folder to the backup folder (default <OneDrive>/GridSim Backups, else
                <user folder>/GridSim Backups) with a timestamp; keeps the newest `backup.keep`.
auto_backup()   the same, never raises; called after Lock in, Grade, the weekly run and once a day.
import_folder() MERGES another folder or backup ZIP into the data folder. Nothing is ever overwritten or lost:
                frozen predictions and snapshots are only added, CSV records are unioned row by row, your current
                settings win over imported ones. Used for restore, for old installs and for the automatic move of
                data from the code folder (older GridSim versions kept it there).
"""
from __future__ import annotations

import csv
import datetime as dt
import filecmp
import hashlib
import io
import json
import os
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

import yaml

from .config import REPO_ROOT, data_dir, deep_merge

BACKUP_PREFIX = "GridSim-backup-"
# Example runs shipped with the code (developer backtests / tuning / check-up). They stay in the code folder and are
# never copied into your data folder.
SHIPPED_RUNS = {
    "backtests": {"2024_w3-18_a9e0d47f2c_20261105-083327", "baseline_2025_w3-18_01b0802074_20261105-071634",
                  "matchup1_2025_w3-18_107859bc27_20261105-071923",
                  "matchup_qbfix_2025_w3-18_3e2f3dd9dc_20261105-072839"},
    "diagnose": {"2024-2025_w3-18_3e2f3dd9dc"},
    "tuning": {"20261105-074738"},
}
# The developer's own week-9 test predictions shipped in an early download: not the user's record.
DEV_PREDICTION_MARK = "__20261105T080054Z__9ce081a6ac.json"
RUN_DIRS = ("tuning", "backtests", "diagnose")
DATA_ITEMS = ("predictions", "signals", "overrides", "tuning", "backtests", "diagnose", "logs",
              "config.local.yaml", "config.tuned.yaml", "regime_changes.yaml", ".env", ".env.txt")
# CSV records merged row by row: file -> key columns (None = the whole row)
CSV_KEYS = {
    "signals/ledger.csv": None,
    "signals/graded.csv": ("ts_utc", "ticker", "side"),
    "predictions/scorecard.csv": ("game_id",),           # one graded row per game: the latest pre-kickoff one
    "predictions/scorecard_reconstructed.csv": ("game_id",),
    "tuning/history.csv": None,
}
SKIP_NAMES = {"gui_tune.pid", ".DS_Store", "Thumbs.db", "desktop.ini"}
REGIME_TEMPLATE = ("# Coaching / play-caller changes (yours). Plays by that team before `date` get regime.pre_change_weight\n"
                   "# (config.yaml) for play-call tendencies only. Example:\n"
                   "# - team: MIN\n#   date: 2026-10-20\n#   note: \"OC fired; HC takes over play calling\"\n[]\n")


# ---- locations -----------------------------------------------------------------------------------------------------

def normalize_dir(text: str) -> Path | None:
    """A folder typed by the user: quotes stripped, %VARIABLES% and ~ expanded. None unless it is a full path
    (C:\\..., \\\\server\\..., /...) — a relative one would land wherever the program happens to run."""
    import re
    t = str(text or "").strip().strip('"').strip("'")
    t = re.sub(r"%([^%]+)%", lambda m: os.environ.get(m.group(1), m.group(0)), t)    # %OneDrive% on any platform
    t = os.path.expandvars(t)
    if not t or "%" in t:
        return None
    p = Path(t).expanduser()
    return p if p.is_absolute() else None


def backup_dir(cfg=None) -> Path:
    b = ((cfg.raw.get("backup") or {}) if cfg is not None else {}).get("dir") or ""
    p = normalize_dir(b)
    if p is not None:
        return p
    for var in ("OneDriveConsumer", "OneDrive", "OneDriveCommercial"):
        od = os.environ.get(var)
        if od and Path(od).is_dir():
            return Path(od) / "GridSim Backups"
    return Path.home() / "GridSim Backups"


def _locations_file() -> Path:
    return Path.home() / ".gridsim_backup_folders.txt"


def remember_backup_dir(p: Path) -> None:
    """Remember every backup folder ever used (outside the data folder), so backups stay findable after the data
    folder — which holds the backup.dir setting — is lost."""
    try:
        f = _locations_file()
        have = set(f.read_text(encoding="utf-8").splitlines()) if f.exists() else set()
        if str(p) not in have:
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(f"{p}\n")
    except OSError:
        pass


def backup_dirs(cfg=None) -> list[Path]:
    """The current backup folder first, then the automatic default and every folder used before."""
    out = [backup_dir(cfg), backup_dir(None)]
    try:
        f = _locations_file()
        if f.exists():
            out += [Path(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    except OSError:
        pass
    seen, res = set(), []
    for d in out:
        k = str(d).lower()
        if k not in seen:
            seen.add(k)
            res.append(d)
    return res


def list_backups(cfg=None, everywhere: bool = True) -> list[Path]:
    """Backups, newest first (names start with the time). everywhere=False: only the current backup folder."""
    found = []
    for d in (backup_dirs(cfg) if everywhere else [backup_dir(cfg)]):
        if d.is_dir():
            found += list(d.glob(f"{BACKUP_PREFIX}*.zip"))
    return sorted(set(found), key=lambda p: p.name, reverse=True)


def run_dirs(cfg, name: str) -> list[tuple[Path, bool]]:
    """[(folder, shipped?)] for a run folder name (backtests / diagnose / tuning): yours in the data folder first,
    then the example runs shipped in the program folder."""
    out = [(cfg.resolve_path(name), False)]
    shipped = REPO_ROOT / name
    if shipped.is_dir() and shipped.resolve() != out[0][0].resolve():
        out.append((shipped, True))
    return out


ID_FILE = ".gridsim_data_id"
KNOWN_FILE = ".gridsim_known_ids"
LEGACY_FILE = ".legacy_imported.json"


def host() -> str:
    import socket
    return (os.environ.get("COMPUTERNAME") or socket.gethostname() or "?").upper()


def data_id(d: Path | None = None) -> str:
    """A random ID for this data folder, kept inside it and recorded in every backup, so backups of a folder that
    was lost or replaced can be told apart from this folder's own."""
    import re
    d = d or data_dir()
    p = d / ID_FILE
    try:
        v = p.read_text(encoding="utf-8", errors="replace").strip() if p.exists() else ""
    except OSError:
        v = ""
    if re.fullmatch(r"[0-9a-f]{32}", v):
        return v
    d.mkdir(parents=True, exist_ok=True)
    full = has_data(d)
    v = ""
    if full:      # the ID file was deleted / damaged but the data is here: take back this folder's own ID
        for z in list_backups():
            info = backup_info(z)
            if info.get("data_id") and info.get("host") == host() and info.get("data_dir") == str(d):
                v = info["data_id"]
                break
    v = v or uuid.uuid4().hex
    _atomic_write(p, v)
    lines = [f"id:{v}"]
    if full:      # an existing folder getting its first ID: backups made before IDs existed are its own
        lines += [f"file:{z.name}" for z in list_backups() if not backup_info(z).get("data_id")]
    _add_known(d, lines)
    return v


def _known(d: Path | None = None) -> dict:
    d = d or data_dir()
    k = {"ids": set(), "files": set(), "fresh": ""}
    p = d / KNOWN_FILE
    if p.exists():
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                kind, _, val = line.partition(":") if ":" in line else ("id", "", line)
                if kind == "id" and val:
                    k["ids"].add(val)
                elif kind == "file" and val:
                    k["files"].add(val)
                elif kind == "fresh" and val > k["fresh"]:
                    k["fresh"] = val
        except OSError:
            pass
    return k


def _add_known(d: Path, lines) -> None:
    lines = [x for x in lines if x and not x.endswith(":")]
    if lines:
        d.mkdir(parents=True, exist_ok=True)
        with open(d / KNOWN_FILE, "a", encoding="utf-8") as fh:
            fh.writelines(f"{x}\n" for x in lines)


def known_ids(d: Path | None = None) -> set[str]:
    d = d or data_dir()
    return _known(d)["ids"] | {data_id(d)}


def remember_ids(ids, d: Path | None = None) -> None:
    d = d or data_dir()
    cur = known_ids(d)
    _add_known(d, [f"id:{i}" for i in sorted({i for i in ids if i} - cur)])


_INFO_CACHE: dict = {}


def backup_info(z: Path) -> dict:
    """The manifest inside a backup ZIP (created, reason, host, data_id, counts); {} if unreadable."""
    try:
        key = (str(z), z.stat().st_mtime_ns)
    except OSError:
        return {}
    if key not in _INFO_CACHE:
        try:
            with zipfile.ZipFile(z) as zz:
                _INFO_CACHE[key] = json.loads(zz.read("gridsim_backup.json"))
        except Exception:
            return {}            # e.g. an online-only OneDrive file while offline: try again next time
    return _INFO_CACHE[key]


def has_data(d: Path | None = None) -> bool:
    """True if the data folder holds any part of your track record."""
    d = d or data_dir()
    if any((d / "predictions").rglob("*.json")) if (d / "predictions").is_dir() else False:
        return True
    return any((d / f).exists() for f in ("signals/ledger.csv", "signals/graded.csv", "predictions/scorecard.csv",
                                           "config.local.yaml"))


def counts(d: Path | None = None) -> dict:
    d = d or data_dir()

    def n(sub, pat):
        p = d / sub
        return sum(1 for _ in p.rglob(pat)) if p.is_dir() else 0

    led = d / "signals" / "ledger.csv"
    return {"predictions": n("predictions", "*__*.json"), "price snapshots": n("signals/snapshots", "*.json"),
            "signal records": max(0, sum(1 for _ in open(led, encoding="utf-8")) - 1) if led.exists() else 0,
            "lineup files": n("overrides", "*.yaml"),
            "tuning runs": n("tuning", "summary.json"), "check-ups": n("diagnose", "diagnose.json")}


def status(cfg=None) -> dict:
    """warn_empty — "your data looks lost" — is true when a backup holds data this folder doesn't have:
      * a backup made on THIS computer from a data folder this one doesn't know (the folder was lost, deleted or
        replaced; the ID tells), or with MORE predictions or signal records than this folder has (those only ever
        grow, so fewer means something was lost — whatever happened since, e.g. a scan or a lock-in);
      * on a folder with no predictions at all: a backup from ANOTHER computer's unknown folder (a new PC).
    Restoring such a backup or choosing "start fresh" clears it. While it is true, automatic backups pause and the
    code-folder migration waits."""
    d = data_dir()
    b = list_backups(cfg)
    mine = data_id(d)                    # first: a folder getting its first ID claims older unlabeled backups
    cur = counts(d)
    kn, me = _known(d), host()
    ids = kn["ids"] | {mine}
    lost = []
    for z in b:
        info = backup_info(z)
        if not info:
            continue
        if kn["fresh"] and str(info.get("created", "")) <= kn["fresh"]:
            continue
        did, c = info.get("data_id"), info.get("counts") or {}
        unknown = (did not in ids) if did else (z.name not in kn["files"])
        if info.get("host") in (None, me):
            behind = (int(c.get("predictions", 0)) > cur["predictions"]
                      or int(c.get("signal records", 0)) > cur["signal records"])
            if unknown or behind:
                lost.append((z, c))
        elif cur["predictions"] == 0 and unknown:
            lost.append((z, c))
    cand = (max(lost, key=lambda t: (int(t[1].get("predictions", 0)), int(t[1].get("signal records", 0)),
                                     str(backup_info(t[0]).get("created", "")), t[0].name))[0]
            if lost else (b[0] if b else None))
    return {"data_dir": d, "backup_dir": backup_dir(cfg), "backups": b, "empty": not has_data(d),
            "lost_backups": [z for z, _ in lost], "warn_empty": bool(lost), "restore_candidate": cand,
            "counts": cur}


def start_fresh(cfg=None) -> None:
    """The user chose to keep the current data folder: stop warning about any backup made until now (they are not
    deleted and can still be restored later)."""
    d = data_dir()
    bs = list_backups(cfg)
    _add_known(d, [f"fresh:{dt.datetime.now().astimezone().isoformat(timespec='seconds')}"]
               + [f"id:{backup_info(z)['data_id']}" for z in bs if backup_info(z).get("data_id")]
               + [f"file:{z.name}" for z in bs if not backup_info(z).get("data_id")])


# ---- start-up --------------------------------------------------------------------------------------------------------

def ensure(cfg=None, verbose: bool = False) -> dict:
    """Create the data folder and move data from the code folder into it (older GridSim versions kept it there).
    Safe to run on every start: each code-folder file is taken once. While the data looks lost (status), the move
    waits until you restore or start fresh — so stale copies never get in ahead of your backup."""
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    data_id(d)
    rep = {}
    if _legacy_present(REPO_ROOT) and REPO_ROOT.resolve() != d.resolve():
        if status(cfg)["warn_empty"]:
            return {"deferred": True}
        mp = d / LEGACY_FILE
        try:
            all_seen = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
        except (OSError, ValueError):
            all_seen = {}
        if all_seen and all(isinstance(v, str) for v in all_seen.values()):     # older flat format
            all_seen = {str(REPO_ROOT.resolve()): all_seen}
        seen = all_seen.setdefault(str(REPO_ROOT.resolve()), {})   # per program folder: two installs don't clash
        try:
            rep = import_folder(REPO_ROOT, cfg=cfg, backup_first=False, verbose=verbose, legacy=True, seen=seen)
        except Exception as e:  # never block start-up
            rep = {"error": str(e)}
        finally:
            try:
                _atomic_write(mp, json.dumps(all_seen, indent=0, sort_keys=True))
            except OSError:
                pass
    rc = d / "regime_changes.yaml"
    if not rc.exists():
        rc.write_text(REGIME_TEMPLATE, encoding="utf-8")
    return rep


def _legacy_present(root: Path) -> bool:
    for name in ("predictions", "signals", "overrides", "logs"):
        p = root / name
        if p.is_dir() and any(f.is_file() for f in p.rglob("*")):
            return True
    if any((root / f).exists() for f in ("config.local.yaml", "config.tuned.yaml", ".env", ".env.txt")):
        return True
    for sub in RUN_DIRS:
        p = root / sub
        if p.is_dir() and any(c.name not in SHIPPED_RUNS.get(sub, set()) for c in p.iterdir()):
            return True
    return False


# ---- backup ----------------------------------------------------------------------------------------------------------

def backup(cfg=None, reason: str = "manual", verbose: bool = False) -> Path:
    d = data_dir()
    out_dir = backup_dir(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in reason)[:30] or "backup"
    final = out_dir / f"{BACKUP_PREFIX}{stamp}-{safe}.zip"
    i = 1
    while final.exists():
        final = out_dir / f"{BACKUP_PREFIX}{stamp}-{safe}-{i}.zip"
        i += 1
    tmp = final.with_suffix(".zip.partial")
    d_res, bdir = d.resolve(), out_dir.resolve()
    inside = bdir != d_res and d_res in bdir.parents       # backups kept in a subfolder of the data folder
    did = data_id(d)
    n_files = 0
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for f in sorted(d.rglob("*")):
                if not f.is_file() or f.name in SKIP_NAMES or f.suffix == ".partial":
                    continue
                if f.name.startswith(BACKUP_PREFIX) and f.suffix == ".zip":             # never zip a backup
                    continue
                if inside and bdir in f.resolve().parents:
                    continue
                z.write(f, f.relative_to(d).as_posix())
                n_files += 1
            z.writestr("gridsim_backup.json", json.dumps({
                "gridsim_backup": 1, "created": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                "reason": reason, "host": host(), "data_dir": str(d), "data_id": did, "files": n_files,
                "counts": counts(d)},
                indent=1))
        if has_data(d) and n_files == 0:
            raise RuntimeError("nothing could be added to the backup")
        for attempt in range(5):        # OneDrive may hold the folder for a moment while it syncs
            try:
                os.replace(tmp, final)  # never leave a half-written ZIP under the real name
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(1 + attempt)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    remember_backup_dir(out_dir)
    keep = max(1, int(((cfg.raw.get("backup") or {}).get("keep", 40)) if cfg is not None else 40))
    _prune(cfg, keep)
    if verbose:
        print(f"backup: {final}")
    return final


def _prune(cfg, keep: int) -> None:
    """Delete only THIS data folder's oldest backups (beyond the newest `keep`) in the current backup folder.
    Backups of any other data folder or computer, and backups made before IDs existed, are never deleted."""
    mine = data_id()
    own = [z for z in list_backups(cfg, everywhere=False) if backup_info(z).get("data_id") == mine]
    for old in own[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


def auto_backup(cfg=None, reason: str = "auto", min_interval_h: float | None = None) -> Path | None:
    """Back up without ever failing the caller. min_interval_h: skip if a backup is newer than this. Paused while the
    data folder looks lost (see status), so a nearly empty folder never crowds out the real backups."""
    try:
        if not has_data() or status(cfg)["warn_empty"]:
            return None
        if min_interval_h:
            mine = data_id()
            b = [z for z in list_backups(cfg, everywhere=False) if backup_info(z).get("data_id") == mine]
            if b and (dt.datetime.now().timestamp() - b[0].stat().st_mtime) < min_interval_h * 3600:
                return None
        return backup(cfg, reason)
    except Exception as e:
        print(f"  (automatic backup failed: {e})")
        return None


# ---- import / restore ------------------------------------------------------------------------------------------------

def find_source(path: str | Path) -> Path:
    """The folder that holds GridSim data: the given folder, or (for a parent such as C:\\GridSim) the one GridSim
    folder inside it. Raises ValueError with a plain-English message otherwise."""
    p = Path(str(path).strip().strip('"').strip("'")).expanduser()
    if not p.exists():
        raise ValueError(f"Can't find {p}. Check the spelling (tip: in Explorer, Shift + right-click the folder -> "
                         "'Copy as path', then paste it here).")
    if p.is_file():
        if p.suffix.lower() == ".zip":
            return p
        p = p.parent

    def looks(q: Path) -> bool:
        return any((q / n).exists() for n in DATA_ITEMS)

    if looks(p):
        return p
    found = [q for q in p.iterdir() if q.is_dir() and looks(q)] if p.is_dir() else []
    if not found:
        found = [r for q in p.iterdir() if q.is_dir() for r in q.iterdir() if r.is_dir() and looks(r)]
    if len(found) == 1:
        return found[0]
    if not found:
        raise ValueError(f"No GridSim data in {p} (looked for folders such as predictions, signals, overrides).")
    raise ValueError(f"More than one GridSim folder inside {p}: " + ", ".join(q.name for q in found)
                     + ". Pick one of them.")


def restore(zip_path: str | Path, cfg=None, verbose: bool = False) -> dict:
    """Put a backup back. Merges: anything in the backup that is missing now comes back; nothing current is lost.
    Afterwards the folder it came from counts as known, and the code-folder move (if it was waiting) runs."""
    z = Path(str(zip_path).strip().strip('"'))
    rep = import_folder(z, cfg=cfg, verbose=verbose)
    if z.suffix.lower() == ".zip" and not backup_info(z).get("data_id"):
        _add_known(data_dir(), [f"file:{x.name}" for x in list_backups(cfg) if not backup_info(x).get("data_id")])
    later = ensure(cfg)
    if later.get("added") or later.get("records added"):
        rep["notes"].append("also moved in what was left in the GridSim program folder (" + format_report(later)
                            .split(": ", 1)[-1].split("\n")[0] + ")")
    return rep


def import_folder(src: str | Path, cfg=None, backup_first: bool = True, verbose: bool = False,
                  legacy: bool = False, seen: dict | None = None, label: str | None = None) -> dict:
    """Merge GridSim data from `src` (an old GridSim folder, a data folder or a backup ZIP) into the data folder.
    Returns a report {category: count, ...} with 'notes' for decisions and 'failed' for files that couldn't be read
    (the rest is still merged). seen: {file: fingerprint} of code-folder files already taken (ensure())."""
    src_p = find_source(src) if not legacy else Path(src)
    dest = data_dir()
    dest.mkdir(parents=True, exist_ok=True)
    if src_p.suffix.lower() == ".zip":
        with tempfile.TemporaryDirectory(prefix="gridsim-restore-") as tmp:
            try:
                with zipfile.ZipFile(src_p) as z:
                    for m in z.infolist():                   # refuse paths that would land outside the temp folder
                        target = (Path(tmp) / m.filename).resolve()
                        if Path(tmp).resolve() not in target.parents and target != Path(tmp).resolve():
                            raise ValueError(f"unsafe path in backup: {m.filename}")
                    z.extractall(tmp)
            except zipfile.BadZipFile:
                raise ValueError(f"{src_p.name} is not a readable ZIP file.") from None
            try:
                root = find_source(tmp)      # our backups: the ZIP root; Explorer-made ZIPs: the folder inside
            except ValueError as e:
                raise ValueError(f"No GridSim data in {src_p.name}." + (" It holds more than one GridSim folder."
                                                                        if "More than one" in str(e) else "")) from None
            info = {}
            if (root / "gridsim_backup.json").exists():
                try:
                    info = json.loads((root / "gridsim_backup.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    info = {}
            r = import_folder(root, cfg=cfg, backup_first=backup_first, verbose=False, legacy=True,
                              label=label or f"{src_p.name}")
            remember_ids({info.get("data_id")} | set(r.pop("_ids", set())))
            if r["added"] or r["records added"]:
                auto_backup(cfg, "after-restore")
            if verbose:
                print(format_report(r))
            return r
    if src_p.resolve() == dest.resolve():
        raise ValueError("That is GridSim's own data folder — nothing to bring in.")
    if backup_first and has_data(dest):
        auto_backup(cfg, "before-import")
    rep = {"added": 0, "already there": 0, "records added": 0, "notes": [], "failed": [],
           "source": label or str(src_p), "_seen": seen}

    def guarded(fn, key, *a):
        try:
            if seen is not None:
                fp = _fp(a[0])
                if seen.get(key) == fp:
                    return
            fn(*a)
            if seen is not None:
                seen[key] = fp
        except Exception as e:      # one bad file never stops the rest
            rep["failed"].append(f"{key}: could not be read ({type(e).__name__}: {e}) — skipped")

    rep["_guard"] = guarded
    for name in ("predictions", "signals", "overrides", "logs", *RUN_DIRS):
        s = src_p / name
        if s.is_dir():
            _merge_tree(s, dest / name, name, rep)
    for name in ("config.tuned.yaml", "config.local.yaml.bak", ".env", ".env.txt"):
        if (src_p / name).is_file():
            guarded(_copy_file, name, src_p / name, dest / name, name, rep)
    if (src_p / "config.local.yaml").is_file():
        guarded(_merge_settings, "config.local.yaml", src_p / "config.local.yaml", dest / "config.local.yaml", rep)
    if (src_p / "regime_changes.yaml").is_file():
        guarded(_merge_regime, "regime_changes.yaml", src_p / "regime_changes.yaml", dest / "regime_changes.yaml", rep)
    if (src_p / LEGACY_FILE).is_file() and not legacy_code_folder(src_p):
        try:                                           # which code-folder files were already taken: keep them taken
            mp = dest / LEGACY_FILE
            cur = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
            inc = json.loads((src_p / LEGACY_FILE).read_text(encoding="utf-8"))
            for blob in (cur, inc):
                if blob and all(isinstance(v, str) for v in blob.values()):
                    flat = dict(blob)
                    blob.clear()
                    blob[str(REPO_ROOT.resolve())] = flat
            for root, m in inc.items():
                tgt = cur.setdefault(root, {})
                tgt.update({k: v for k, v in m.items() if k not in tgt})
            _atomic_write(mp, json.dumps(cur, indent=0, sort_keys=True))
        except (OSError, ValueError, AttributeError):
            pass
    ids = set()
    for f in (src_p / ID_FILE, src_p / KNOWN_FILE):    # an old data folder and what it knew: known, not "lost"
        try:
            if f.is_file():
                for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if line.startswith("id:"):
                        ids.add(line[3:])
                    elif line and ":" not in line:
                        ids.add(line)
        except OSError:
            pass
    rep["_ids"] = ids
    if not legacy:
        remember_ids(ids)
        rep.pop("_ids")
    rep.pop("_guard", None)
    rep.pop("_seen", None)
    if not legacy and (rep["added"] or rep["records added"]):
        auto_backup(cfg, "after-import")               # so what was just brought in is backed up right away
    if verbose:
        print(format_report(rep))
    return rep


def legacy_code_folder(p: Path) -> bool:
    return p.resolve() == REPO_ROOT.resolve()


def _fp(p: Path) -> str:
    st = p.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


def _merge_tree(s: Path, d: Path, top: str, rep: dict) -> None:
    shipped = SHIPPED_RUNS.get(top, set())
    for f in sorted(s.rglob("*")):
        if not f.is_file() or f.name in SKIP_NAMES or f.suffix == ".partial":
            continue
        rel = f.relative_to(s)
        if rel.parts[0] in shipped or f.name.endswith(DEV_PREDICTION_MARK):
            continue
        key = f"{top}/{rel.as_posix()}"
        if key in CSV_KEYS:
            rep["_guard"](_merge_csv, key, f, d / rel, CSV_KEYS[key], rep)
        elif key == "signals/alert_state.json":
            rep["_guard"](_merge_alert_state, key, f, d / rel, rep)
        else:
            rep["_guard"](_copy_file, key, f, d / rel, key, rep)


def _copy_file(f: Path, t: Path, key: str, rep: dict) -> None:
    if not t.exists():
        t.parent.mkdir(parents=True, exist_ok=True)
        tmp = t.with_name(t.name + ".partial")
        try:
            shutil.copy2(f, tmp)            # a read that fails half-way never leaves a truncated file behind
            os.replace(tmp, t)
        finally:
            if tmp.exists():
                tmp.unlink()
        rep["added"] += 1
        return
    if filecmp.cmp(f, t, shallow=False):
        rep["already there"] += 1
        return
    # Same name, different content. Frozen predictions / snapshots / CSVs are never replaced (yours stay). Text files
    # you may want to compare (lineups, logs, .env) are kept next to yours as "<name>.from-import".
    if t.suffix.lower() in (".yaml", ".yml", ".log", ".txt") or t.name in (".env", ".env.txt"):
        alt = t.with_name(t.name + ".from-import")
        if not alt.exists() or not filecmp.cmp(f, alt, shallow=False):
            shutil.copy2(f, alt)
            rep["notes"].append(f"{key}: you already have a different version; kept yours, the other is saved as {alt.name}")
    else:
        rep["notes"].append(f"{key}: you already have a different version; kept yours")


def _read_csv(p: Path) -> tuple[list[str], list[dict]]:
    with open(p, encoding="utf-8-sig", newline="") as fh:
        r = csv.DictReader(fh)
        return list(r.fieldnames or []), list(r)


def _merge_csv(f: Path, t: Path, key_cols, rep: dict) -> None:
    if not t.exists():
        _copy_file(f, t, str(t.name), rep)
        return
    tf, trows = _read_csv(t)
    sf, srows = _read_csv(f)
    fields = tf + [c for c in sf if c not in tf]

    def key(r):
        cols = key_cols or fields
        return tuple((r.get(c) or "") for c in cols)

    have = {key(r): i for i, r in enumerate(trows)}
    new, replaced = [], 0
    for r in srows:
        k = key(r)
        if k not in have:
            new.append(r)
        elif key_cols == ("game_id",) and (r.get("created_utc") or "") > (trows[have[k]].get("created_utc") or ""):
            trows[have[k]] = r          # same game graded in both: keep the later pre-kickoff prediction (grade's rule)
            replaced += 1
    if not new and not replaced:
        rep["already there"] += 1
        return
    if replaced:
        rep["notes"].append(f"{t.name}: {replaced} game(s) were graded in both places; kept the later locked-in "
                            "prediction for each")
    rows = trows + new
    if "ts_utc" in fields:
        rows.sort(key=lambda r: r.get("ts_utc") or "")
    elif "created_utc" in fields:
        rows.sort(key=lambda r: (r.get("season") or "", r.get("week") or "", r.get("created_utc") or ""))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    _atomic_write(t, buf.getvalue())
    rep["records added"] += len(new)


def _merge_alert_state(f: Path, t: Path, rep: dict) -> None:
    """Which alerts were already sent: short-lived state, taken only if there is none yet (merging old keys back in
    would re-send 'edge gone' pushes)."""
    if t.exists():
        return
    json.loads(f.read_text(encoding="utf-8"))          # must be valid
    t.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(f, t)


def _merge_settings(f: Path, t: Path, rep: dict) -> None:
    from .config import save_local
    try:
        src = yaml.safe_load(f.read_text(encoding="utf-8-sig")) or {}
        cur = (yaml.safe_load(t.read_text(encoding="utf-8-sig")) or {}) if t.exists() else {}
    except (OSError, yaml.YAMLError) as e:
        rep["notes"].append(f"config.local.yaml: could not read ({e}); skipped")
        return
    if not isinstance(src, dict) or not isinstance(cur, dict):
        rep["notes"].append("config.local.yaml: not a list of settings; skipped")
        return
    merged = deep_merge(src, cur)                    # your current settings win; imported ones fill the gaps
    if merged == cur:
        rep["already there"] += 1
        return
    save_local(merged)
    rep["added"] += 1
    rep["notes"].append("settings: brought in the setting changes you didn't have yet (where both had one, yours stayed)")


def _merge_regime(f: Path, t: Path, rep: dict) -> None:
    try:
        src = yaml.safe_load(f.read_text(encoding="utf-8")) or []
        cur = (yaml.safe_load(t.read_text(encoding="utf-8")) or []) if t.exists() else []
    except (OSError, yaml.YAMLError):
        return
    seen = {(str(c.get("team")).upper(), str(c.get("date"))) for c in cur if isinstance(c, dict)}
    new = [c for c in src if isinstance(c, dict) and c.get("team") and c.get("date")
           and (str(c["team"]).upper(), str(c["date"])) not in seen]
    if new:
        header = "".join(line + "\n" for line in REGIME_TEMPLATE.splitlines() if line.startswith("#"))
        _atomic_write(t, header + yaml.safe_dump(cur + new, sort_keys=False))
        rep["records added"] += len(new)


def _atomic_write(t: Path, text: str) -> None:
    t.parent.mkdir(parents=True, exist_ok=True)
    tmp = t.with_name(t.name + ".partial")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, t)


def format_report(rep: dict) -> str:
    if rep.get("error"):
        return f"could not bring in the data: {rep['error']}"
    def n(k, one, many):
        return f"{rep[k]} {one if rep[k] == 1 else many}"

    lines = [f"from {rep.get('source')}: {n('added', 'file', 'files')} added, "
             f"{n('records added', 'record', 'records')} added, {rep['already there']} already there"]
    lines += [f"  - {n}" for n in rep.get("notes", [])]
    if rep.get("failed"):
        lines.append(f"  {len(rep['failed'])} file(s) could not be read and were skipped (if the folder is on "
                     "OneDrive, make sure OneDrive is running and online, then bring it in again — that's safe):")
        lines += [f"  - {n}" for n in rep["failed"][:10]]
    return "\n".join(lines)


def fingerprint(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()[:12]
