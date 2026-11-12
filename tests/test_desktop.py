"""The launcher of the installed Windows program (gridsim/desktop.py): everything it decides, checked on any PC.
The Windows calls themselves (job object, mutex, windows, registry, pywebview) are stood in for by small fakes; the
health check talks to a stand-in web server on this PC. No network, no real child process, no Streamlit."""
from __future__ import annotations

import ctypes
import http.server
import json
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest

from gridsim import desktop as d
from gridsim import installed

ROOT = Path(__file__).resolve().parent.parent
BREAKAWAY = 0x01000000
URL = "http://127.0.0.1:50123"


@pytest.fixture()
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIDSIM_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("GRIDSIM_DATA", str(tmp_path / "GridSim Data"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("GRIDSIM_SMOKE", raising=False)
    monkeypatch.delenv("GRIDSIM_WINDOW", raising=False)
    return installed.state_dir()


def log_text(state) -> str:
    return (state / "desktop.log").read_text(encoding="utf-8")


class FakeProc:
    """A child process that is "running" until it is stopped."""

    def __init__(self, code=None, pid=4242, on_stop=None):
        self.code, self.pid, self.on_stop, self.stopped = code, pid, on_stop, []

    def poll(self):
        return self.code

    def _end(self, how):
        self.stopped.append(how)
        if self.code is None:
            self.code = 0
            if self.on_stop:
                self.on_stop()

    def terminate(self):
        self._end("terminate")

    def kill(self):
        self._end("kill")

    def wait(self, timeout=None):
        return self.code


class Site:
    """A stand-in for the app's server on 127.0.0.1: answers /_stcore/health and / the way it is told to."""

    def __init__(self, health=(200, b"ok"), not_ready_first=0):
        site = self
        self.health, self.not_ready, self.asked = health, not_ready_first, 0

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/_stcore/health":
                    site.asked += 1
                    status, body = (503, b"unavailable") if site.asked <= site.not_ready else site.health
                else:
                    status, body = 200, b"<!DOCTYPE html><html><body><div id='root'></div></body></html>"
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.open = True

    def close(self):
        if self.open:
            self.open = False
            self.httpd.shutdown()
            self.httpd.server_close()
            self.thread.join(5)


@pytest.fixture()
def site():
    s = Site()
    yield s
    s.close()


# ---- import, log, stdio ----------------------------------------------------------------------------------------------

def test_importing_the_launcher_needs_no_windows_and_starts_nothing(state):
    assert not hasattr(ctypes, "windll") or sys.platform == "win32"      # (this test bench is not Windows)
    assert "webview" not in sys.modules and "winreg" not in sys.modules or sys.platform == "win32"
    assert not (state / "desktop.log").exists() and not (state / "desktop.json").exists()
    assert ctypes.sizeof(d.JobLimits) == 144 or ctypes.sizeof(ctypes.c_size_t) != 8   # the size Windows expects


def test_under_pythonw_everything_printed_goes_to_the_log(state, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(sys, "stdout", None)
        m.setattr(sys, "stderr", None)
        m.setattr(sys, "stdin", None)
        d.guard_stdio()
        out, err = sys.stdout, sys.stderr
        print("printed by a library")
        print("an error → with an arrow", file=sys.stderr)
        assert sys.stdin.read() == ""
    assert out is err and out is not None
    text = log_text(state)
    assert "printed by a library" in text and "an error → with an arrow" in text
    before = sys.stdout
    d.guard_stdio()                                   # a normal start (there is a console): nothing is touched
    assert sys.stdout is before


def test_the_logs_do_not_grow_for_ever(state):
    p = state / "server.log"
    p.write_bytes(b"".join(b"line %06d\n" % i for i in range(20000)))          # 240 kB
    d.trim_log(p, max_bytes=1_000_000)
    assert p.stat().st_size == 240000                                         # under the cap: left alone
    d.trim_log(p, max_bytes=100_000, keep_bytes=20_000)
    lines = p.read_bytes().splitlines()
    assert p.stat().st_size < 21_000 and lines[0].startswith(b"(older lines") and lines[-1] == b"line 019999"
    assert all(ln.startswith(b"line ") and len(ln) == 11 for ln in lines[1:])   # whole lines only
    d.trim_log(state / "not there.log")                                        # no file: no error
    assert d.tail_text(p, max_bytes=30).endswith("line 019999\n") and d.tail_text(state / "not there.log") == ""


def test_a_log_that_another_gridsim_has_open_is_left_as_it_is(state, monkeypatch):
    p = state / "desktop.log"
    p.write_bytes(b"line\n" * 1000)

    def in_use(src, dst):
        raise PermissionError(13, "The process cannot access the file because it is being used by another process")
    monkeypatch.setattr(d.os, "replace", in_use)
    d.trim_log(p, max_bytes=1000, keep_bytes=100)                              # (what Windows does then)
    assert p.stat().st_size == 5000 and [f.name for f in state.iterdir()] == ["desktop.log"]   # no .tmp left


def test_a_message_box_is_never_shown_in_self_check_mode(state, monkeypatch, capsys):
    monkeypatch.setenv("GRIDSIM_SMOKE", "1")
    d.message_box("GridSim could not start.")
    assert capsys.readouterr().err == "" and "message: GridSim could not start." in log_text(state)
    monkeypatch.delenv("GRIDSIM_SMOKE")
    d.message_box("Shown to the user")                 # (not Windows: printed instead of a box)
    assert "Shown to the user" in capsys.readouterr().err


def test_a_message_box_comes_to_the_front_of_the_other_windows(state, monkeypatch):
    shown = []
    user32 = types.SimpleNamespace(MessageBoxW=lambda *a: shown.append(a))
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(user32=user32), raising=False)
    with monkeypatch.context() as m:
        m.setattr(d.sys, "platform", "win32")
        d.message_box("GridSim could not start.", 0x10)
    assert shown == [(None, "GridSim could not start.", "GridSim", 0x10 | 0x00010000)]   # error icon | foreground


# ---- the server ------------------------------------------------------------------------------------------------------

def test_the_server_command_gives_every_setting_that_matters_as_a_flag(state):
    cmd = d.server_command(50123, python="C:/GridSim/python/python.exe", program=Path("C:/GridSim/program"))
    assert cmd[:4] == ["C:/GridSim/python/python.exe", "-m", "streamlit", "run"]
    assert Path(cmd[4]) == Path("C:/GridSim/program") / "app" / "streamlit_app.py"
    flags = dict(zip(cmd[5::2], cmd[6::2]))
    assert flags == {"--global.developmentMode": "false", "--server.headless": "true",
                     "--server.address": "127.0.0.1", "--server.port": "50123", "--server.fileWatcherType": "none",
                     "--server.runOnSave": "false", "--browser.gatherUsageStats": "false",
                     "--browser.serverAddress": "127.0.0.1", "--client.toolbarMode": "minimal"}
    phone = d.server_command(8501, "0.0.0.0")
    assert phone[phone.index("--server.address") + 1] == "0.0.0.0"
    assert phone[phone.index("--browser.serverAddress") + 1] == "127.0.0.1"       # the window still uses this PC
    assert phone[0] == installed.python_exe() and (Path(phone[4]).parent.parent / "gridsim" / "desktop.py").exists()
    assert not any("localhost" in part for part in cmd + phone)


def test_the_server_is_started_hidden_with_its_output_in_server_log(state):
    seen = {}

    def popen(cmd, **kw):
        seen.update(kw, cmd=cmd)
        kw["stdout"].write(b"hello from the server\n")
        return FakeProc()
    (state / "server.log").write_text("from last time\n")
    proc, since = d.start_server(50123, popen=popen)
    assert proc.pid == 4242 and since == len("from last time\n")
    assert seen["cmd"] == d.server_command(50123) and seen["cwd"] == str(installed.program_dir())
    assert seen["stdin"] == subprocess.DEVNULL and seen["stderr"] == subprocess.STDOUT
    assert seen["env"]["PYTHONIOENCODING"] == "utf-8:backslashreplace" and seen["env"]["PYTHONUNBUFFERED"] == "1"
    assert "creationflags" not in seen or sys.platform == "win32"
    assert d.hidden_flags(True) == {"creationflags": 0x08000000} and d.hidden_flags(False) == {}
    text = (state / "server.log").read_text()
    assert text.startswith("from last time\n") and "server on 127.0.0.1:50123" in text
    assert d.tail_text(state / "server.log", since).endswith("hello from the server\n")
    assert "from last time" not in d.tail_text(state / "server.log", since)


def test_private_to_this_pc_unless_phone_access_is_switched_on(state, monkeypatch):
    host, port = d.choose_address(False)
    assert host == "127.0.0.1" and 1024 < port < 65536 and port != 8501
    assert d.choose_address(True, in_use=lambda p: False) == ("0.0.0.0", 8501)
    # 8501 is taken (a GridSim started the old way is still open): another port, and the log says so
    assert d.choose_address(True, in_use=lambda p: True, pick=lambda host: 50999) == ("0.0.0.0", 50999)
    assert "8501 is already in use" in log_text(state)
    from gridsim import config
    assert d.phone_access() is False                                   # the shipped default
    cfg = config.load_config()
    monkeypatch.setattr(config, "load_config", lambda: cfg.copy_with({"display.phone_access": True}))
    assert d.phone_access() is True
    monkeypatch.setattr(config, "load_config", lambda: (_ for _ in ()).throw(OSError("settings unreadable")))
    assert d.phone_access() is False                                   # in doubt: private


def test_ready_means_the_health_address_says_ok(state, site):
    assert d.health_ok(site.port) and d.page_served(site.port) and d.port_in_use(site.port)
    site.not_ready = site.asked + 3                                    # 503 a few times first, as while starting
    assert d.wait_healthy(site.port, lambda: True, timeout=5, interval=0.01) == "ok" and site.asked >= 5
    site.health = (200, b"<html>some other page</html>")              # 200 alone is not enough
    assert not d.health_ok(site.port)
    assert d.wait_healthy(site.port, lambda: True, timeout=0.2, interval=0.01) == "timeout"
    site.health = (200, b"ok")
    assert d.wait_healthy(site.port, lambda: False, timeout=5, interval=0.01) == "died"   # fails fast
    site.close()
    assert not d.health_ok(site.port) and not d.page_served(site.port) and not d.port_in_use(site.port)


def test_a_server_that_dies_is_reported_with_the_end_of_its_log(state, monkeypatch):
    def start(port, host):
        (state / "server.log").write_text("Traceback (most recent call last):\nImportError: no module named polars\n")
        return FakeProc(code=1), 0
    monkeypatch.setattr(d, "start_server", start)
    st = d.Session()
    with pytest.raises(d.ServerFailed) as e:
        d.bring_up_server(st, timeout=1)
    assert "stopped while starting" in str(e.value) and "no module named polars" in e.value.detail
    assert st.proc is None

    slow = FakeProc()
    monkeypatch.setattr(d, "start_server", lambda port, host: (slow, 0))
    monkeypatch.setattr(d, "wait_healthy", lambda port, alive, timeout: "timeout")
    with pytest.raises(d.ServerFailed) as e:
        d.bring_up_server(d.Session(), timeout=120)
    assert "did not answer within 120 seconds" in str(e.value) and slow.stopped == ["terminate"]


def test_a_port_that_was_taken_after_all_is_tried_again_with_another(state, monkeypatch, site):
    starts = []

    def start(port, host):
        starts.append(port)
        if len(starts) == 1:
            (state / "server.log").write_text("Port 50001 is not available\n")
            return FakeProc(code=1), 0
        return FakeProc(pid=777), 0
    monkeypatch.setattr(d, "start_server", start)
    ports = iter([50001, site.port])
    monkeypatch.setattr(d, "choose_address", lambda phone: ("127.0.0.1", next(ports)))
    st = d.Session()
    d.bring_up_server(st, timeout=5)
    assert starts == [50001, site.port] and st.port == site.port and st.proc.pid == 777
    assert st.url == f"http://127.0.0.1:{site.port}" and "trying another one" in log_text(state)


def test_with_phone_access_a_refused_8501_is_not_asked_for_twice(state, monkeypatch, site):
    starts = []

    def start(port, host):
        starts.append((host, port))
        if len(starts) == 1:                                 # nothing answers on 8501, yet it cannot be had
            (state / "server.log").write_text("Port 8501 is not available\n")
            return FakeProc(code=1), 0
        return FakeProc(), 0
    monkeypatch.setattr(d, "start_server", start)
    monkeypatch.setattr(d, "port_in_use", lambda port: False)
    monkeypatch.setattr(d, "free_port", lambda host: site.port)
    st = d.Session()
    d.bring_up_server(st, phone=True, timeout=5)
    assert starts == [("0.0.0.0", 8501), ("0.0.0.0", site.port)] and (st.host, st.port) == ("0.0.0.0", site.port)


# ---- Windows calls, through fakes ------------------------------------------------------------------------------------

class FakeK32:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def __getattr__(self, name):
        def call(*args):
            self.calls.append((name, args))
            return 0 if name in self.fail else 77
        return call

    def names(self):
        return [name for name, _ in self.calls]


def test_the_job_object_ends_the_server_with_the_launcher_and_lets_children_break_away(state):
    k = FakeK32()
    job = d.create_job(k)
    (name, (handle, info_class, ref, size)) = k.calls[1]
    assert job == 77 and name == "SetInformationJobObject" and handle == 77 and info_class == 9 and size == 144
    assert ref._obj.BasicLimitInformation.LimitFlags == 0x2000 | 0x0800      # kill on close | breakaway allowed
    d.assign_to_job(job, 4242, k)
    assert ("OpenProcess", (0x0001 | 0x0100, False, 4242)) in k.calls        # only the server, by its pid
    assert k.names()[-2:] == ["AssignProcessToJobObject", "CloseHandle"]
    with pytest.raises(OSError, match="CreateJobObject"):
        d.create_job(FakeK32(fail={"CreateJobObjectW"}))
    bad = FakeK32(fail={"SetInformationJobObject"})
    with pytest.raises(OSError, match="SetInformationJobObject"):
        d.create_job(bad)
    assert bad.names()[-1] == "CloseHandle"                                  # no half-made job is left open
    bad = FakeK32(fail={"AssignProcessToJobObject"})
    with pytest.raises(OSError, match="AssignProcessToJobObject"):
        d.assign_to_job(77, 4242, bad)
    assert bad.names()[-1] == "CloseHandle"
    assert d.new_job() is None or sys.platform == "win32"                    # not Windows: no job, no error


def test_closing_stops_the_server_through_the_job_or_else_with_taskkill(state):
    k, proc = FakeK32(), FakeProc()
    d.stop_server(proc, job=77, k32=k)
    assert k.calls == [("TerminateJobObject", (77, 0)), ("CloseHandle", (77,))] and not proc.stopped
    ran = []
    d.stop_server(FakeProc(pid=31), job=None, run=lambda cmd, **kw: ran.append((cmd, kw)), windows=True)
    assert ran[0][0] == ["taskkill", "/PID", "31", "/T", "/F"] and ran[0][1]["creationflags"] == 0x08000000
    assert ran[0][1]["stdin"] == subprocess.DEVNULL
    proc = FakeProc()
    d.stop_server(proc, windows=False)
    assert proc.stopped == ["terminate"]
    d.stop_server(None)                                                      # nothing started: nothing to do

    class Stubborn(FakeProc):
        def wait(self, timeout=None):
            if "kill" not in self.stopped:
                raise subprocess.TimeoutExpired("server", timeout)
    proc = Stubborn()
    d.stop_server(proc, windows=False)
    assert proc.stopped == ["terminate", "kill"]


def test_the_windows_libraries_are_set_up_with_types_that_exist(monkeypatch):
    """Every Windows function gets its argument and return types (the default would cut 64-bit handles). Here a
    stand-in library takes them: a misspelt type name would fail on any PC, not only on Windows."""
    class Library:
        def __init__(self, name, use_last_error=False):
            self.name, self.use_last_error = name, use_last_error

        def __getattr__(self, function):
            setattr(self, function, types.SimpleNamespace())
            return self.__dict__[function]
    monkeypatch.setattr(ctypes, "WinDLL", Library, raising=False)
    monkeypatch.setattr(d, "_K32", None)
    monkeypatch.setattr(d, "_U32", None)
    k, u = d._kernel32(), d._user32()
    assert (k.name, u.name) == ("kernel32", "user32") and k.use_last_error and d._kernel32() is k
    from ctypes import wintypes
    for fn in ("CreateJobObjectW", "OpenProcess", "CreateMutexW"):
        assert getattr(k, fn).restype is wintypes.HANDLE
    for fn in ("SetInformationJobObject", "AssignProcessToJobObject", "TerminateJobObject", "CloseHandle"):
        assert getattr(k, fn).restype is wintypes.BOOL and getattr(k, fn).argtypes[0] is wintypes.HANDLE
    for fn in ("IsWindowVisible", "GetWindowTextW", "GetClassNameW", "GetWindowThreadProcessId", "IsIconic",
               "ShowWindow", "SetForegroundWindow"):
        assert getattr(u, fn).argtypes[0] is wintypes.HWND
    assert u.GetWindowThreadProcessId.argtypes[1] is ctypes.POINTER(wintypes.DWORD)


def test_only_one_gridsim_at_a_time(state, monkeypatch):
    k = FakeK32()
    monkeypatch.setattr(d, "_last_error", lambda: 0)
    assert d.single_instance(k) is True and k.calls == [("CreateMutexW", (None, False, "Local\\GridSim.Desktop"))]
    monkeypatch.setattr(d, "_last_error", lambda: 183)                       # the mutex already exists
    assert d.single_instance(k) is False


class FakeUser32:
    def __init__(self, windows):
        self.windows, self.restored, self.front = windows, [], []

    def EnumWindows(self, callback, lparam):
        for hwnd in self.windows:
            if not callback(hwnd, 0):
                break
        return 1

    def IsWindowVisible(self, hwnd):
        return self.windows[hwnd].get("visible", True)

    def GetWindowTextW(self, hwnd, buf, n):
        buf.value = self.windows[hwnd]["title"]

    def GetClassNameW(self, hwnd, buf, n):
        buf.value = self.windows[hwnd].get("cls", "WindowsForms10.Window.8.app")

    def GetWindowThreadProcessId(self, hwnd, ref):
        ref._obj.value = self.windows[hwnd]["pid"]

    def IsIconic(self, hwnd):
        return self.windows[hwnd].get("minimised", False)

    def ShowWindow(self, hwnd, how):
        self.restored.append((hwnd, how))

    def SetForegroundWindow(self, hwnd):
        self.front.append(hwnd)


def test_a_second_double_click_brings_the_first_window_forward(state):
    u = FakeUser32({11: {"title": "GridSim", "pid": 999, "cls": "CabinetWClass"},          # a folder named GridSim
                    12: {"title": "GridSim", "pid": 500, "visible": False},
                    13: {"title": "GridSim", "pid": 500, "minimised": True},
                    14: {"title": "gridsim - Microsoft Edge", "pid": 800, "cls": "Chrome_WidgetWin_1"}})
    assert d.focus_existing({"pid": 500}, u) is True
    assert u.front == [13] and u.restored == [(13, 9)]                       # the launcher's own, restored first
    u = FakeUser32({11: {"title": "GridSim", "pid": 999, "cls": "CabinetWClass"},
                    14: {"title": "gridsim", "pid": 800, "cls": "Chrome_WidgetWin_1"}})
    assert d.focus_existing({"pid": 500}, u) is True and u.front == [14] and not u.restored   # the Edge window
    u = FakeUser32({11: {"title": "GridSim", "pid": 999, "cls": "CabinetWClass"}})
    assert d.focus_existing({"pid": 500}, u) is False and not u.front        # never some other program's window


def test_a_second_launch_never_starts_a_second_gridsim(state, monkeypatch):
    opened = []
    monkeypatch.setattr(d.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(d, "health_ok", lambda port: True)
    monkeypatch.setattr(d, "focus_existing", lambda info: True)
    (state / "desktop.json").write_text(json.dumps({"pid": 500, "port": 50123, "window": "browser"}))
    assert d.second_launch() == 0 and not opened                             # window found: brought forward
    monkeypatch.setattr(d, "focus_existing", lambda info: False)
    assert d.second_launch() == 0 and opened == ["http://127.0.0.1:50123"]   # its browser tab was closed: reopen
    assert d.second_launch(smoke=True) == 0 and len(opened) == 1
    (state / "desktop.json").write_text(json.dumps({"pid": 500, "port": 50123, "window": "webview"}))
    assert d.second_launch() == 0 and len(opened) == 1                       # still starting: just leave
    # ... but a window that should have been there long ago and is not: the app is opened in the browser, so that
    # a double-click never ends with nothing on the screen for good
    monkeypatch.setattr(d, "desktop_json_age", lambda: d.WINDOW_WAIT_S + 1)
    assert d.second_launch() == 0 and opened[1:] == ["http://127.0.0.1:50123"]
    assert d.second_launch(smoke=True) == 0 and len(opened) == 2
    monkeypatch.setattr(d, "health_ok", lambda port: False)                  # (only when the app is being served)
    assert d.second_launch() == 0 and len(opened) == 2
    monkeypatch.setattr(d, "health_ok", lambda port: True)
    monkeypatch.setattr(d.webbrowser, "open", lambda url: (_ for _ in ()).throw(OSError("no browser")))
    assert d.second_launch() == 0                                            # never an error, whatever happens
    (state / "desktop.json").write_text(json.dumps({"pid": "?", "port": "not a number", "window": "browser"}))
    assert d.second_launch() == 0                                            # a damaged file
    (state / "desktop.json").unlink()
    monkeypatch.setattr(d, "focus_existing", lambda info: (_ for _ in ()).throw(OSError("no desktop")))
    assert d.second_launch() == 0 and len(opened) == 2


def test_desktop_json_says_how_long_ago_it_was_written(state):
    import os
    import time
    assert d.desktop_json_age() == 0.0                                       # none there
    st = d.Session()
    d.write_desktop_json(st)
    assert 0.0 <= d.desktop_json_age() < 30
    then = time.time() - 600
    os.utime(state / "desktop.json", (then, then))
    assert 590 < d.desktop_json_age() < 630


def test_the_first_gridsim_clears_what_a_killed_one_left_behind(state, monkeypatch):
    (state / "desktop.json").write_text(json.dumps({"pid": 1, "port": 50123, "window": "webview"}))
    assert d.only_instance(windows=False) is True and (state / "desktop.json").exists()   # no mutex here: untouched
    monkeypatch.setattr(d, "single_instance", lambda: False)
    assert d.only_instance(windows=True) is False and (state / "desktop.json").exists()   # the running one's file
    monkeypatch.setattr(d, "single_instance", lambda: True)
    assert d.only_instance(windows=True) is True and not (state / "desktop.json").exists()
    monkeypatch.setattr(d, "single_instance", lambda: (_ for _ in ()).throw(OSError("no kernel32")))
    assert d.only_instance(windows=True) is True and "single instance check" in log_text(state)


class FakeWinreg:
    HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE = "HKCU", "HKLM"

    class Key:
        def __init__(self, values):
            self.values = values

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def __init__(self, keys):
        self.keys = keys

    def OpenKey(self, hive, path):
        if (hive, path) not in self.keys:
            raise FileNotFoundError(path)
        return self.Key(self.keys[(hive, path)])

    def QueryValueEx(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name], 1


WV2_PC = ("HKLM", "SOFTWARE\\WOW6432Node\\Microsoft\\EdgeUpdate\\Clients\\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}")
WV2_USER = ("HKCU", "SOFTWARE\\Microsoft\\EdgeUpdate\\Clients\\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}")
DOTNET = ("HKLM", "SOFTWARE\\Microsoft\\NET Framework Setup\\NDP\\v4\\Full")


def test_the_webview2_window_is_used_only_when_the_registry_says_it_is_there(state):
    net = {DOTNET: {"Release": 533320}}
    assert d.webview2_version(FakeWinreg({WV2_PC: {"pv": "152.0.3800.70"}})) == "152.0.3800.70"
    assert d.webview2_version(FakeWinreg({WV2_USER: {"pv": "151.0.1.2"}})) == "151.0.1.2"       # per-user install
    for missing in ({}, {WV2_PC: {}}, {WV2_PC: {"pv": ""}}, {WV2_PC: {"pv": "0.0.0.0"}},
                    {WV2_USER: {"pv": "85.0.1.1"}}):
        assert d.webview2_version(FakeWinreg(missing)) is None
        assert d.webview_ready(FakeWinreg({**missing, **net}), have_module=True)[0] is False
    assert d.webview2_version(FakeWinreg({WV2_USER: {"pv": "0.0.0.0"}, WV2_PC: {"pv": "152.0.1.1"}})) == "152.0.1.1"
    ok, why = d.webview_ready(FakeWinreg({WV2_PC: {"pv": "152.0.3800.70"}, **net}), have_module=True)
    assert ok and "152.0.3800.70" in why
    ok, why = d.webview_ready(FakeWinreg({WV2_PC: {"pv": "152.0.3800.70"}}), have_module=True)
    assert not ok and ".NET" in why                                          # pywebview would fall back to IE: no
    ok, why = d.webview_ready(FakeWinreg({WV2_PC: {"pv": "152.0.3800.70"}, DOTNET: {"Release": 394271}}), True)
    assert not ok and "394271" in why
    ok, why = d.webview_ready(FakeWinreg({WV2_PC: {"pv": "152.0.3800.70"}, **net}), have_module=False)
    assert not ok and "pywebview is not installed" in why                    # a source folder without it
    assert d.webview_ready()[0] is False or sys.platform == "win32"


def test_which_window_is_tried_and_in_what_order():
    assert d.window_paths(webview_ok=True) == ["webview", "edge", "browser"]
    assert d.window_paths(webview_ok=False) == ["edge", "browser"]           # never pywebview without WebView2
    assert d.window_paths("edge", webview_ok=True) == ["edge", "browser"]
    assert d.window_paths("browser", webview_ok=True) == ["browser"]
    assert d.window_paths("webview", webview_ok=False) == ["edge", "browser"]
    assert d.window_paths("nonsense", webview_ok=True) == ["webview", "edge", "browser"]
    # the self-check tries exactly one kind, so a pass can only mean that kind worked
    assert d.window_paths("", smoke=True, webview_ok=True) == ["webview"]
    assert d.window_paths("", smoke=True, webview_ok=False) == []
    assert d.window_paths("edge", smoke=True, webview_ok=True) == ["edge"]
    assert d.window_paths("browser", smoke=True) == ["browser"]
    assert d.window_paths("webview", smoke=True, webview_ok=True, windows=False) == ["browser"]


def test_the_window_always_fits_the_screen():
    assert d.window_geometry(2560, 1440) == {"width": 1400, "height": 900, "min_size": (1000, 700), "maximized": False}
    g = d.window_geometry(1536, 864)                 # a 1920x1080 laptop at 125 %
    assert g["height"] <= 864 - 80 and g["width"] == 1400 and g["maximized"]
    g = d.window_geometry(1024, 768)                 # the test machines on GitHub
    assert (g["width"], g["height"]) == (984, 688) and g["min_size"] == (984, 688) and g["maximized"]
    g = d.window_geometry(0, 0)                      # unknown screen: modest and maximised
    assert g["maximized"] and g["width"] <= 1100 and g["min_size"][0] <= g["width"]
    assert d.screen_size() == (0, 0) or sys.platform == "win32"


# ---- the three kinds of window ---------------------------------------------------------------------------------------

class FakeWebview(types.ModuleType):
    """pywebview as far as the launcher uses it: start() runs the given function on a thread, as pywebview does."""

    def __init__(self, renderer="edgechromium", has_app=True, loads=True, fail=None):
        super().__init__("webview")
        self.settings = {"ALLOW_DOWNLOADS": False, "OPEN_DEVTOOLS_IN_DEBUG": True}
        self.renderer, self._after, self.fail = None, renderer, fail
        events = types.SimpleNamespace(loaded=threading.Event(), closed=self.Closed(), shown=threading.Event())
        self.window = types.SimpleNamespace(events=events, destroyed=0, asked=[])
        self.window.evaluate_js = lambda js: (self.window.asked.append(js), has_app if "stApp" in js else 321)[1]
        self.window.destroy = self._destroy
        self.loads = loads

    class Closed(threading.Event):
        """Notes when the launcher starts waiting for the window to be closed."""

        def __init__(self):
            super().__init__()
            self.waited_for = threading.Event()

        def wait(self, timeout=None):
            self.waited_for.set()
            return super().wait(timeout)

    def _destroy(self):
        self.window.destroyed += 1
        self.window.events.closed.set()

    def create_window(self, title, url, **kw):
        self.created = (title, url, kw)
        return self.window

    def start(self, func, **kw):
        self.started = kw
        self.note_while_opening = d.webview_note().exists()
        if self.fail:
            raise self.fail
        self.renderer = self._after
        self.window.events.shown.set()
        if self.loads:
            self.window.events.loaded.set()
        t = threading.Thread(target=func)
        t.start()
        closed = self.window.events.closed
        while t.is_alive() and not closed.waited_for.wait(0.01):             # the window is up and stays open ...
            pass
        closed.set()                                                         # ... until the user closes it
        t.join(20)
        assert not t.is_alive()


@pytest.fixture()
def fast(monkeypatch):
    ended = []
    monkeypatch.setattr(d.time, "sleep", lambda s: None)
    monkeypatch.setattr(d, "end_after_close", lambda st: ended.append(st))
    return ended


def test_the_webview_window_is_asked_for_webview2_with_its_own_profile(state, monkeypatch, fast):
    wv = FakeWebview()
    monkeypatch.setitem(sys.modules, "webview", wv)
    st = d.Session()
    assert d.show_webview("http://127.0.0.1:50123", st) is True
    title, url, kw = wv.created
    assert (title, url) == ("GridSim", "http://127.0.0.1:50123") and kw["text_select"] and kw["zoomable"]
    assert kw["width"] <= 1400 and kw["min_size"][0] <= kw["width"]
    assert kw["background_color"] == "#0d0d0d" and f'backgroundColor = "{kw["background_color"]}"' in \
        (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")     # the app's own: no white flash
    assert wv.started["gui"] == "edgechromium" and wv.started["private_mode"] is False
    assert wv.started["storage_path"] == str(state / "webview") and wv.started["icon"] is None   # a source folder
    assert wv.settings == {"ALLOW_DOWNLOADS": True, "OPEN_DEVTOOLS_IN_DEBUG": False}
    assert wv.window.destroyed == 0 and not wv.window.asked                  # a normal run only watches it load
    assert len(st.closers) == 1 and "webview: page loaded" in log_text(state)
    assert fast == [st]                                                      # closed: the launcher is made to end
    assert wv.note_while_opening and not d.webview_note().exists()           # (see the test after the next two)
    st.closers[0]()                                                          # (how an update closes the window)
    assert wv.window.destroyed == 1


def test_the_self_check_passes_only_when_webview2_really_shows_the_app(state, monkeypatch, fast):
    def run(**kw):
        wv = FakeWebview(**kw)
        monkeypatch.setitem(sys.modules, "webview", wv)
        return d.show_webview("http://127.0.0.1:50123", d.Session(), smoke=True), wv
    ok, wv = run()
    assert ok is True and wv.window.destroyed == 1 and "stApp" in wv.window.asked[0]   # found, then closes itself
    assert not fast                                          # (a hang after closing must show up as a failed check)
    assert "smoke: app on the page: True" in log_text(state)
    monkeypatch.setattr(d, "SMOKE_RENDER_S", 0.05)
    ok, wv = run(has_app=False)
    assert ok is False and wv.window.destroyed == 1                          # a blank page is not a pass
    for renderer in ("mshtml", None):
        ok, wv = run(renderer=renderer)
        assert ok is False and wv.window.destroyed == 1 and not wv.window.asked   # Internet Explorer's engine: no
    assert "renderer mshtml" in log_text(state)


def test_a_webview_window_that_cannot_work_makes_way_for_the_next_kind(state, monkeypatch, fast):
    def run(**kw):
        wv = FakeWebview(**kw)
        monkeypatch.setitem(sys.modules, "webview", wv)
        return d.show_webview("http://127.0.0.1:50123", d.Session()), wv
    ok, wv = run(fail=RuntimeError("pythonnet cannot be loaded"))
    assert ok is False and "pythonnet cannot be loaded" in log_text(state)
    ok, wv = run(renderer="mshtml")
    assert ok is False and wv.window.destroyed == 1
    monkeypatch.setattr(d, "PAGE_TIMEOUT_S", 0.05)
    ok, wv = run(loads=False)                                                # WebView2 never got going: blank window
    assert ok is False and wv.window.destroyed == 1 and "did not load" in log_text(state)
    monkeypatch.setitem(sys.modules, "webview", None)                        # not installed at all
    with pytest.raises(ImportError):                                         # (run() catches it and goes on)
        d.show("webview", "http://127.0.0.1:50123", d.Session())


def test_a_start_that_died_while_opening_the_webview_window_uses_the_next_kind_once(state, monkeypatch):
    monkeypatch.setattr(d, "webview_ready", lambda: (True, "WebView2 Runtime 152.0.1.1"))
    assert d.plan_windows() == (["webview", "edge", "browser"], "WebView2 Runtime 152.0.1.1")
    d.webview_note().write_text("2026.11.12")                # the program ended while the window was being opened
    paths, why = d.plan_windows()
    assert paths == ["edge", "browser"] and "this once" in why
    assert d.plan_windows()[0] == ["webview", "edge", "browser"]             # the start after that tries again
    for kw in ({"smoke": True}, {"forced": "webview"}):                      # the tests ask for exactly one kind
        d.webview_note().write_text("2026.11.12")
        assert d.plan_windows(**kw)[0][0] == "webview" and not d.webview_note().exists()
    assert d.plan_windows(windows=False) == (["browser"], "not Windows")
    # a window that could not be shown in an orderly way leaves no note behind
    monkeypatch.setitem(sys.modules, "webview", FakeWebview(fail=RuntimeError("no .NET")))
    assert d.show_webview(URL, d.Session()) is False and not d.webview_note().exists()


def test_only_a_real_ico_file_is_given_to_the_window(tmp_path, monkeypatch):
    assert d.icon_path() is None                             # a source folder has none
    monkeypatch.setattr(d, "install_root", lambda: tmp_path)
    assert d.icon_path() is None                             # installed, but the file is missing
    (tmp_path / "gridsim.ico").write_bytes(b"\x89PNG\r\n\x1a\n....")
    assert d.icon_path() is None                             # a PNG with the wrong name
    (tmp_path / "gridsim.ico").write_bytes(b"\x00\x00\x01\x00\x03\x00....")
    assert d.icon_path() == str(tmp_path / "gridsim.ico")


def test_a_window_whose_code_never_comes_back_cannot_leave_gridsim_running_unseen(state):
    st, left = d.Session(), []
    st.proc, st.port = FakeProc(), 50123
    d.write_desktop_json(st)
    st.cleaned.set()                                         # the normal case: the main thread has cleaned up
    d.end_after_close(st, grace=5, leave=left.append)
    assert not left and not st.proc.stopped
    st.cleaned.clear()
    d.end_after_close(st, grace=0.01, leave=left.append)
    assert left == [0] and st.proc.stopped == ["terminate"] and not (state / "desktop.json").exists()
    assert "did not return" in log_text(state)


class Clock:
    def __init__(self):
        self.now, self.steps = 100.0, []

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.now += s
        if self.steps:
            self.steps.pop(0)()


def test_the_edge_window_has_its_own_profile_and_is_waited_for_until_it_is_really_closed(state, monkeypatch):
    from gridsim import appwindow
    cmd = d.browser_command("C:/Edge/msedge.exe", "http://127.0.0.1:50123", Path("C:/state/browser-profile"),
                            d.window_geometry(2560, 1440))
    assert cmd[:2] == ["C:/Edge/msedge.exe", "--app=http://127.0.0.1:50123"]
    assert f"--user-data-dir={Path('C:/state/browser-profile')}" in cmd and "--no-first-run" in cmd
    assert "--window-size=1400,900" in cmd and "--start-maximized" not in cmd
    assert "--start-maximized" in d.browser_command("x", "u", Path("p"), d.window_geometry(1280, 720))

    monkeypatch.setattr(appwindow, "browser_exe", lambda: None)
    assert d.show_edge(URL, d.Session()) is False       # no Edge, no Chrome: next kind

    monkeypatch.setattr(appwindow, "browser_exe", lambda: "C:/Edge/msedge.exe")
    clock, lock, started = Clock(), state / "browser-profile" / "lockfile", []
    proc = FakeProc()

    def popen(cmd, **kw):
        started.append(cmd)
        return proc
    fake = {"popen": popen, "sleep": clock.sleep, "now": clock.monotonic}
    lock.parent.mkdir()
    # Edge takes the profile, restarts itself under another process number (ours ends), and is closed much later
    clock.steps = [lock.touch, lambda: setattr(proc, "code", 0)] + [lambda: None] * 30 + [lock.unlink]
    st = d.Session()
    assert d.show_edge(URL, st, **fake) is True
    assert not clock.steps and clock.now - 100.0 > 15                        # waited for the lock, not our child
    assert started[0][1] == "--app=http://127.0.0.1:50123"
    assert f"--user-data-dir={state / 'browser-profile'}" in started[0]
    # the browser ends at once and never touches its profile: it did not open -> the next kind of window
    clock.steps, proc = [], FakeProc(code=1)
    t0 = clock.now
    assert d.show_edge(URL, d.Session(), **fake) is False
    assert "did not open" in log_text(state) and clock.now - t0 > d.EDGE_OPEN_S      # (it was given its time)
    # Edge restarts itself at once and the new one takes the profile only seconds later: still the same window
    proc = FakeProc(code=0)
    clock.steps = [lambda: None] * 8 + [lock.touch] + [lambda: None] * 30 + [lock.unlink]
    t0 = clock.now
    assert d.show_edge(URL, d.Session(), **fake) is True and not clock.steps and clock.now - t0 > 19
    # a browser that never uses a lock file at all: its window was open for a while and is closed now
    proc = FakeProc()
    clock.steps = [lambda: None] * 40 + [lambda: setattr(proc, "code", 0)]
    assert d.show_edge(URL, d.Session(), **fake) is True and not clock.steps
    # an update: the window is closed for the user and the wait ends
    proc, st = FakeProc(), d.Session()
    clock.steps = [lambda: None, lambda: (st.closers[0](), st.stop.set())]
    assert d.show_edge(URL, st, **fake) is True
    assert proc.stopped == ["terminate"]
    # self-check: the profile lock appears -> pass, and the launcher closes the browser itself
    proc = FakeProc()
    clock.steps = [lambda: None, lock.touch, lock.unlink]
    assert d.show_edge(URL, d.Session(), smoke=True, **fake) is True
    assert proc.stopped == ["terminate"] and "profile lock seen: True" in log_text(state)
    proc, clock.steps = FakeProc(), []
    assert d.show_edge(URL, d.Session(), smoke=True, **fake) is False


def test_the_last_resort_is_the_normal_browser(state, monkeypatch, site, capsys):
    opened = []
    monkeypatch.setattr(d.webbrowser, "open", lambda url: opened.append(url))
    st = d.Session()
    st.port, st.proc = site.port, FakeProc(code=0)
    assert d.show_browser(st.url, st) is True and opened == [st.url]
    assert "GridSim is open in your web browser" in capsys.readouterr().out       # (on Windows: the OK box)
    assert d.show_browser(st.url, st, smoke=True) is True and len(opened) == 1    # self-check: no browser, no box
    site.close()
    assert d.show_browser(st.url, st, smoke=True) is False


# ---- a whole run -----------------------------------------------------------------------------------------------------

def run_with(monkeypatch, site, proc, show=None):
    monkeypatch.setattr(d, "choose_address", lambda phone: ("127.0.0.1", site.port))
    monkeypatch.setattr(d, "start_server", lambda port, host: (proc, 0))
    if show:
        monkeypatch.setattr(d, "show_browser", show)
    return d.main([])


def test_a_run_keeps_desktop_json_while_the_window_is_open_and_leaves_nothing_behind(state, monkeypatch, site):
    proc, seen = FakeProc(on_stop=site.close), {}

    def window(url, st, smoke=False):
        seen.update(json.loads((state / "desktop.json").read_text()), url=url)
        return True
    assert run_with(monkeypatch, site, proc, window) == 0
    import os
    assert seen["pid"] == os.getpid() and seen["server_pid"] == 4242 and seen["port"] == site.port
    assert seen["host"] == "127.0.0.1" and seen["window"] == "browser" and seen["started_utc"].endswith("+00:00")
    assert seen["url"] == f"http://127.0.0.1:{site.port}"
    assert set(seen) - {"url"} == {"pid", "server_pid", "port", "host", "window", "started_utc"}
    assert proc.stopped == ["terminate"] and not (state / "desktop.json").exists()
    text = log_text(state)
    assert "starting" in text and "server ready" in text and "closed (exit code 0)" in text
    assert not [t.name for t in threading.enumerate() if t.name.startswith("gridsim-")]


def test_a_crash_in_the_window_code_still_stops_the_server(state, monkeypatch, site, capsys):
    proc = FakeProc(on_stop=site.close)
    monkeypatch.setattr(d, "show_browser", lambda url, st, smoke=False: (_ for _ in ()).throw(RuntimeError("boom")))
    assert run_with(monkeypatch, site, proc) == 2
    assert proc.stopped == ["terminate"] and not (state / "desktop.json").exists()
    assert "RuntimeError: boom" in log_text(state) and "GridSim could not start" in capsys.readouterr().err


def test_the_self_check_reports_through_its_exit_code_and_never_asks_anything(state, monkeypatch, site, capsys):
    monkeypatch.setenv("GRIDSIM_SMOKE", "1")
    monkeypatch.setattr(d.webbrowser, "open", lambda url: pytest.fail("the self-check opened a browser"))
    stays_up = FakeProc()                                    # "stopped", but the port still answers afterwards
    assert run_with(monkeypatch, site, stays_up) == 3 and "still answers after closing" in log_text(state)
    proc = FakeProc(on_stop=site.close)
    assert run_with(monkeypatch, site, proc) == 0            # page handed out, server gone afterwards
    assert not (state / "desktop.json").exists()

    def dead(port, host):
        (state / "server.log").write_text("ModuleNotFoundError: No module named 'streamlit'\n")
        return FakeProc(code=1), 0
    monkeypatch.setattr(d, "start_server", dead)
    assert d.main([]) == 1                                   # the server did not start
    assert "No module named 'streamlit'" in log_text(state)  # ... and why is in desktop.log
    assert capsys.readouterr().err == "" and not (state / "desktop.json").exists()


def test_a_server_that_does_not_start_is_explained_to_the_user(state, monkeypatch, capsys):
    monkeypatch.setattr(d, "start_server", lambda port, host: (FakeProc(code=1), 0))
    assert d.main([]) == 1
    err = capsys.readouterr().err                            # here: a source folder, so that is what it says
    assert "GridSim could not start." in err and str(state / "desktop.log") in err and "Setup GridSim" in err
    monkeypatch.setattr(d, "is_installed", lambda: True)
    monkeypatch.setattr(d, "repoint_schedule", lambda: None)
    assert d.main([]) == 1
    err = capsys.readouterr().err                            # the installed program: the installer repairs it
    assert "GridSim-Setup.exe again" in err and "leaves your data alone" in err and str(state / "desktop.log") in err


def test_the_background_jobs_are_repointed_once_per_start_of_the_installed_program(state, monkeypatch, site):
    calls = []
    fake = types.ModuleType("gridsim.schedule")
    fake.repoint = lambda: calls.append("repoint") or "2 jobs checked"
    monkeypatch.setitem(sys.modules, "gridsim.schedule", fake)
    assert run_with(monkeypatch, site, FakeProc(on_stop=site.close), lambda url, st, smoke=False: True) == 0
    assert not calls                                         # a source folder: its jobs are left alone
    monkeypatch.setattr(d, "is_installed", lambda: True)
    done = threading.Event()

    def window(url, st, smoke=False):
        for t in threading.enumerate():
            if t.name == "gridsim-repoint":
                t.join(5)
        done.set()
        return True
    site2 = Site()
    try:
        assert run_with(monkeypatch, site2, FakeProc(on_stop=site2.close), window) == 0
    finally:
        site2.close()
    assert done.is_set() and calls == ["repoint"] and "schedule.repoint: 2 jobs checked" in log_text(state)
    fake.repoint = lambda: (_ for _ in ()).throw(RuntimeError("schtasks said no"))
    d.repoint_schedule()                                     # a failure is logged, never raised
    monkeypatch.setitem(sys.modules, "gridsim.schedule", None)
    d.repoint_schedule()                                     # (and so is a missing module)
    assert "schtasks said no" in log_text(state)


# ---- the update hand-over --------------------------------------------------------------------------------------------

class FakeUpdate:
    def __init__(self, answers=()):
        self.answers, self.cleared = list(answers), 0

    def pending(self):
        return self.answers.pop(0) if self.answers else None

    def clear_pending(self):
        self.cleared += 1


def test_the_installer_is_started_on_its_own_feet(state):
    cmd = d.handoff_command("C:/state/update/GridSim-Setup.exe")
    assert cmd == ["C:/state/update/GridSim-Setup.exe", "/SILENT", "/NOCANCEL", "/NORESTART", "/RELAUNCH=1",
                   f"/LOG={state / 'update' / 'install.log'}"]
    assert "/SUPPRESSMSGBOXES" not in cmd and "/VERYSILENT" not in cmd       # a real error must be shown
    calls = []

    def popen(args, **kw):
        calls.append((args, kw))
        if kw["creationflags"] & BREAKAWAY and len(calls) == 1:
            raise PermissionError(5, "Access is denied")                     # a job that does not allow leaving it
        return FakeProc()
    d.start_installer("C:/state/update/GridSim-Setup.exe", popen)
    (_, first), (args, second) = calls
    assert first["creationflags"] == 0x00000008 | 0x00000200 | BREAKAWAY     # detached | new group | breakaway
    assert second["creationflags"] == 0x00000008 | 0x00000200 and args == cmd
    assert second["stdin"] == second["stdout"] == second["stderr"] == subprocess.DEVNULL and second["close_fds"]
    assert (state / "update").is_dir()
    with pytest.raises(OSError):
        d.start_installer("gone.exe", lambda args, **kw: (_ for _ in ()).throw(FileNotFoundError(args[0])))


def test_an_update_starts_the_installer_closes_the_window_and_stops_the_server(state, monkeypatch):
    setup = state / "update" / "GridSim-Setup.exe"
    setup.parent.mkdir()
    setup.write_bytes(b"MZ")
    started, closed, left = [], [], []
    monkeypatch.setattr(d, "start_installer", lambda installer: started.append(installer))
    up, st = FakeUpdate(), d.Session()
    st.proc, st.port = FakeProc(), 50123
    d.write_desktop_json(st)
    st.closers.append(lambda: closed.append("window"))
    # the main thread is held up (the "Click OK" box): the hand-over stops everything itself and leaves
    assert d.hand_off({"installer": str(setup), "version": "2026.12.1"}, st, up, leave=left.append, wait_s=0.01)
    assert started == [str(setup)] and up.cleared == 1 and closed == ["window"] and left == [0]
    assert st.handoff and st.stop.is_set() and st.proc.stopped == ["terminate"]
    assert not (state / "desktop.json").exists()
    # the window closed in time and the main thread cleaned up: nothing more to do here
    st = d.Session()
    st.cleaned.set()
    assert d.hand_off({"installer": str(setup), "version": "2026.12.1"}, st, up, leave=left.append) and left == [0]


def test_an_update_that_cannot_start_leaves_gridsim_running_and_is_not_tried_again(state, monkeypatch, capsys):
    up, st, left = FakeUpdate(), d.Session(), []
    st.proc = FakeProc()
    info = {"installer": str(state / "missing.exe"), "version": "2026.12.1"}
    assert d.hand_off(info, st, up, leave=left.append) is False
    assert up.cleared == 1 and not left and not st.handoff and not st.stop.is_set() and not st.proc.stopped
    err = capsys.readouterr().err
    assert "The update could not be started" in err and "github.com/Dierks27/GameSim/releases/latest" in err
    setup = state / "GridSim-Setup.exe"
    setup.write_bytes(b"MZ")
    monkeypatch.setattr(d, "start_installer", lambda installer: (_ for _ in ()).throw(OSError("blocked")))
    assert d.hand_off({"installer": str(setup)}, st, up, leave=left.append) is False and up.cleared == 2


def test_the_launcher_looks_for_a_requested_update_every_couple_of_seconds(state, monkeypatch):
    handed = []
    monkeypatch.setattr(d, "hand_off", lambda info, st, update: handed.append(info) or True)

    class Flaky(FakeUpdate):
        def pending(self):
            if not self.answers:
                return None
            answer = self.answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
    up = Flaky([None, OSError("file being written"), {"installer": "x.exe", "version": "2026.12.1"}])
    st = d.Session()
    st.proc = FakeProc(code=3)                               # (the server ending by itself is noted once)
    t = threading.Thread(target=d.watch, args=(st, up, 0.01))
    t.start()
    t.join(5)
    assert not t.is_alive() and handed == [{"installer": "x.exe", "version": "2026.12.1"}]
    assert log_text(state).count("the server stopped by itself (exit code 3)") == 1
    st = d.Session()                                         # no update module: it just waits for the end
    t = threading.Thread(target=d.watch, args=(st, None, 0.01))
    t.start()
    st.stop.set()
    t.join(5)
    assert not t.is_alive()
    monkeypatch.setitem(sys.modules, "gridsim.update", None)
    assert d._update_module() is None


# ---- children that must outlive the window ---------------------------------------------------------------------------

def test_the_autopilot_run_leaves_the_windows_job_and_still_starts_where_that_is_refused(tmp_path, monkeypatch):
    from gridsim import autopilot as ap
    from gridsim.config import load_config
    monkeypatch.setenv("GRIDSIM_DATA", str(tmp_path / "GridSim Data"))
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = load_config()
    monkeypatch.setattr(ap, "running", lambda cfg_: False)
    monkeypatch.setattr(ap, "due", lambda cfg_: [{"key": "prices", "what": "x", "why": "y"}])
    calls = []

    def popen(cmd, **kw):
        calls.append((cmd, kw))
        if kw.get("creationflags", 0) & BREAKAWAY and refuse:
            raise PermissionError(5, "Access is denied")
        return FakeProc()
    monkeypatch.setattr(ap.subprocess, "Popen", popen)
    refuse = False
    monkeypatch.setattr(ap, "_LAST_LOOK", 0.0)
    assert ap.start_if_due(cfg) == "started"                 # here (not Windows): its own session, no flags
    assert calls[-1][1].get("start_new_session") is True or sys.platform == "win32"
    monkeypatch.setattr(ap.sys, "platform", "win32")
    monkeypatch.setattr(ap, "_LAST_LOOK", 0.0)
    assert ap.start_if_due(cfg) == "started"
    assert calls[-1][1]["creationflags"] == 0x08000000 | 0x00000200 | 0x00004000 | BREAKAWAY
    refuse = True
    monkeypatch.setattr(ap, "_LAST_LOOK", 0.0)
    n = len(calls)
    assert ap.start_if_due(cfg) == "started" and len(calls) == n + 2
    assert calls[-1][1]["creationflags"] == 0x08000000 | 0x00000200 | 0x00004000       # the old way
    assert calls[-1][0] == calls[-2][0] and calls[-1][0][1:] == ["-m", "gridsim", "autopilot"]


def test_the_background_tune_asks_to_be_left_out_of_the_windows_job_too():
    src = (ROOT / "app" / "pages" / "weekly.py").read_text(encoding="utf-8")
    start = src.index('"gridsim", "tune"')
    block = src[start:start + 1800]
    assert "0x08000000 | 0x00000200 | 0x00004000 | 0x01000000" in block      # ... | leave the window's job
    assert "except OSError" in block and "&= ~0x01000000" in block           # refused: started without


def test_the_streamlit_settings_file_says_who_overrides_it():
    text = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert "gridsim/desktop.py" in text and "Start GridSim.bat" in text
    import tomllib
    cfg = tomllib.loads(text)
    assert cfg["server"]["fileWatcherType"] == "none" and cfg["browser"]["gatherUsageStats"] is False
