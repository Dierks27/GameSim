"""Open GridSim in a window of its own — no tabs, no address bar — instead of a tab in the web browser.

Uses the "app window" mode of Microsoft Edge (part of Windows 11) or Chrome; anything going wrong falls back to
the normal browser, so GridSim always opens somewhere. Started by "Start GridSim.bat" next to the server:
    pythonw -m gridsim.appwindow [url]
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

URL = "http://localhost:8501"


def wait_for(port: int = 8501, timeout: float = 120.0) -> bool:
    """Wait until the GridSim server answers on this PC."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def browser_exe() -> str | None:
    """Edge or Chrome on this PC (both have the app-window mode), or None."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        for name in ("msedge.exe", "chrome.exe"):
            for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(root, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{name}") as k:
                        path = winreg.QueryValue(k, None)
                    if path and Path(path).exists():
                        return path
                except OSError:
                    continue
    except ImportError:
        pass
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LocalAppData")):
        for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
            if base and (Path(base) / rel).exists():
                return str(Path(base) / rel)
    return None


def command(exe: str, url: str = URL) -> list[str]:
    return [exe, f"--app={url}", "--window-size=1500,950"]


def open_window(url: str = URL) -> str:
    """Returns "app" (own window) or "browser" (a normal tab)."""
    exe = browser_exe()
    if exe:
        try:
            subprocess.Popen(command(exe, url), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return "app"
        except OSError:
            pass
    webbrowser.open(url)
    return "browser"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    url = argv[0] if argv else URL
    try:
        port = int(url.rsplit(":", 1)[1].split("/")[0])
    except (IndexError, ValueError):
        port = 8501
    if not wait_for(port):               # the server never came up (its window shows why): no dead window later
        return 1
    open_window(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
