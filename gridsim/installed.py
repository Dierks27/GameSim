"""Where GridSim is running from: the installed Windows program, or a source folder.

The installed program (GridSim-Setup.exe) lays out
    <root>\\python\\python.exe, pythonw.exe      a private Python with everything GridSim needs
    <root>\\program\\gridsim, app, config.yaml   the same files as the source folder
    <root>\\gridsim.ico
and keeps its own small state (logs, the update download, the window's browser profile) under
%LOCALAPPDATA%\\GridSim — NOT the user's data, which stays in the data folder (config.data_dir()).
A source install (a folder with Setup GridSim.bat / a .venv) has none of this; everything here then answers
"not installed" and the callers fall back to what they did before.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ID = "Dierks27.GridSim"
REPO = "Dierks27/GameSim"
TASK_NAMES = ("GridSim Weekly", "GridSim Predict", "GridSim Scan", "GridSim Watch")


def state_dir() -> Path:
    """The program's own small state folder (created on demand). GRIDSIM_STATE overrides it (tests, CI)."""
    env = os.environ.get("GRIDSIM_STATE", "").strip().strip('"')
    d = Path(env) if env else Path(os.environ.get("LOCALAPPDATA") or (Path.home() / ".local" / "state")) / "GridSim"
    d.mkdir(parents=True, exist_ok=True)
    return d


def install_root(executable: str | None = None) -> Path | None:
    """<root> of the installed program when this Python is its private one, else None."""
    exe = Path(executable or sys.executable).resolve()
    root = exe.parent.parent
    if exe.parent.name.lower() == "python" and (root / "program" / "gridsim").is_dir():
        return root
    return None


def is_installed() -> bool:
    return install_root() is not None


def program_dir() -> Path:
    """The folder that holds gridsim/, app/ and config.yaml (installed or source)."""
    return Path(__file__).resolve().parent.parent


def python_exe() -> str:
    """The console Python next to the one that is running (python.exe even when started by pythonw.exe)."""
    exe = Path(sys.executable)
    cand = exe.with_name("python.exe") if exe.name.lower().startswith("pythonw") else exe
    return str(cand if cand.exists() else exe)


def pythonw_exe() -> str:
    """The windowless Python next to the one that is running (falls back to the running one)."""
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    return str(cand if cand.exists() else exe)
