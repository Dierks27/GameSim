"""GridSim as one Windows program: a window of its own, no black console window, nothing left running afterwards.

The installed program's icon runs
    pythonw -m gridsim.desktop
which (1) makes sure only one GridSim runs (a second double-click brings the first window forward), (2) starts the
app's server hidden, on this PC only (or on the home network when display.phone_access is on), (3) shows it in a
window — WebView2 through pywebview; Microsoft Edge / Chrome "app" mode if that is not possible; the normal web
browser as the last resort — and (4) stops the server when that window is closed. While it runs it keeps
desktop.json in the program's state folder (installed.state_dir()); what it did is in desktop.log and what the
server printed in server.log, both in that folder. It also hands over to the installer when the app has downloaded
an update (gridsim.update.pending()).

Environment switches (for the automatic tests on GitHub; a user never needs them):
    GRIDSIM_SMOKE=1                    check yourself and close: no message box ever, exactly one kind of window
                                       (WebView2 unless GRIDSIM_WINDOW names another) and no falling back.
                                       Exit code 0 only if the app really came up in the WebView2 window and the
                                       server is gone afterwards; 1 the server did not start; 2 the window check
                                       failed; 3 the server still answered after closing. The other two kinds
                                       cannot look at the page: "edge" passes when the browser took its profile,
                                       "browser" when the page is handed out (no browser is opened).
    GRIDSIM_WINDOW=webview|edge|browser  start with this kind of window instead of choosing

Nothing here needs Windows to be imported: every Windows call sits inside a function, and the decisions (command
lines, flags, which window, the registry answer, the health check, the hand-over) are plain functions.
READ-ONLY towards Kalshi, like everything else; it never touches the data folder.
"""
from __future__ import annotations

import ctypes
import datetime as dt
import http.client
import importlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

from . import __version__
from .installed import APP_ID, REPO, install_root, is_installed, program_dir, python_exe, state_dir

TITLE = "GridSim"
BACKGROUND = "#0d0d0d"                 # the app's own background (.streamlit/config.toml): no white flash before it
LOOPBACK = "127.0.0.1"                 # never "localhost": on Windows that tries IPv6 first and can cost seconds
PHONE_PORT = 8501                      # phone access keeps the address people have bookmarked on their phone
MUTEX_NAME = "Local\\GridSim.Desktop"
WINDOW_KINDS = ("webview", "edge", "browser")

START_TIMEOUT_S = 120.0                # the server must answer within this (a first start after installing is slow)
PAGE_TIMEOUT_S = 60.0                  # the WebView2 window must have loaded the page within this, else Edge is used
SMOKE_RENDER_S = 180.0                 # self-check: the app must be on the page within this
SMOKE_BROWSER_S = 60.0                 # self-check of the Edge window: its profile lock must appear within this
EDGE_OPEN_S = 10.0                     # an Edge that ends sooner than this without using its profile never opened
WINDOW_WAIT_S = 90.0                   # a second double-click this long after the start expects a window to be there
CLOSE_GRACE_S = 20.0                   # window closed but its code does not come back: end from the side after this
UPDATE_POLL_S = 2.0
LOG_MAX_BYTES, LOG_KEEP_BYTES = 1_000_000, 200_000

EXIT_OK, EXIT_NO_SERVER, EXIT_NO_WINDOW, EXIT_STILL_RUNNING = 0, 1, 2, 3    # 2 and 3 only in self-check mode

# Windows process creation flags
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
# Job object: everything in it ends when the launcher ends; children may ask to be left out (the tune, autopilot)
JOB_KILL_ON_CLOSE, JOB_BREAKAWAY_OK, JOB_EXTENDED_LIMITS = 0x2000, 0x0800, 9
ERROR_ALREADY_EXISTS = 183

# With browser keys on, the window behaves like the Edge window GridSim used before: F5 reloads, Ctrl+F finds,
# Ctrl+P prints, right-click has a menu. (pywebview only offers this through its "debug" switch; the developer
# tools window it would also open is switched off below.)
BROWSER_KEYS = True

START_FAILED = ("GridSim could not start.\n\n"
                "Please try again. If it keeps happening, restart the PC. If that does not help, run "
                "GridSim-Setup.exe again: it repairs the program and leaves your data alone.\n\n"
                "The details are in this file:\n{log}")
START_FAILED_SOURCE = ("GridSim could not start.\n\n"
                       "This GridSim runs from its own folder (it was not installed with GridSim-Setup.exe): "
                       "run \"Setup GridSim\" in that folder again, then start it once more.\n\n"
                       "The details are in this file:\n{log}")
UPDATE_FAILED = ("The update could not be started, so GridSim stays as it is.\n\n"
                 "You can try again from the update message in GridSim, or download the new version here:\n"
                 "https://github.com/" + REPO + "/releases/latest")
IN_BROWSER = "GridSim is open in your web browser.\n\nClick OK to stop GridSim."


# ---- log ------------------------------------------------------------------------------------------------------------

_LOG = None


def trim_log(path: Path, max_bytes: int = LOG_MAX_BYTES, keep_bytes: int = LOG_KEEP_BYTES) -> None:
    """Keep a log from growing for ever: past max_bytes only its newest part stays."""
    try:
        if path.stat().st_size <= max_bytes:
            return
        with open(path, "rb") as fh:
            fh.seek(-keep_bytes, os.SEEK_END)
            tail = fh.read()
        tail = tail[tail.find(b"\n") + 1:]                       # start at a whole line
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(b"(older lines were removed to keep this file small)\n" + tail)
        try:
            os.replace(tmp, path)
        except OSError:                  # Windows: another GridSim process has the log open; it stays as it is
            tmp.unlink(missing_ok=True)
    except OSError:
        pass


def _open_log():
    global _LOG
    path = state_dir() / "desktop.log"
    if _LOG is None or _LOG.closed or _LOG.name != str(path):
        trim_log(path)
        _LOG = open(path, "a", buffering=1, encoding="utf-8", errors="backslashreplace")
    return _LOG


def log(text: str) -> None:
    try:
        _open_log().write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")
    except (OSError, ValueError):
        pass


def guard_stdio() -> None:
    """pythonw.exe has no stdout, stderr or stdin (they are None), and libraries that print then fail. Point them
    at desktop.log, so that whatever is printed (also a traceback) can be read afterwards."""
    if sys.stdout is not None and sys.stderr is not None and sys.stdin is not None:
        return
    try:
        fh = _open_log()
    except OSError:
        fh = open(os.devnull, "w")
    if sys.stdout is None:
        sys.stdout = fh
    if sys.stderr is None:
        sys.stderr = fh
    if sys.stdin is None:
        sys.stdin = open(os.devnull, "r")


if sys.stdout is None or sys.stderr is None or sys.stdin is None:      # before any import that may print
    guard_stdio()


def smoke_mode() -> bool:
    return os.environ.get("GRIDSIM_SMOKE", "").strip() == "1"


def message_box(text: str, flags: int = 0x40) -> None:
    """A plain Windows message box (0x40 = information icon, OK button), in front of the other windows: the
    launcher has no window of its own for it to belong to. Never in self-check mode."""
    log("message: " + text.replace("\n", " | "))
    if smoke_mode():
        return
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(None, text, TITLE, flags | 0x00010000)      # MB_SETFOREGROUND
        except Exception:
            log(traceback.format_exc())
    else:
        print(text, file=sys.stderr)


def tail_text(path: Path, since: int = 0, max_bytes: int = 6000) -> str:
    """The end of a log file (from byte `since` on), for the report of what went wrong."""
    try:
        with open(path, "rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            fh.seek(max(since if since <= size else 0, size - max_bytes))
            return fh.read().decode("utf-8", errors="backslashreplace")
    except OSError:
        return ""


# ---- the server -----------------------------------------------------------------------------------------------------

class ServerFailed(Exception):
    """The app's server did not come up; .detail is the end of server.log."""

    def __init__(self, what: str, detail: str = ""):
        super().__init__(what)
        self.detail = detail


def phone_access() -> bool:
    """display.phone_access from config.yaml / the user's settings; False on any doubt (private to this PC)."""
    try:
        from .config import load_config
        return bool((load_config().raw.get("display") or {}).get("phone_access", False))
    except Exception:
        log("could not read the settings; phone access stays off\n" + traceback.format_exc())
        return False


def free_port(host: str = LOOPBACK) -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)       # (no SO_REUSEADDR: on Windows that allows sharing)
    try:
        s.bind((host, 0))
        return s.getsockname()[1]
    finally:
        s.close()


def port_in_use(port: int, timeout: float = 0.5) -> bool:
    """Does something on this PC answer on the port?"""
    try:
        with socket.create_connection((LOOPBACK, port), timeout=timeout):
            return True
    except OSError:
        return False


def choose_address(phone: bool, in_use=port_in_use, pick=free_port) -> tuple[str, int]:
    """Where the server listens: this PC only on a free port, or — phone access — the home network on port 8501
    (another port when 8501 is taken, for instance by a GridSim started the old way)."""
    if not phone:
        return LOOPBACK, pick(LOOPBACK)
    if not in_use(PHONE_PORT):
        return "0.0.0.0", PHONE_PORT
    port = pick("0.0.0.0")
    log(f"port {PHONE_PORT} is already in use on this PC; using {port} (the phone needs this number this time)")
    return "0.0.0.0", port


def server_command(port: int, host: str = LOOPBACK, python: str | None = None,
                   program: Path | None = None) -> list[str]:
    """python.exe (not pythonw: the server gets a hidden console that its own helpers inherit, so nothing flashes)
    -m streamlit run ... with every setting that matters given here, where it beats any config.toml."""
    program = Path(program) if program else program_dir()
    return [python or python_exe(), "-m", "streamlit", "run", str(program / "app" / "streamlit_app.py"),
            "--global.developmentMode", "false",
            "--server.headless", "true",
            "--server.address", host,
            "--server.port", str(port),
            "--server.fileWatcherType", "none",
            "--server.runOnSave", "false",
            "--browser.gatherUsageStats", "false",
            "--browser.serverAddress", LOOPBACK,
            "--client.toolbarMode", "minimal"]


def server_env(base: dict | None = None) -> dict:
    """The server's output goes to a file: without these Windows would write it in the PC's old code page (an arrow
    or an accent then stops the program) and hold it back in a buffer (a crash would leave the log empty)."""
    return dict(os.environ if base is None else base, PYTHONIOENCODING="utf-8:backslashreplace", PYTHONUNBUFFERED="1")


def hidden_flags(windows: bool | None = None) -> dict:
    windows = sys.platform == "win32" if windows is None else windows
    return {"creationflags": CREATE_NO_WINDOW} if windows else {}


def start_server(port: int, host: str = LOOPBACK, popen=subprocess.Popen):
    """Start the server as a hidden child; returns (process, where its part of server.log begins)."""
    path = state_dir() / "server.log"
    trim_log(path)
    with open(path, "ab") as fh:
        since = fh.seek(0, os.SEEK_END)
        head = f"\n=== {dt.datetime.now():%Y-%m-%d %H:%M:%S} GridSim {__version__} server on {host}:{port}\n"
        fh.write(head.encode())
        fh.flush()
        proc = popen(server_command(port, host), cwd=str(program_dir()), env=server_env(),
                     stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT, **hidden_flags())
    return proc, since


def health_ok(port: int, timeout: float = 2.0) -> bool:
    """/_stcore/health must answer 200 with the body "ok" (other addresses answer 200 too, so the body counts).
    http.client and the literal address: no proxy settings, no name lookup."""
    try:
        conn = http.client.HTTPConnection(LOOPBACK, port, timeout=timeout)
        try:
            conn.request("GET", "/_stcore/health")
            r = conn.getresponse()
            return r.status == 200 and r.read(16).strip() == b"ok"
        finally:
            conn.close()
    except (OSError, http.client.HTTPException):
        return False


def page_served(port: int, timeout: float = 5.0) -> bool:
    """The app's page itself is handed out (what a browser would load first)."""
    try:
        conn = http.client.HTTPConnection(LOOPBACK, port, timeout=timeout)
        try:
            conn.request("GET", "/")
            r = conn.getresponse()
            return r.status == 200 and b"<html" in r.read(4000).lower()
        finally:
            conn.close()
    except (OSError, http.client.HTTPException):
        return False


def wait_healthy(port: int, alive, timeout: float = START_TIMEOUT_S, interval: float = 0.25) -> str:
    """Wait for the server: "ok", "died" (the process ended: no point in waiting on) or "timeout"."""
    end = time.monotonic() + timeout
    while True:
        if not alive():
            return "died"
        if health_ok(port):
            return "ok"
        if time.monotonic() >= end:
            return "timeout"
        time.sleep(interval)


class Session:
    """What one run of the launcher holds."""

    def __init__(self):
        self.proc = None                    # the server
        self.job = None                     # the job object that ends it with us (Windows)
        self.host, self.port = LOOPBACK, 0
        self.window = ""                    # webview | edge | browser
        self.started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        self.closers: list = []             # ways to close the window from another thread (the update hand-over)
        self.stop = threading.Event()       # set when the launcher is to end
        self.cleaned = threading.Event()
        self.handoff = False
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        return f"http://{LOOPBACK}:{self.port}"


def bring_up_server(st: Session, phone: bool = False, timeout: float = START_TIMEOUT_S) -> None:
    """Start the server and wait until it answers. A port that turned out to be taken is tried again with another
    one (up to three starts); anything else raises ServerFailed with the end of server.log."""
    for attempt in (1, 2, 3):
        # (phone access: 8501 was refused although nothing answered there -> any free port, not 8501 once more)
        st.host, st.port = choose_address(phone) if attempt == 1 or not phone else ("0.0.0.0", free_port("0.0.0.0"))
        st.job = new_job()
        st.proc, since = start_server(st.port, st.host)
        if st.job:
            try:
                assign_to_job(st.job, st.proc.pid)
            except Exception as e:           # still works; closing then uses taskkill
                log(f"the server could not be put in the job object ({e}); closing will use taskkill")
                close_job(st.job)
                st.job = None
        t0 = time.monotonic()
        outcome = wait_healthy(st.port, lambda: st.proc.poll() is None, timeout)
        if outcome == "ok":
            log(f"server ready on {st.host}:{st.port} after {time.monotonic() - t0:.1f} s (pid {st.proc.pid})")
            return
        detail = tail_text(state_dir() / "server.log", since)
        stop_server(st.proc, st.job)
        st.proc = st.job = None
        if outcome == "died" and "is not available" in detail and attempt < 3:
            log(f"port {st.port} was taken after all; trying another one")
            continue
        raise ServerFailed("the server stopped while starting" if outcome == "died"
                           else f"the server did not answer within {timeout:.0f} seconds", detail)


# ---- Windows: job object, single instance, bringing a window forward ------------------------------------------

class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes",
                                               "OtherBytes")]


class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32)]


class JobLimits(ctypes.Structure):
    """JOBOBJECT_EXTENDED_LIMIT_INFORMATION (144 bytes on 64-bit Windows)."""
    _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _last_error() -> int:
    return getattr(ctypes, "get_last_error", lambda: 0)()


_K32 = None
_U32 = None


def _kernel32():
    """kernel32 with the argument and return types spelled out (the default return type cuts 64-bit handles)."""
    global _K32
    if _K32 is None:
        from ctypes import wintypes as w
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.argtypes, k.CreateJobObjectW.restype = (w.LPVOID, w.LPCWSTR), w.HANDLE
        k.SetInformationJobObject.argtypes = (w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD)
        k.SetInformationJobObject.restype = w.BOOL
        k.AssignProcessToJobObject.argtypes, k.AssignProcessToJobObject.restype = (w.HANDLE, w.HANDLE), w.BOOL
        k.TerminateJobObject.argtypes, k.TerminateJobObject.restype = (w.HANDLE, w.UINT), w.BOOL
        k.OpenProcess.argtypes, k.OpenProcess.restype = (w.DWORD, w.BOOL, w.DWORD), w.HANDLE
        k.CloseHandle.argtypes, k.CloseHandle.restype = (w.HANDLE,), w.BOOL
        k.CreateMutexW.argtypes, k.CreateMutexW.restype = (w.LPVOID, w.BOOL, w.LPCWSTR), w.HANDLE
        _K32 = k
    return _K32


def _user32():
    global _U32
    if _U32 is None:
        from ctypes import wintypes as w
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.EnumWindows.argtypes, u.EnumWindows.restype = (ctypes.c_void_p, w.LPARAM), w.BOOL
        u.IsWindowVisible.argtypes, u.IsWindowVisible.restype = (w.HWND,), w.BOOL
        u.GetWindowTextW.argtypes, u.GetWindowTextW.restype = (w.HWND, w.LPWSTR, ctypes.c_int), ctypes.c_int
        u.GetClassNameW.argtypes, u.GetClassNameW.restype = (w.HWND, w.LPWSTR, ctypes.c_int), ctypes.c_int
        u.GetWindowThreadProcessId.argtypes = (w.HWND, ctypes.POINTER(w.DWORD))
        u.GetWindowThreadProcessId.restype = w.DWORD
        u.IsIconic.argtypes, u.IsIconic.restype = (w.HWND,), w.BOOL
        u.ShowWindow.argtypes, u.ShowWindow.restype = (w.HWND, ctypes.c_int), w.BOOL
        u.SetForegroundWindow.argtypes, u.SetForegroundWindow.restype = (w.HWND,), w.BOOL
        _U32 = u
    return _U32


def create_job(k32=None):
    """A job object whose processes end when its last handle closes — so also when the launcher crashes or is
    ended in Task Manager. BREAKAWAY_OK lets the tune and autopilot ask to be left out."""
    k32 = k32 or _kernel32()
    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(f"CreateJobObject failed (Windows error {_last_error()})")
    info = JobLimits()
    info.BasicLimitInformation.LimitFlags = JOB_KILL_ON_CLOSE | JOB_BREAKAWAY_OK
    if not k32.SetInformationJobObject(job, JOB_EXTENDED_LIMITS, ctypes.byref(info), ctypes.sizeof(info)):
        err = _last_error()
        k32.CloseHandle(job)
        raise OSError(f"SetInformationJobObject failed (Windows error {err})")
    return job


def assign_to_job(job, pid: int, k32=None) -> None:
    """Only the server goes in the job — never the launcher itself (an Edge window would then be in it too)."""
    k32 = k32 or _kernel32()
    handle = k32.OpenProcess(0x0001 | 0x0100, False, pid)            # PROCESS_TERMINATE | PROCESS_SET_QUOTA
    if not handle:
        raise OSError(f"OpenProcess failed (Windows error {_last_error()})")
    try:
        if not k32.AssignProcessToJobObject(job, handle):
            raise OSError(f"AssignProcessToJobObject failed (Windows error {_last_error()})")
    finally:
        k32.CloseHandle(handle)


def close_job(job, k32=None) -> None:
    try:
        (k32 or _kernel32()).CloseHandle(job)
    except Exception:
        pass


def new_job():
    """The job object for this start, or None (not Windows, or it could not be made: closing then uses taskkill)."""
    if sys.platform != "win32":
        return None
    try:
        return create_job()
    except Exception as e:
        log(f"no job object ({e}); closing will use taskkill")
        return None


def stop_server(proc, job=None, k32=None, run=subprocess.run, windows: bool | None = None) -> None:
    """End the server and everything it started (except what asked to be left out of the job)."""
    if proc is None:
        return
    windows = sys.platform == "win32" if windows is None else windows
    try:
        if job:
            k32 = k32 or _kernel32()
            k32.TerminateJobObject(job, 0)
            k32.CloseHandle(job)
        elif windows:
            run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=30, **hidden_flags(True))
        else:
            proc.terminate()
    except Exception:
        log("stopping the server:\n" + traceback.format_exc())
    try:
        proc.wait(10)
    except Exception:
        try:
            proc.kill()
            proc.wait(5)
        except Exception:
            pass


_MUTEX = None


def single_instance(k32=None) -> bool:
    """True when this is the only GridSim window program running (the named mutex is kept until we end)."""
    global _MUTEX
    k32 = k32 or _kernel32()
    getattr(ctypes, "set_last_error", lambda v: None)(0)
    _MUTEX = k32.CreateMutexW(None, False, MUTEX_NAME)
    return _last_error() != ERROR_ALREADY_EXISTS


def find_window(user32, wanted):
    """The first visible top-level window for which wanted(title, owner_pid, class_name) is true, or None."""
    from ctypes import wintypes
    found = []

    def each(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return 1
        title, cls = ctypes.create_unicode_buffer(512), ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 512)
        user32.GetClassNameW(hwnd, cls, 256)
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if wanted(title.value, pid.value, cls.value):
            found.append(hwnd)
            return 0                                                # stop looking
        return 1

    proto = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    callback = proto(each)
    user32.EnumWindows(callback, 0)
    return found[0] if found else None


def focus_existing(info: dict, user32=None) -> bool:
    """Bring the running GridSim's window forward: the launcher's own window (WebView2, or the "Click OK to stop"
    box), else an Edge / Chrome window that shows GridSim."""
    user32 = user32 or _user32()
    pid = int(info.get("pid") or 0)
    hwnd = (find_window(user32, lambda title, owner, cls: owner == pid and title == TITLE)
            or find_window(user32, lambda title, owner, cls: cls.startswith("Chrome_WidgetWin")
                           and "gridsim" in title.lower()))
    if not hwnd:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)                                  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    return True


def second_launch(smoke: bool = False) -> int:
    """GridSim is already running: show its window and leave. Always 0.
    No window to show although the app is being served: it is opened in the web browser — at once when the running
    GridSim uses a browser window (it was closed), and for the WebView2 window only when that should have been
    there long ago (while GridSim is still starting, a second double-click must not add a browser tab). So a
    double-click never ends with nothing on the screen for good."""
    info = read_desktop_json() or {}
    shown = False
    try:
        shown = focus_existing(info)
    except Exception:
        log("bringing the window forward:\n" + traceback.format_exc())
    log(f"GridSim is already running (pid {info.get('pid')}); "
        f"its window was {'brought forward' if shown else 'not found'}")
    try:
        port = int(info.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    overdue = desktop_json_age() > WINDOW_WAIT_S
    if not shown and not smoke and port and (info.get("window") in ("edge", "browser") or overdue) \
            and health_ok(port):
        log(f"opening http://{LOOPBACK}:{port} in the web browser instead")
        try:
            webbrowser.open(f"http://{LOOPBACK}:{port}")
        except Exception:
            log(traceback.format_exc())
    return EXIT_OK


def only_instance(windows: bool | None = None) -> bool:
    """True when this start is the one GridSim window program (always, where there is no Windows mutex). What a
    GridSim that was ended from outside (Task Manager, a power cut) left in desktop.json is taken away then: until
    this start has its own server, nobody is to read the old one's port."""
    windows = sys.platform == "win32" if windows is None else windows
    if not windows:
        return True
    try:
        only = single_instance()
    except Exception:
        log("single instance check:\n" + traceback.format_exc())
        only = True
    if only:
        remove_desktop_json()
    return only


def set_app_id() -> None:
    """Before any window: the taskbar then groups GridSim under its own icon and name (same id as the shortcut)."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        log("AppUserModelID:\n" + traceback.format_exc())


def screen_size() -> tuple[int, int]:
    """The main screen in the units windows are sized in (pixels at 100 % scaling); (0, 0) when unknown."""
    if sys.platform != "win32":
        return 0, 0
    try:
        u = ctypes.windll.user32
        w, h = int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
        try:
            dpi = int(u.GetDpiForSystem()) or 96
        except Exception:
            dpi = 96
        return w * 96 // dpi, h * 96 // dpi
    except Exception:
        return 0, 0


def window_geometry(screen_w: int, screen_h: int) -> dict:
    """1400 x 900 where that fits; on a smaller screen (most laptops at 125 % or 150 % scaling) a window that fits,
    opened maximised — never one whose title bar ends up off the screen."""
    if screen_w <= 0 or screen_h <= 0:
        return {"width": 1100, "height": 700, "min_size": (800, 600), "maximized": True}
    w, h = max(min(1400, screen_w - 40), 640), max(min(900, screen_h - 80), 480)
    return {"width": w, "height": h, "min_size": (min(1000, w), min(700, h)), "maximized": w < 1400 or h < 900}


# ---- which window ---------------------------------------------------------------------------------------------------

WEBVIEW2_CLIENT = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def _major(version: str) -> int:
    try:
        return int(str(version).split(".")[0])
    except ValueError:
        return 0


def webview2_version(winreg) -> str | None:
    """The WebView2 Runtime's version from the registry (the places Microsoft documents: per user, and for the whole
    PC), or None. The same test pywebview 6.2 makes — and when its test fails it quietly uses Internet Explorer's
    engine instead, which shows GridSim as a blank page; so we ask first and never let it choose."""
    for hive, path in ((winreg.HKEY_CURRENT_USER, "SOFTWARE\\Microsoft\\EdgeUpdate\\Clients\\" + WEBVIEW2_CLIENT),
                       (winreg.HKEY_LOCAL_MACHINE,
                        "SOFTWARE\\WOW6432Node\\Microsoft\\EdgeUpdate\\Clients\\" + WEBVIEW2_CLIENT)):
        try:
            with winreg.OpenKey(hive, path) as key:
                pv = str(winreg.QueryValueEx(key, "pv")[0])
        except OSError:
            continue
        if _major(pv) >= 86:                                        # (also rules out "" and "0.0.0.0")
            return pv
    return None


def dotnet_release(winreg) -> int:
    """The .NET Framework 4.x release number (pywebview's window needs 394802 = 4.6.2 or newer); 0 when absent."""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\Microsoft\\NET Framework Setup\\NDP\\v4\\Full") as k:
            return int(winreg.QueryValueEx(k, "Release")[0])
    except (OSError, ValueError, TypeError):
        return 0


def webview_ready(winreg=None, have_module: bool | None = None) -> tuple[bool, str]:
    """Can the WebView2 window be used? (yes/no, why) — for the choice and for the log."""
    if have_module is None:
        have_module = importlib.util.find_spec("webview") is not None
    if not have_module:
        return False, "pywebview is not installed"
    if winreg is None:
        try:
            import winreg
        except ImportError:
            return False, "not Windows"
    pv = webview2_version(winreg)
    if not pv:
        return False, "the WebView2 Runtime was not found in the registry"
    rel = dotnet_release(winreg)
    if rel < 394802:
        return False, f".NET Framework 4.6.2 or newer was not found (release {rel})"
    return True, f"WebView2 Runtime {pv}, .NET Framework release {rel}"


def window_paths(forced: str = "", smoke: bool = False, webview_ok: bool = False, windows: bool = True) -> list[str]:
    """The kinds of window to try, in order. Normally: WebView2 when it is there, then Edge/Chrome, then the web
    browser. GRIDSIM_WINDOW starts further down that list. The self-check tries exactly one kind (no falling back:
    a pass must mean that kind worked). WebView2 is never tried when it is not there."""
    if not windows:
        return ["browser"]
    order = list(WINDOW_KINDS)
    first = forced if forced in order else "webview"
    paths = [first] if smoke else order[order.index(first):]
    return [p for p in paths if p != "webview" or webview_ok]


def icon_path() -> str | None:
    """The installed program's icon — only when it really is a .ico file: anything else (a renamed PNG) would
    make the window's own code fail where nothing can catch it."""
    root = install_root()
    ico = root / "gridsim.ico" if root else None
    try:
        if ico and ico.read_bytes()[:4] == b"\x00\x00\x01\x00":
            return str(ico)
    except OSError:
        pass
    return None


def webview_note() -> Path:
    return state_dir() / "webview.starting"


def _drop_note() -> None:
    try:
        webview_note().unlink(missing_ok=True)
    except OSError:
        pass


def webview_died_last_time() -> bool:
    """show_webview leaves a note while the WebView2 window is being opened and takes it away once the window is
    there. A note that is still there at the next start means the program ended right then — the one failure that
    shows nothing and can report nothing. The note is taken away here, so the start after this one tries again."""
    if not webview_note().exists():
        return False
    _drop_note()
    return True


def plan_windows(forced: str = "", smoke: bool = False, windows: bool = True) -> tuple[list[str], str]:
    """(the kinds of window to try, what was found out about WebView2 — for the log)."""
    ok, why = webview_ready() if windows else (False, "not Windows")
    died = webview_died_last_time()
    if ok and died and not smoke and not forced:
        ok, why = False, "last time GridSim ended while this window was being opened; the next kind is used this once"
    return window_paths(forced, smoke, ok, windows), why


def _quiet(fn) -> None:
    try:
        fn()
    except Exception:
        log(traceback.format_exc())


def end_after_close(st: Session, grace: float = CLOSE_GRACE_S, leave=os._exit) -> None:
    """The window is closed. Normally the main thread now stops the server and ends the program; should the
    window's own code never hand control back (the .NET side hanging), do it from here — otherwise an invisible
    GridSim would stay behind and no new one could be started."""
    if st.cleaned.wait(grace):
        return
    log(f"the window is closed but its code did not return within {grace:.0f} s; stopping the server from here")
    cleanup(st)
    log("--- GridSim closed")
    leave(EXIT_OK)


STAPP_JS = "!!document.querySelector('[data-testid=\"stApp\"]')"
CONTENT_JS = "document.querySelectorAll('[data-testid=\"stMain\"] *').length"


def show_webview(url: str, st: Session, smoke: bool = False) -> bool:
    """GridSim in a WebView2 window (pywebview). Blocks until the window is closed; must run on the main thread.
    True: the window was there and is closed now (self-check: and the app was on the page, drawn by WebView2).
    False: it could not be shown — the caller goes on to the next kind of window."""
    os.environ.setdefault("PYWEBVIEW_LOG", "INFO")      # BROWSER_KEYS must not switch its debug logging on
    import webview
    for key, value in (("ALLOW_DOWNLOADS", True), ("OPEN_DEVTOOLS_IN_DEBUG", False)):
        try:
            webview.settings[key] = value               # downloads: st.download_button does nothing otherwise
        except Exception:
            log(f"pywebview has no setting {key}")
    geo = window_geometry(*screen_size())
    win = webview.create_window(TITLE, url, width=geo["width"], height=geo["height"], min_size=geo["min_size"],
                                maximized=geo["maximized"], text_select=True, zoomable=True,
                                background_color=BACKGROUND)
    result = {"loaded": False, "rendered": False, "error": ""}
    st.closers.append(lambda: _quiet(win.destroy))
    icon = icon_path()
    if icon is None and is_installed():
        log("webview: no usable gridsim.ico next to the program; the window shows Python's icon")

    def session():                                      # on a worker thread, once the window exists
        renderer = getattr(webview, "renderer", None)
        log(f"webview: renderer {renderer}")
        if renderer not in (None, "edgechromium") or (smoke and renderer != "edgechromium"):
            result["error"] = f"the window is not drawn by WebView2 (renderer {renderer})"
            return _quiet(win.destroy)
        end = time.monotonic() + PAGE_TIMEOUT_S
        while not win.events.loaded.wait(0.5):
            if win.events.shown.is_set():
                _drop_note()                            # the window is there: opening it did not end us
            if win.events.closed.is_set() or st.stop.is_set():
                return None
            if time.monotonic() > end:
                result["error"] = f"the page did not load within {PAGE_TIMEOUT_S:.0f} seconds"
                return _quiet(win.destroy)
        result["loaded"] = True
        _drop_note()
        log("webview: page loaded")
        if not smoke:                                   # a normal run: stay until the window is closed
            while not (win.events.closed.wait(1.0) or st.stop.is_set()):
                pass
            return end_after_close(st)
        end, last = time.monotonic() + SMOKE_RENDER_S, None
        while time.monotonic() < end and not win.events.closed.is_set():
            try:
                if win.evaluate_js(STAPP_JS):
                    result["rendered"] = True
                    break
            except Exception as e:
                last = e
            time.sleep(0.5)
        log(f"smoke: app on the page: {result['rendered']}" + (f" (last error: {last})" if last else ""))
        if result["rendered"]:
            try:
                time.sleep(3)
                log(f"smoke: elements in the main part of the page after 3 s: {win.evaluate_js(CONTENT_JS)}")
            except Exception as e:
                log(f"smoke: could not count the page's elements ({e})")
        return _quiet(win.destroy)

    try:
        webview_note().write_text(__version__, encoding="utf-8")
    except OSError:
        pass
    try:
        webview.start(session, gui="edgechromium", debug=BROWSER_KEYS, icon=icon, private_mode=False,
                      storage_path=str(state_dir() / "webview"))
    except Exception:
        log("webview: could not show the window\n" + traceback.format_exc())
        return False
    finally:
        _drop_note()
    if not result["error"] and not result["loaded"] and not win.events.shown.is_set() and not st.stop.is_set():
        result["error"] = "the window never appeared"
    if result["error"]:
        log("webview: " + result["error"])
        return False
    return result["rendered"] if smoke else True


def browser_command(exe: str, url: str, profile: Path, geo: dict | None = None) -> list[str]:
    """Edge / Chrome as an "app" window. Its own profile folder always: with the user's profile and their Edge
    already open, the new process hands over and ends at once, which would look like "window closed"."""
    geo = geo or window_geometry(0, 0)
    cmd = [exe, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
           "--disable-background-mode", "--disable-sync", "--disable-extensions", "--hide-crash-restore-bubble",
           f"--window-size={geo['width']},{geo['height']}", "--do-not-de-elevate", "--edge-skip-compat-layer-relaunch"]
    return cmd + (["--start-maximized"] if geo.get("maximized") else [])


def show_edge(url: str, st: Session, smoke: bool = False, popen=subprocess.Popen, sleep=time.sleep,
              now=time.monotonic) -> bool:
    """GridSim in an Edge / Chrome app window. Blocks until that window is closed: the browser process we started
    has ended AND nothing holds the profile's lock file any more (Edge sometimes restarts itself under another
    process number, so waiting for our own child alone is not enough)."""
    from .appwindow import browser_exe
    exe = browser_exe()
    if not exe:
        log("edge: neither Microsoft Edge nor Chrome was found")
        return False
    profile = state_dir() / "browser-profile"
    lock = profile / "lockfile"
    proc = popen(browser_command(exe, url, profile, window_geometry(*screen_size())), stdin=subprocess.DEVNULL,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    st.closers.append(lambda: _quiet(proc.terminate))
    log(f"edge: started {exe} (pid {getattr(proc, 'pid', '?')})")
    t0, seen, ended = now(), False, None                  # ended: when the process we started was first seen gone
    while not st.stop.is_set():
        locked = lock.exists()
        seen = seen or locked
        age = now() - t0
        if ended is None and proc.poll() is not None:
            ended = age
        if smoke:
            if seen or age > SMOKE_BROWSER_S:
                break
        elif ended is not None and not locked and age > (2 if seen else EDGE_OPEN_S):
            break                                        # (never seen: a browser that restarted itself gets time)
        sleep(0.5)
    age = now() - t0
    if smoke:
        log(f"smoke: edge profile lock seen: {seen}; the process we started is "
            f"{'still running' if proc.poll() is None else 'gone'} after {age:.0f} s")
        _quiet(proc.terminate)
        end = now() + 15
        while lock.exists() and now() < end:
            sleep(0.5)
        log(f"smoke: edge profile lock {'still there' if lock.exists() else 'released'} after closing")
        return seen
    if not seen and ended is not None and ended < EDGE_OPEN_S and not st.stop.is_set():
        log(f"edge: the browser ended after {ended:.0f} s without ever using its profile; it did not open")
        return False
    return True


def show_browser(url: str, st: Session, smoke: bool = False) -> bool:
    """Last resort: the normal web browser, and a box that stays until the user wants GridSim to stop."""
    if smoke:
        ok = page_served(st.port)
        log(f"smoke: last-resort window; no browser is opened and no box is shown; page handed out: {ok}")
        return ok
    try:
        webbrowser.open(url)
    except Exception:
        log(traceback.format_exc())
    if sys.platform == "win32":
        message_box(IN_BROWSER)
    else:
        print(f"GridSim is open in your web browser: {url}\nPress Ctrl+C here to stop GridSim.")
        try:
            while st.proc is not None and st.proc.poll() is None and not st.stop.wait(0.5):
                pass
        except KeyboardInterrupt:
            pass
    return True


def show(kind: str, url: str, st: Session, smoke: bool = False) -> bool:
    return {"webview": show_webview, "edge": show_edge, "browser": show_browser}[kind](url, st, smoke)


# ---- desktop.json: "GridSim is running" for the other parts ----------------------------------------------------------

def desktop_json() -> Path:
    return state_dir() / "desktop.json"


def write_desktop_json(st: Session) -> None:
    info = {"pid": os.getpid(), "server_pid": getattr(st.proc, "pid", None), "port": st.port, "host": st.host,
            "window": st.window, "started_utc": st.started}
    try:
        tmp = desktop_json().with_name("desktop.json.tmp")
        tmp.write_text(json.dumps(info), encoding="utf-8")
        os.replace(tmp, desktop_json())
    except OSError:
        log("desktop.json:\n" + traceback.format_exc())


def read_desktop_json() -> dict | None:
    try:
        info = json.loads(desktop_json().read_text(encoding="utf-8"))
        return info if isinstance(info, dict) else None
    except (OSError, ValueError):
        return None


def remove_desktop_json() -> None:
    try:
        desktop_json().unlink()
    except OSError:
        pass


def desktop_json_age() -> float:
    """Seconds since desktop.json was last written (when the server was ready, or the kind of window changed);
    0 when there is none."""
    try:
        return max(0.0, time.time() - desktop_json().stat().st_mtime)
    except OSError:
        return 0.0


def cleanup(st: Session) -> None:
    """Stop the server and take desktop.json away. Safe to call twice and from either thread."""
    with st._lock:
        if st.cleaned.is_set():
            return
        st.stop.set()
        stop_server(st.proc, st.job)
        st.job = None
        remove_desktop_json()
        st.cleaned.set()


# ---- background jobs and updates ------------------------------------------------------------------------------------

def repoint_schedule() -> None:
    """Once per start: make the user's background jobs (if they have any) point at this installed program."""
    try:
        schedule = importlib.import_module("gridsim.schedule")
        log(f"schedule.repoint: {schedule.repoint()}")
    except Exception:
        log("schedule.repoint did not work:\n" + traceback.format_exc())


def handoff_command(installer: str, log_path: Path | None = None) -> list[str]:
    """The downloaded installer, run so that it shows its progress bar only, cannot be cancelled half-way, and
    starts GridSim again when it is done. (No /SUPPRESSMSGBOXES: a real error must be shown.)"""
    log_path = log_path or state_dir() / "update" / "install.log"
    return [str(installer), "/SILENT", "/NOCANCEL", "/NORESTART", "/RELAUNCH=1", f"/LOG={log_path}"]


def start_installer(installer: str, popen=subprocess.Popen):
    """Start the installer on its own feet: detached, and outside any job this program may be in (a job that does
    not allow that refuses the start: then without)."""
    (state_dir() / "update").mkdir(parents=True, exist_ok=True)
    args = handoff_command(installer)
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    try:
        return popen(args, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **kw)
    except OSError:
        return popen(args, creationflags=flags, **kw)


def hand_off(info: dict, st: Session, update=None, leave=os._exit, wait_s: float = 8.0) -> bool:
    """The app has downloaded an update and asks for it to be installed: start the installer, close the window,
    stop the server, end. The request is taken away first, so it can never be acted on twice. False (and GridSim
    keeps running) when the installer could not be started."""
    installer = str(info.get("installer") or "")
    log(f"update: version {info.get('version')} is ready to install ({installer})")
    if update is not None:
        _quiet(update.clear_pending)
    try:
        if not Path(installer).is_file():
            raise FileNotFoundError(installer)
        start_installer(installer)
    except Exception:
        log("update: the installer could not be started\n" + traceback.format_exc())
        message_box(UPDATE_FAILED, 0x30)
        return False
    log("update: installer started; closing GridSim")
    st.handoff = True
    st.stop.set()
    for close in list(st.closers):
        _quiet(close)
    if not st.cleaned.wait(wait_s):          # the main thread is held up (the "Click OK" box): end from here
        cleanup(st)
        log("--- GridSim closed for the update")
        leave(EXIT_OK)
    return True


def watch(st: Session, update=None, interval: float = UPDATE_POLL_S) -> None:
    """Runs beside the window: every couple of seconds, has the app asked for an update to be installed?"""
    server_gone = False
    while not st.stop.wait(interval):
        if st.proc is not None and not server_gone and st.proc.poll() is not None:
            server_gone = True
            log(f"the server stopped by itself (exit code {st.proc.poll()}); see server.log")
        if update is None:
            continue
        try:
            info = update.pending()
        except Exception:
            info = None
        if info and hand_off(info, st, update):
            return


def _update_module():
    try:
        return importlib.import_module("gridsim.update")
    except Exception:
        log("no update module: updates are not watched for")
        return None


# ---- main -----------------------------------------------------------------------------------------------------------

def run(st: Session, smoke: bool = False, forced: str = "") -> int:
    windows = sys.platform == "win32"
    bring_up_server(st, phone_access())
    paths, why = plan_windows(forced, smoke, windows)
    log(f"window: {' > '.join(paths) or 'none'} (WebView2: {why})")
    st.window = paths[0] if paths else ""
    write_desktop_json(st)
    if is_installed():
        threading.Thread(target=repoint_schedule, name="gridsim-repoint", daemon=True).start()
    if windows:
        threading.Thread(target=watch, args=(st, _update_module()), name="gridsim-watch", daemon=True).start()
    for kind in paths:
        st.window = kind
        write_desktop_json(st)
        try:
            shown = show(kind, st.url, st, smoke)
        except Exception:
            log(f"window: {kind} failed\n" + traceback.format_exc())
            shown = False
        if shown or st.handoff:
            return EXIT_OK
        log(f"window: {kind} did not work")
    return EXIT_NO_WINDOW


def main(argv: list[str] | None = None) -> int:
    guard_stdio()
    smoke = smoke_mode()
    forced = os.environ.get("GRIDSIM_WINDOW", "").strip().lower()
    log(f"--- GridSim {__version__} starting (pid {os.getpid()}, {sys.executable}, "
        f"{'installed' if is_installed() else 'source folder'}{', self-check' if smoke else ''}"
        f"{', window=' + forced if forced else ''})")
    if sys.platform == "win32":
        set_app_id()
        if not only_instance():
            return second_launch(smoke)
    st = Session()
    code, problem = EXIT_NO_SERVER, ""
    try:
        code = run(st, smoke, forced)
        if code != EXIT_OK and not smoke:
            problem = "no window could be opened"
    except ServerFailed as e:
        problem = str(e)
        log(f"GridSim could not start: {e}. The end of server.log:\n{e.detail}")
    except KeyboardInterrupt:
        code = EXIT_OK
    except Exception:
        problem = "unexpected error"
        log("GridSim could not start:\n" + traceback.format_exc())
    finally:
        cleanup(st)
    if smoke and code == EXIT_OK and st.port and port_in_use(st.port):
        log(f"smoke: port {st.port} still answers after closing")
        code = EXIT_STILL_RUNNING
    if problem:
        text = START_FAILED if is_installed() else START_FAILED_SOURCE
        message_box(text.format(log=state_dir() / "desktop.log"), 0x10)
    log(f"--- GridSim closed (exit code {code})")
    return _leave(code)


def _leave(code: int) -> int:
    """After a pywebview window the .NET runtime is loaded, and Python's normal way out can hang on it: leave at
    once then (everything is already stopped and written)."""
    if "clr" in sys.modules:
        try:
            _open_log().flush()
        except Exception:
            pass
        os._exit(code)
    return code


if __name__ == "__main__":
    sys.exit(main())
