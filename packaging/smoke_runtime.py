"""Checks that the private Python of the installed program works from wherever it was put.

Run BY that Python (windows-installer workflow, tests 02 and 05), from an unrelated working directory:
    <root>\\python\\python.exe <repo>\\packaging\\smoke_runtime.py

Every check runs even when an earlier one failed, and each prints one line:
    OK    <what>
    FAIL  <what>: <what was found>
    NOTE  <a fact worth knowing that is not a failure>
The exit code is 0 only when nothing failed.
"""
from __future__ import annotations

import importlib
import os
import pathlib
import subprocess
import sys
import tempfile

FAILED: list[str] = []

# every package with compiled parts that GridSim, the app or the window needs, plus the standard-library modules
# that depend on DLLs shipped next to python.exe
NATIVE = ["numpy", "scipy.stats", "scipy.optimize", "polars", "pyarrow.parquet", "pandas", "plotly.graph_objects",
          "optuna", "streamlit", "yaml", "jinja2", "nflreadpy", "ssl", "sqlite3", "ctypes", "zoneinfo",
          "webview", "clr_loader"]

# What packaging/ci/build.ps1 writes into python313._pth. No "import site" line: sys.path is exactly these four
# folders, so nothing else on the PC (PYTHONPATH, the registry, another Python's per-user site-packages) can get in.
PTH_LINES = ["Lib", "DLLs", "Lib\\site-packages", "..\\program"]


def check(what: str, fn) -> None:
    try:
        detail = fn()
    except BaseException as e:                         # an assert, an ImportError, a failed child: all are findings
        FAILED.append(what)
        print(f"FAIL  {what}: {type(e).__name__}: {e}", flush=True)
    else:
        print(f"OK    {what}" + (f"  ({detail})" if detail else ""), flush=True)


def note(text: str) -> None:
    print(f"NOTE  {text}", flush=True)


def main() -> int:
    exe = pathlib.Path(sys.executable).resolve()
    root = exe.parent.parent
    print(f"python {sys.version}", flush=True)
    print(f"executable {sys.executable}  (resolved: {exe})", flush=True)
    print(f"root {root}", flush=True)
    for p in sys.path:
        print(f"sys.path  {p}", flush=True)
    if sys.platform != "win32":
        print("FAIL  this check is for the Windows program only")
        return 1

    def isolated():
        f = sys.flags
        assert f.isolated and f.ignore_environment and f.safe_path and f.no_site, f"flags {f}"
        return "isolated, environment ignored, safe path, no site import"

    def prefix():
        assert pathlib.Path(sys.prefix).resolve() == root / "python", f"sys.prefix is {sys.prefix}"
        assert pathlib.Path(sys.base_prefix).resolve() == root / "python", f"sys.base_prefix is {sys.base_prefix}"
        return sys.prefix

    def path_file():
        pth = exe.parent / "python313._pth"
        assert pth.is_file(), f"{pth} is missing"
        lines = [x.strip() for x in pth.read_text(encoding="ascii").splitlines() if x.strip()]
        assert lines == PTH_LINES, f"python313._pth holds {lines}"
        return ", ".join(lines)

    def nothing_outside():
        outside = [p for p in sys.path if not pathlib.Path(p).resolve().is_relative_to(root)]
        assert not outside, f"sys.path entries outside the program folder: {outside}"
        return f"{len(sys.path)} entries, all under the program folder"

    def user_site_ignored():
        # smoke.ps1 plants gridsim_ci_usersite_probe.py in %APPDATA%\Python\Python313\site-packages, the folder a
        # "pip install --user" of another Python 3.13 on the same PC would have filled. A path file with an
        # "import site" line would put that folder on sys.path (CPython leaves the per-user site switched on).
        import site
        user_site = site.getusersitepackages()
        planted = os.path.isfile(os.path.join(user_site, "gridsim_ci_usersite_probe.py"))
        try:
            importlib.import_module("gridsim_ci_usersite_probe")
        except ImportError:
            return f"{user_site}: " + ("a module planted there is not importable" if planted else "nothing planted")
        raise AssertionError(f"a module planted in {user_site} was importable")

    def environment_ignored():
        bad = [k for k in ("PYTHONHOME", "PYTHONPATH") if os.environ.get(k)]
        hits = [p for p in sys.path if "does-not-exist" in p.lower()]
        assert not hits, f"PYTHONHOME/PYTHONPATH leaked into sys.path: {hits}"
        return f"set in the environment and ignored: {', '.join(bad) or 'none were set'}"

    check("isolated mode (python313._pth is honoured)", isolated)
    check("sys.prefix is the private Python", prefix)
    check("python313._pth content", path_file)
    check("no sys.path entry outside the program folder", nothing_outside)
    check("PYTHONHOME and PYTHONPATH are ignored", environment_ignored)
    check("the per-user site-packages of another Python is ignored", user_site_ignored)

    for name in NATIVE:
        def imp(name=name):
            mod = importlib.import_module(name)
            where = getattr(mod, "__file__", None)
            if where:
                assert pathlib.Path(where).resolve().is_relative_to(root), f"imported from {where}"
            return getattr(importlib.import_module(name.split(".")[0]), "__version__", "")
        check(f"import {name}", imp)

    def tz():
        import zoneinfo
        return str(zoneinfo.ZoneInfo("America/New_York"))

    def dotnet():
        import clr                                     # pythonnet: loads the .NET Framework that is part of Windows
        import System
        return f"pythonnet on {System.Environment.Version}"

    def tls():
        import ssl
        ctx = ssl.create_default_context()             # reads the Windows certificate store
        return f"{ssl.OPENSSL_VERSION}, {len(ctx.get_ca_certs())} root certificates loaded so far"

    check("time zone data (America/New_York)", tz)
    check("pythonnet starts (import clr)", dotnet)
    check("TLS context from the Windows certificate store", tls)

    def gridsim_from_program():
        import gridsim
        where = pathlib.Path(gridsim.__file__).resolve()
        assert where.is_relative_to(root / "program"), f"gridsim imported from {where}"
        return f"gridsim {gridsim.__version__}"

    def installed_layout():
        from gridsim import installed
        found = installed.install_root()
        assert found is not None and pathlib.Path(found).resolve() == root, f"install_root() says {found}"
        assert installed.is_installed()
        pyw = pathlib.Path(installed.pythonw_exe())
        assert pyw.name.lower() == "pythonw.exe" and pyw.is_file(), f"pythonw_exe() says {pyw}"
        return f"install_root() = {found}"

    check("gridsim is imported from <root>\\program", gridsim_from_program)
    check("gridsim.installed recognises the installed layout", installed_layout)

    pyw = str(exe.with_name("pythonw.exe"))
    cwd = os.environ.get("SystemRoot", r"C:\Windows")

    # The two pythonw children below get NO standard streams (nothing is passed for stdin/stdout/stderr, and
    # subprocess then lets them inherit none): that is how the shortcut and Task Scheduler start pythonw, and it is
    # the harder case (sys.stdout is None). Handing them a stream, even the null device, would give them a real
    # sys.stdout and test the easy case only.
    def windowless_cli():
        r = subprocess.run([pyw, "-m", "gridsim", "--version"], cwd=cwd, timeout=300)
        assert r.returncode == 0, f"exit code {r.returncode}"
        return "exit code 0 from an unrelated working directory"

    def windowless_pool():
        out = pathlib.Path(tempfile.gettempdir()) / f"gridsim_mp_{os.getpid()}.txt"
        code = ("import sys, math, multiprocessing as mp\n"
                "from concurrent.futures import ProcessPoolExecutor\n"
                "if __name__ == '__main__':\n"
                "    with ProcessPoolExecutor(2, mp_context=mp.get_context('spawn')) as ex:\n"
                "        r = list(ex.map(math.sqrt, [4.0, 9.0]))\n"
                "    open(sys.argv[1], 'w').write(f'{r} {sys.stdout!r} {sys.executable}')\n")
        out.unlink(missing_ok=True)
        r = subprocess.run([pyw, "-c", code, str(out)], cwd=cwd, timeout=300)
        assert r.returncode == 0, f"exit code {r.returncode}"
        txt = out.read_text()
        out.unlink(missing_ok=True)
        assert txt.startswith("[2.0, 3.0] "), f"the child wrote {txt!r}"
        assert txt.lower().endswith("pythonw.exe"), f"the child ran as {txt!r}"
        if not txt.startswith("[2.0, 3.0] None "):
            # not a fault of the program: only that this run did not exercise the no-streams case
            note(f"the pythonw child had a standard output after all, so the pool ran in the easy case: {txt}")
        return txt

    check("pythonw -m gridsim --version", windowless_cli)
    check("a spawn process pool under pythonw", windowless_pool)

    # ---- facts, not failures ---------------------------------------------------------------------------------------
    if (exe.parent / "Scripts").exists():
        note("python\\Scripts exists (its .exe launchers hold the build machine's path; the build should remove it)")
    pth_files = sorted(p.name for p in (exe.parent / "Lib" / "site-packages").glob("*.pth"))
    note(f".pth files in site-packages (not processed, because site is not imported): "
         f"{', '.join(pth_files) or 'none'}")

    if FAILED:
        print(f"runtime check: {len(FAILED)} FAILED: " + "; ".join(FAILED), flush=True)
        return 1
    print("runtime OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
