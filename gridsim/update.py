"""In-app updates: is there a newer GridSim on GitHub, download its installer, and ask the launcher to run it.

Three steps, each as small as it can be (standard library only):

1. check()      asks https://api.github.com/repos/<REPO>/releases/latest whether a release newer than the running
                gridsim.__version__ exists and carries a finished asset named exactly GridSim-Setup.exe. The answer
                is remembered in <state>/update/check.json, so GitHub is asked at most every 12 hours (an hour after
                a failed attempt). It never raises and never says anything: no internet simply means "no update".
2. download()   fetches that installer to <state>/update/GridSim-Setup-<version>.exe through a .partial file. It only
                follows redirects to https://github.com and https://*.githubusercontent.com, and keeps the file only
                if its size, and its SHA-256 when GitHub publishes one, are what the release lists.
3. request_install()   writes <state>/update/pending.json. The LAUNCHER (gridsim.desktop) polls pending(); when it
                gets an answer it starts the installer, closes the window and exits. Nothing here starts a program.

<state> is gridsim.installed.state_dir(): the program's own folder, never the user's data folder.

The checksum proves the file is the one GitHub holds, not who put it there: whoever can publish a release in the
repository decides what the next "Update now" installs. That is why nothing is installed without a click.

The app uses the helpers at the bottom (cached / check_soon / start_download / job) so that no page ever waits for
the network: checks and downloads run in a background thread of the app's own process.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .installed import REPO, state_dir

ASSET_NAME = "GridSim-Setup.exe"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
CHECK_EVERY_H = 12.0          # GitHub is asked at most this often ...
RETRY_AFTER_H = 1.0           # ... or this long after an attempt that failed (no internet) or found a release half-made
CHECK_TIMEOUT_S = 6.0
DOWNLOAD_TIMEOUT_S = 30.0     # per network read, not for the whole file
PENDING_MAX_AGE_MIN = 15.0    # an install request older than this is dropped (a failed update must not repeat forever)
HANDOVER_DELAY_S = 4.0        # start_download(): time to read "GridSim closes now" before the launcher is told
_CHUNK = 1 << 18
_MAX_API_BYTES = 2_000_000

_CHECK_LOCK = threading.Lock()
_DOWNLOAD_LOCK = threading.Lock()
_JOB_LOCK = threading.Lock()
_JOB: dict = {"state": "idle"}
_LAST: dict = {}              # the last answer from GitHub, should check.json not be writable (a full or locked disk)
_CHECK_THREAD: threading.Thread | None = None
_JOB_THREAD: threading.Thread | None = None


class UpdateError(Exception):
    """An update step failed. The message is a plain sentence that can be shown to the user as it is."""


# ---- versions -------------------------------------------------------------------------------------------

_VERSION_RE = re.compile(r"[vV]?(\d{1,9}(?:\.\d{1,9}){0,5})")


def current() -> str:
    """The running version (read when asked, so a test can change it)."""
    import gridsim
    return gridsim.__version__


def version_tuple(text) -> tuple[int, ...] | None:
    """"v2026.11.12" or "2026.11.12" -> (2026, 11, 12). Anything else (letters, a suffix, nothing) -> None."""
    m = _VERSION_RE.fullmatch(text.strip()) if isinstance(text, str) else None
    if not m:
        return None
    nums = [int(p) for p in m.group(1).split(".")]
    while len(nums) > 1 and nums[-1] == 0:          # 2026.11.12.0 is 2026.11.12
        nums.pop()
    return tuple(nums)


def is_newer(candidate, than: str | None = None) -> bool:
    """True only when both are well-formed versions and candidate is the later one."""
    new, cur = version_tuple(candidate), version_tuple(current() if than is None else than)
    return bool(new and cur and new > cur)


def _canonical(version) -> str | None:
    t = version_tuple(version)
    return ".".join(str(n) for n in t) if t else None


# ---- small files in <state>/update ----------------------------------------------------------------------

def update_dir() -> Path:
    d = state_dir() / "update"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _check_file() -> Path:
    return update_dir() / "check.json"


def _pending_file() -> Path:
    return update_dir() / "pending.json"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_time(text) -> dt.datetime | None:
    try:
        when = dt.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)


def _read(path: Path) -> dict:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _write(path: Path, obj: dict) -> None:
    """Whole file or nothing (the launcher reads pending.json from another process while the app writes it)."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
        _replace(tmp, path)
    except OSError:
        _unlink(tmp)
        raise


def _replace(src: Path, dst: Path, tries: int = 8) -> None:
    """os.replace that waits a moment when Windows says the file is in use (a reader in the other process, or the
    virus scanner looking at a file that was just written)."""
    for attempt in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(0.15)


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


# ---- the network ----------------------------------------------------------------------------------------

def _https_host(url) -> str:
    """The host of a plain https address (no user name, no odd port), else ""."""
    try:
        u = urllib.parse.urlsplit(url)
        if u.scheme != "https" or u.username is not None or u.password is not None or u.port not in (None, 443):
            return ""
        return (u.hostname or "").lower()
    except (ValueError, TypeError, AttributeError):
        return ""


def _api_host_ok(url) -> bool:
    return _https_host(url) == "api.github.com"


def _download_host_ok(url) -> bool:
    host = _https_host(url)
    return host == "github.com" or host.endswith(".githubusercontent.com")


class _SafeRedirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to an address the caller allows; anything else stops the request before it is made."""

    def __init__(self, allowed):
        self.allowed = allowed

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not self.allowed(newurl):
            raise UpdateError("The download was sent to an address that is not GitHub's, so it was stopped.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _urlopen(req: urllib.request.Request, timeout: float, allowed):
    """The one place that touches the network (the tests replace it)."""
    return urllib.request.build_opener(_SafeRedirects(allowed)).open(req, timeout=timeout)


def _user_agent() -> str:
    return f"GridSim-Updater/{current()}"            # the program and its version: nothing about the PC or the user


# ---- 1. check -------------------------------------------------------------------------------------------

def _valid_info(info) -> dict | None:
    """The five fields of an update, checked (they are also read back from check.json), or None."""
    if not isinstance(info, dict):
        return None
    version, size, digest = _canonical(info.get("version")), info.get("size"), info.get("digest")
    page = info.get("page")
    if (version is None or _https_host(info.get("url")) != "github.com"
            or isinstance(size, bool) or not isinstance(size, int) or size <= 0):
        return None
    if not (isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", digest)):
        digest = None
    return {"version": version, "url": info["url"], "size": size, "digest": digest,
            "page": page if _https_host(page) == "github.com" else RELEASES_PAGE}


def _usable(release) -> tuple[dict | None, bool]:
    """(this release's installer or None, False when a newer release exists but its installer is not there yet)."""
    if not isinstance(release, dict):
        raise ValueError("the answer is not a release")
    tag = release.get("tag_name")
    if release.get("draft") or release.get("prerelease") or version_tuple(tag) is None:
        return None, True
    for asset in release.get("assets") or []:
        if isinstance(asset, dict) and asset.get("name") == ASSET_NAME:
            digest = asset.get("digest")
            info = _valid_info({"version": tag, "url": asset.get("browser_download_url"), "size": asset.get("size"),
                                "digest": digest.lower() if isinstance(digest, str) else None,
                                "page": release.get("html_url")})
            if asset.get("state") == "uploaded" and info:
                return info, True
            break
    return None, not is_newer(tag)                   # a newer release without its installer: look again soon


def _ask_github(old: dict) -> dict:
    """One request. Returns the new check.json content; a failure keeps what was known and is tried again sooner."""
    # "Has it changed since?" (ETag) is only asked about an answer this same version of GridSim wrote down.
    etag = old.get("etag") if isinstance(old.get("etag"), str) and old.get("by") == current() else None
    rec = {"checked_utc": _now().isoformat(timespec="seconds"), "ok": False, "wait_h": RETRY_AFTER_H, "etag": etag,
           "latest": _valid_info(old.get("latest")), "error": "", "by": current(),
           "tag": old.get("tag") if isinstance(old.get("tag"), str) else None}
    req = urllib.request.Request(API_URL, headers={
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": _user_agent()})
    if etag:
        req.add_header("If-None-Match", etag)
    token = os.environ.get("GRIDSIM_GITHUB_TOKEN", "").strip()      # CI only (runners share addresses and GitHub's
    if token:                                                         # hourly limit); never set on a user's PC
        req.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        with _urlopen(req, CHECK_TIMEOUT_S, _api_host_ok) as r:
            if getattr(r, "status", 200) != 200 or not _api_host_ok(r.geturl()):
                raise ValueError(f"unexpected answer {getattr(r, 'status', '?')} from {r.geturl()}")
            body = r.read(_MAX_API_BYTES + 1)
            new_etag = r.headers.get("ETag") if getattr(r, "headers", None) is not None else None
        if len(body) > _MAX_API_BYTES:
            raise ValueError("the answer is too long")
        release = json.loads(body.decode("utf-8"))
        latest, complete = _usable(release)
        tag = release.get("tag_name")
        rec.update(ok=True, latest=latest, tag=tag if isinstance(tag, str) else None,
                   etag=new_etag if complete and isinstance(new_etag, str) else None,
                   wait_h=CHECK_EVERY_H if complete else RETRY_AFTER_H)
    except urllib.error.HTTPError as e:
        if e.code == 304 and etag:                   # nothing changed since last time: what is remembered stands
            rec.update(ok=True, wait_h=CHECK_EVERY_H)
        elif e.code == 404:                          # no release has been published yet
            rec.update(ok=True, latest=None, tag=None, etag=None, wait_h=CHECK_EVERY_H)
        else:
            rec["error"] = f"GitHub answered {e.code}"
        e.close()
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"[:200]
    return rec


def _due(rec: dict) -> bool:
    when = _parse_time(rec.get("checked_utc"))
    if when is None:
        return True
    try:
        wait_h = min(CHECK_EVERY_H, max(RETRY_AFTER_H, float(rec.get("wait_h"))))
    except (TypeError, ValueError):
        wait_h = CHECK_EVERY_H
    age_h = (_now() - when).total_seconds() / 3600
    return age_h < 0 or age_h >= wait_h             # (a time in the future: the clock was changed)


def _newer_in(rec: dict) -> dict | None:
    info = _valid_info(rec.get("latest"))
    return info if info and is_newer(info["version"]) else None


def _remembered() -> dict:
    return _read(_check_file()) or dict(_LAST)


def check(force: bool = False) -> dict | None:
    """{"version", "url", "size", "digest", "page"} when a release newer than the running GridSim can be installed,
    else None. Asks GitHub only when the remembered answer is older than 12 hours (or force=True). Never raises."""
    global _LAST
    try:
        with _CHECK_LOCK:
            rec = _remembered()
            if force or _due(rec):
                rec = _LAST = _ask_github(rec)
                try:
                    _write(_check_file(), rec)
                except OSError:
                    pass                             # remembered in _LAST for as long as this process runs
                _tidy()
            return _newer_in(rec)
    except Exception:
        return None


def cached() -> dict | None:
    """What the last check found, without asking GitHub (one small file read). Never raises."""
    try:
        return _newer_in(_remembered())
    except Exception:
        return None


def status() -> dict:
    """The last check in a few words: {"state", "available", "listed", "current", "ok", "error", "checked_utc"}.
    state: "available" (a newer version can be installed: its number is in "available"), "newest", "not_ready" (a
    newer release is listed but its installer is not there yet), "none_published", or "unknown" (GitHub could not
    be asked and nothing newer is remembered). "listed" is the tag GitHub shows as the latest release."""
    try:
        rec = _remembered()
    except Exception:
        rec = {}
    newer = _newer_in(rec) if rec else None
    ok, listed = bool(rec.get("ok")), rec.get("tag") if isinstance(rec.get("tag"), str) else None
    state = ("available" if newer else "unknown" if not ok else "none_published" if listed is None
             else "not_ready" if is_newer(listed) else "newest")
    return {"state": state, "available": newer["version"] if newer else None, "listed": listed, "current": current(),
            "ok": ok, "error": str(rec.get("error") or ""), "checked_utc": rec.get("checked_utc")}


def _tidy() -> None:
    """Remove what earlier updates left behind: installers of versions that are no longer newer, old leftovers."""
    if not _DOWNLOAD_LOCK.acquire(blocking=False):   # a download is running: leave its files alone
        return
    try:
        day_ago = time.time() - 86400
        for f in update_dir().iterdir():
            m = re.fullmatch(r"GridSim-Setup-(.+)\.exe", f.name)
            if m and not is_newer(m.group(1)):
                _unlink(f)
            elif f.name.endswith((".partial", ".tmp")) and f.stat().st_mtime < day_ago:
                _unlink(f)
    except OSError:
        pass
    finally:
        _DOWNLOAD_LOCK.release()


# ---- 2. download ----------------------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _already_there(path: Path, size: int, sha: str | None) -> bool:
    try:
        return path.is_file() and path.stat().st_size == size and (sha is None or _sha256(path) == sha)
    except OSError:
        return False


def download(info: dict, progress=None) -> Path:
    """Download the installer described by check()'s answer and return its path. progress(done, total) is called
    as the bytes arrive. Raises UpdateError (a plain sentence) on any problem; a file that is not exactly what the
    release lists is deleted."""
    good = _valid_info(info)
    if good is None:
        raise UpdateError("The update information from GitHub is incomplete.")
    size, sha = good["size"], (good["digest"] or "sha256:")[7:] or None
    final = update_dir() / f"GridSim-Setup-{good['version']}.exe"
    part = final.with_name(final.name + ".partial")
    with _DOWNLOAD_LOCK:
        if _already_there(final, size, sha):         # downloaded and checked before (a second click)
            if progress:
                progress(size, size)
            return final
        for old in update_dir().iterdir():           # earlier downloads: each is well over 100 MB
            if old.name.startswith("GridSim-Setup-") and old.name.endswith((".exe", ".partial")):
                _unlink(old)
        try:
            if shutil.disk_usage(final.parent).free < size + (64 << 20):
                raise UpdateError(f"There is not enough free space on this PC for the update "
                                  f"(it needs about {size / 1e6:.0f} MB).")
        except OSError:
            pass
        try:
            _fetch(good["url"], part, size, sha, progress)
            try:
                _replace(part, final, tries=40)      # a virus scanner may hold a file this size for some seconds
            except OSError as e:
                raise _DiskError(e.strerror or str(e)) from e
        except BaseException as e:                   # also a page that is redrawn while its progress bar is updated
            _unlink(part)
            if isinstance(e, UpdateError) or not isinstance(e, Exception):
                raise
            if isinstance(e, _DiskError):
                raise UpdateError(f"This PC could not save the update ({e}).") from e
            raise UpdateError("GitHub could not be reached, or the connection dropped. Check the internet "
                              "connection and try again.") from e
    return final


class _DiskError(Exception):
    pass


def _fetch(url: str, part: Path, size: int, sha: str | None, progress) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": _user_agent(), "Accept": "application/octet-stream"})
    not_github = UpdateError("The download was sent to an address that is not GitHub's, so it was stopped.")
    wrong_size = UpdateError("The file is not the size GitHub lists for this update, so it was not kept.")
    try:
        response = _urlopen(req, DOWNLOAD_TIMEOUT_S, _download_host_ok)
    except urllib.error.HTTPError as e:
        e.close()
        raise UpdateError(f"GitHub did not hand out the update (it answered {e.code}).") from e
    with response as r:
        if not _download_host_ok(r.geturl()):
            raise not_github
        if getattr(r, "status", 200) != 200:
            raise UpdateError(f"GitHub did not hand out the update (it answered {r.status}).")
        announced = r.headers.get("Content-Length") if getattr(r, "headers", None) is not None else None
        if announced is not None and str(announced).strip() != str(size):
            raise wrong_size
        h, done = hashlib.sha256(), 0
        try:
            f = open(part, "wb")
        except OSError as e:
            raise _DiskError(e.strerror or str(e)) from e
        with f:
            while True:
                chunk = r.read(_CHUNK)
                if not chunk:
                    break
                done += len(chunk)
                if done > size:
                    raise wrong_size
                h.update(chunk)
                try:
                    f.write(chunk)
                except OSError as e:
                    raise _DiskError(e.strerror or str(e)) from e
                if progress:
                    progress(done, size)
    if done != size:
        raise UpdateError(f"The download stopped before it was complete ({done / 1e6:.0f} of {size / 1e6:.0f} MB).")
    if sha and h.hexdigest() != sha:
        raise UpdateError("The downloaded file does not match the checksum GitHub lists for this update, "
                          "so it was not kept.")


# ---- 3. hand-over to the launcher -----------------------------------------------------------------------

def request_install(path, version) -> None:
    """Ask the launcher to run this installer (it polls pending()). The page does not start anything itself."""
    p, canon = Path(os.path.abspath(path)), _canonical(version)
    if canon is None:
        raise UpdateError("The update has no usable version number.")
    if not p.is_file():
        raise UpdateError("The downloaded installer is no longer on this PC.")
    try:
        _write(_pending_file(), {"installer": str(p), "version": canon, "size": p.stat().st_size,
                                 "requested_utc": _now().isoformat(timespec="seconds")})
    except OSError as e:
        raise UpdateError(f"This PC could not save the request to install the update ({e.strerror or e}).") from e


def pending() -> dict | None:
    """{"installer", "version"} when an install was requested and can be started now, else None.
    A request is dropped (pending.json removed) when its version is not newer than the running GridSim (that update
    is done), when its installer is missing or not the size that was recorded, or when it is older than 15 minutes
    (so an update that failed is offered again in the app instead of being started at every launch). Never raises."""
    try:
        rec = _read(_pending_file())
        if not rec:
            return None
        installer, version = rec.get("installer"), _canonical(rec.get("version"))
        when = _parse_time(rec.get("requested_utc"))
        fresh = when is not None and abs((_now() - when).total_seconds()) <= PENDING_MAX_AGE_MIN * 60
        size = rec.get("size")
        p = Path(installer) if isinstance(installer, str) and installer else None
        if (p is not None and version and fresh and is_newer(version) and isinstance(size, int)
                and p.is_file() and p.stat().st_size == size):
            return {"installer": str(p), "version": version}
        clear_pending()
        if p is not None and version and not is_newer(version) and p.parent == update_dir() \
                and re.fullmatch(r"GridSim-Setup-.+\.exe", p.name):
            _unlink(p)                               # the installer of the version that now runs (kept if still in use)
    except Exception:
        pass
    return None


def clear_pending() -> None:
    """Take the install request away (the launcher does this before it starts the installer). Never raises."""
    try:
        _unlink(_pending_file())
    except OSError:
        pass


# ---- for the app: nothing below makes a page wait --------------------------------------------------------

def check_soon() -> bool:
    """Start check() in a background thread if the remembered answer is old enough. Returns at once."""
    global _CHECK_THREAD
    try:
        if (_CHECK_THREAD is not None and _CHECK_THREAD.is_alive()) or not _due(_remembered()):
            return False
        _CHECK_THREAD = threading.Thread(target=check, name="gridsim-update-check", daemon=True)
        _CHECK_THREAD.start()
        return True
    except Exception:
        return False


def job() -> dict:
    """The download the app started: {"state": "idle" | "downloading" | "ready" | "failed", "version", "done",
    "total", "error", "page", "age_s"} ("ready": the launcher has been, or is about to be, asked to install)."""
    with _JOB_LOCK:
        out = dict(_JOB)
    out["age_s"] = time.monotonic() - out.pop("since", time.monotonic())
    return out


def start_download(info: dict) -> bool:
    """Download in the background, then request_install(). False when a download is already on its way.
    After a download that failed, GitHub is asked once more what it lists (see _download_job)."""
    global _JOB_THREAD
    with _JOB_LOCK:
        if _JOB.get("state") == "downloading" or (_JOB.get("state") == "ready" and not _JOB.get("asked")):
            return False
        _JOB.clear()
        _JOB.update(state="downloading", version=str(info.get("version") or ""), done=0,
                    total=info.get("size") if isinstance(info.get("size"), int) else 0, error="",
                    page=info.get("page") or RELEASES_PAGE, since=time.monotonic())
        _JOB_THREAD = threading.Thread(target=_download_job, args=(dict(info),), name="gridsim-update-download",
                                       daemon=True)
        _JOB_THREAD.start()
    return True


def _download_job(info: dict) -> None:
    def progress(done: int, total: int) -> None:
        with _JOB_LOCK:
            _JOB.update(done=done, total=total)

    def failed(sentence: str) -> None:
        with _JOB_LOCK:
            _JOB.update(state="failed", error=sentence, since=time.monotonic())

    try:
        path = download(info, progress)
        with _JOB_LOCK:
            _JOB.update(state="ready", asked=False, since=time.monotonic())
        time.sleep(HANDOVER_DELAY_S)
        request_install(path, info["version"])
        with _JOB_LOCK:
            _JOB.update(asked=True)
    except UpdateError as e:
        failed(str(e))
        check(force=True)       # the release may have been replaced since it was last looked at: the next
                                # "Update now" then fetches what GitHub lists now (no internet: nothing changes)
    except Exception as e:
        failed(f"Something unexpected went wrong ({type(e).__name__}).")
