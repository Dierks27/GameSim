"""The Windows installer's build and test scripts (packaging/, .github/workflows): what can be checked without Windows.

PowerShell, Inno Setup and the Windows program itself only ever run on GitHub's Windows machines. These tests read
the scripts as text: their syntax (tests/ps_lint.py), that they agree with each other and with gridsim/installed.py
on names and paths, and that the build stages the right files. No network, no subprocesses.
"""
from __future__ import annotations

import fnmatch
import importlib.util
import re
import struct
import tomllib
from pathlib import Path

import pytest
import yaml

import gridsim
import ps_lint
from gridsim import installed

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "packaging"
CI = PKG / "ci"
SMOKE = CI / "smoke"
APP_GUID = "8F6B5C0E-3B7A-4E0B-9C55-6C1D7A2E4F10"


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ps1_files() -> list[Path]:
    return sorted(PKG.rglob("*.ps1"))


# ---- syntax -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("script", _ps1_files(), ids=lambda p: str(p.relative_to(PKG)))
def test_a_powershell_script_has_none_of_the_mistakes_that_stop_it_before_its_first_line(script):
    assert ps_lint.problems(_text(script)) == []


def test_all_the_powershell_scripts_are_there():
    names = {str(p.relative_to(PKG)).replace("\\", "/") for p in _ps1_files()}
    assert {"stop-gridsim.ps1", "ci/probe.ps1", "ci/build.ps1", "ci/smoke.ps1", "ci/release.ps1",
            "ci/smoke/lib.ps1"} <= names


@pytest.mark.parametrize("snippet, word", [
    ('Write-Host "exit code $LASTEXITCODE: done"\n', "LASTEXITCODE"),          # "$name:" is read as a scope
    ("function A {\n  if ($x) { 1 \n}\n", "never closed"),
    ("Write-Host 'abc\n", "single-quoted"),
    ('Write-Host "a $(Get-Date b"\n', "never closed"),
    ("$a = @(1, 2]\n", "closes"),
    ("Get-Process | where { $_.Id }\n", "alias"),
    ("Get-Process | % { $_ }\n", "alias"),
    ("$pid = 5\n", "own variable"),
    ("param([string]$Host)\n", "own variable"),
    ("$script = 'x'\n", "scope name"),
    ('Write-Host "café"\n', "ASCII"),
    ("Get-Process `\n  -Name x\n", "backtick"),
    ("$x = @'\nabc\n'@\n", "here-string"),
    ("Expect (-not $p.HasExited) 'still running' \"it ended with exit code $($p.ExitCode)\"\n", "still running"),
])
def test_the_checker_catches_the_mistakes_it_is_there_for(snippet, word):
    found = ps_lint.problems(snippet)
    assert found and any(word in f for f in found), found


def test_the_checker_accepts_ordinary_powershell():
    good = "\n".join([
        '$x = "a $($y.z) b ${name}: c $env:PATH;$env:TEMP"',
        "if ($a -and (Test-Path -LiteralPath $b)) { Write-Host 'it''s' }   # a comment with ' and \" and (",
        '$h = @{ A = 1; B = "x)" }',
        '& "$env:SystemRoot\\System32\\tar.exe" -xzf $tgz -C $root',
        "$p = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\\ISCC.exe'",
        "foreach ($r in $list) { [math]::Round($r.Value, 1) }",
        'Get-Process | Where-Object { $_.Id -ne $PID } | ForEach-Object { "pid $($_.Id): $($_.Name)" }',
        "$script:Results = [System.Collections.Generic.List[object]]::new()",
        "$global:LASTEXITCODE = 0",
        'if (-not $p.HasExited) { Stop-Process -Id $p.Id } else { Write-Host "exit code $($p.ExitCode)" }',
        "if ($p.HasExited) { $state = \"it ended with exit code $($p.ExitCode)\" }",
    ]) + "\n"
    assert ps_lint.problems(good) == []


def test_the_python_helpers_compile_and_the_runtime_check_refuses_to_run_off_windows(capsys):
    for p in (PKG / "print_deps.py", PKG / "smoke_runtime.py", PKG / "make_icon.py", SMOKE / "spawn_under.py"):
        compile(_text(p), str(p), "exec")
    assert _load(PKG / "smoke_runtime.py").main() == 1
    assert "Windows program only" in capsys.readouterr().out
    assert _load(SMOKE / "spawn_under.py").main(["spawn_under.py", "1", "0", "x"]) == 1


def test_the_runtime_check_starts_its_windowless_children_the_way_windows_starts_them():
    # The shortcut and Task Scheduler start pythonw with no standard streams (sys.stdout is None): the hard case.
    # A child that is handed a stream - the null device included - has a real sys.stdout, so the check would test
    # the easy case and, where it looks for "None", fail on a healthy program.
    code = _text(PKG / "smoke_runtime.py")
    assert "subprocess.DEVNULL" not in code and "stdout=" not in code and "stderr=" not in code
    assert code.count("subprocess.run([pyw, ") == 2
    assert 'txt.startswith("[2.0, 3.0] ")' in code           # the result decides, not what the streams were


def test_the_scripts_the_workflow_runs_end_with_exit_code_zero_when_they_get_to_their_last_line():
    # GitHub ends a step with the exit code of the last program that ran. The clean-up runs taskkill and schtasks
    # on things that are usually not there (codes other than 0), so each script says "0" itself at its end.
    for name in ("probe.ps1", "build.ps1", "smoke.ps1", "release.ps1"):
        last = [x for x in _text(CI / name).splitlines() if x.strip()][-1]
        assert last == "exit 0", name
    for name in ("build.ps1", "smoke.ps1", "release.ps1"):
        assert "$global:LASTEXITCODE = 0\nexit 0" in _text(CI / name), name
    driver = _text(CI / "smoke.ps1")                         # ... and a failed test still ends the step red
    assert driver.index("throw \"The smoke test found problems in:") < driver.index("$global:LASTEXITCODE = 0\nexit 0")


def test_the_window_watcher_source_has_paired_braces():
    code = re.sub(r'"(?:[^"\\]|\\.)*"', '""', _text(SMOKE / "win32.cs"))
    code = re.sub(r"//[^\n]*", "", code)
    assert code.count("{") == code.count("}") and code.count("(") == code.count(")")
    assert "ConsoleWindowClass" in _text(SMOKE / "win32.cs") and "CASCADIA_HOSTING_WINDOW_CLASS" in _text(SMOKE / "win32.cs")


# ---- the build ----------------------------------------------------------------------------------------------------------

def test_print_deps_prints_exactly_the_dependencies_of_pyproject(capsys):
    mod = _load(PKG / "print_deps.py")
    with open(ROOT / "pyproject.toml", "rb") as fh:
        want = tomllib.load(fh)["project"]["dependencies"]
    assert mod.dependencies(ROOT / "pyproject.toml") == want
    assert mod.main(["print_deps.py"]) == 0
    assert capsys.readouterr().out.split("\n") == want + [""]
    assert any(d.startswith("streamlit") for d in want) and not any("gridsim" in d for d in want)


def test_the_window_libraries_are_pinned_and_the_one_source_package_is_allowed_in_the_build():
    lines = [x.strip() for x in _text(PKG / "requirements-win.txt").splitlines() if x.strip() and not x.startswith("#")]
    assert lines == ["pywebview==6.2.1", "pythonnet==3.0.5", "clr_loader==0.2.10", "streamlit>=1.62"]
    build = _text(CI / "build.ps1")
    # everything from wheels, except proxy_tools (pywebview needs it; PyPI has it only as source)
    assert "'--only-binary=:all:', '--no-binary=proxy_tools'" in build
    assert "'-r', $req, '-r', (Join-Path $packaging 'requirements-win.txt')" in build


def test_the_runtime_is_pinned_by_tag_file_name_and_checksum():
    build = _text(CI / "build.ps1")
    tag = re.search(r"\$PbsTag = '(\d{8})'", build).group(1)
    asset = re.search(r"\$PbsAsset = '([^']+)'", build).group(1)
    sha = re.search(r"\$PbsSha256 = '([0-9a-f]{64})'", build).group(1)
    assert asset == f"cpython-3.13.18+{tag}-x86_64-pc-windows-msvc-install_only_stripped.tar.gz" and len(sha) == 64
    assert "releases/latest" not in build                      # never "latest": the runtime must not change by itself
    assert "$hash -ne $PbsSha256" in build


def test_the_path_file_is_the_same_in_the_build_and_in_the_runtime_check_and_imports_no_site():
    build = _text(CI / "build.ps1")
    lines = re.findall(r"'([^']+)'", re.search(r"\$PthLines = @\(([^)]*)\)", build).group(1))
    assert lines == _load(PKG / "smoke_runtime.py").PTH_LINES == ["Lib", "DLLs", "Lib\\site-packages", "..\\program"]
    assert "python\\python313._pth" in build                    # named after the DLL, so pythonw.exe honours it too
    assert "import site" not in lines                           # would let in another Python's per-user site-packages


def test_the_shipped_pyc_files_are_forced_into_the_form_that_survives_the_install():
    build = _text(CI / "build.ps1")
    # pip writes time-based .pyc files unless told not to, and compileall keeps an existing one unless forced (-f):
    # either would leave .pyc files that the installed program judges stale and writes again at first import
    assert "'--no-compile'" in build
    assert "-m compileall -q -f -j 0 --invalidation-mode checked-hash" in build
    assert "if ($flags -ne 3)" in build                          # the build checks three .pyc headers itself


def test_the_build_stages_what_the_program_needs_and_nothing_else():
    rules = [x.split()[0] for x in _text(PKG / "export-ignore.gitattributes").splitlines()
             if x.strip() and not x.startswith("#") and x.split()[1:] == ["export-ignore"]]

    def left_out(path: str) -> bool:
        for r in rules:
            if r.startswith("/"):
                if path == r[1:] or path.startswith(r[1:] + "/"):
                    return True
            elif fnmatch.fnmatch(path.rsplit("/", 1)[-1], r):
                return True
        return False

    ships = ["gridsim/__init__.py", "gridsim/installed.py", "app/streamlit_app.py", "app/pages/settings.py",
             ".streamlit/config.toml", "config.yaml", "regime_changes.yaml", "README.md", "GETTING_STARTED.md",
             "pyproject.toml", "backtests/x/metrics.json", "tuning/x/summary.json", "diagnose/x/diagnose.json"]
    stays_out = ["tests/test_engine.py", "packaging/gridsim.iss", "packaging/ci/build.ps1",
                 ".github/workflows/windows-installer.yml", "Start GridSim.bat", "Setup GridSim.bat",
                 "Schedule GridSim.bat", "Allow phone access.bat", "DECISIONS.md", "SCHEMA_NOTES.md", ".gitattributes",
                 ".gitignore"]
    assert [p for p in ships if left_out(p)] == []
    assert [p for p in stays_out if not left_out(p)] == []
    for p in ships[:10]:
        assert (ROOT / p).exists(), p
    build = _text(CI / "build.ps1")
    assert "export-ignore.gitattributes" in build and "rev-parse --git-path info/attributes" in build
    # GitHub's "Download ZIP" honours export-ignore in the repository's own .gitattributes: with such a line there,
    # the ZIP (the old way of installing: Setup / Start / Schedule GridSim.bat) would lose those files.
    assert "export-ignore" not in "".join(x for x in _text(ROOT / ".gitattributes").splitlines() if not x.startswith("#"))
    for bat in ("Setup GridSim.bat", "Start GridSim.bat", "Schedule GridSim.bat"):
        assert (ROOT / bat).exists(), bat


def test_the_icon_is_a_real_multi_size_ico():
    data = (PKG / "gridsim.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind) == (0, 1) and count == 7
    sizes = []
    for i in range(count):
        w, h, _, _, planes, bpp, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        w, h = w or 256, h or 256
        assert w == h and planes == 1 and bpp == 32 and offset + size <= len(data)
        image = data[offset:offset + size]
        if w == 256:
            assert image[:8] == b"\x89PNG\r\n\x1a\n"                       # the large one as PNG
        else:
            head, bw, bh, _, bits = struct.unpack_from("<IiiHH", image, 0)
            assert (head, bw, bh, bits) == (40, w, 2 * h, 32)              # the others as 32-bit bitmaps
            assert len(image) == 40 + w * h * 4 + ((w + 31) // 32) * 4 * h
        sizes.append(w)
    assert sizes == [16, 24, 32, 48, 64, 128, 256]
    assert len(data) == 6 + 16 * count + sum(struct.unpack_from("<I", data, 6 + 16 * i + 8)[0] for i in range(count))


# ---- the installer script -------------------------------------------------------------------------------------------------

def test_the_installer_script_agrees_with_the_program_on_names_and_layout():
    iss = _text(PKG / "gridsim.iss")
    assert f"AppId={{{{{APP_GUID}}}" in iss                                  # never changes: it is what makes an update an update
    assert iss.count(f'AppUserModelID: "{installed.APP_ID}"') == 2          # Start menu and desktop: the taskbar groups by it
    assert f"https://github.com/{installed.REPO}" in iss
    for name in installed.TASK_NAMES:
        assert f"QuietTask('{name}');" in iss
    assert "PrivilegesRequired=lowest" in iss and "DefaultDirName={localappdata}\\Programs\\GridSim" in iss
    assert "OutputBaseFilename=GridSim-Setup" in iss and "DisableDirPage=yes" in iss
    assert "AppMutex" not in iss.split("[Setup]", 1)[1]                     # it would cancel a silent update of a running app
    assert "CloseApplications=no" in iss
    # the layout gridsim.installed.install_root() recognises: <root>\python\pythonw.exe and <root>\program\gridsim
    for line in ('DestDir: "{app}\\python"', 'DestDir: "{app}\\program"', 'DestDir: "{app}\\tools"',
                 'Filename: "{app}\\python\\pythonw.exe"; Parameters: "-m gridsim.desktop"; WorkingDir: "{app}\\program"',
                 'Parameters: "-m gridsim schedule --repoint"', 'Parameters: "-m gridsim schedule --remove"'):
        assert line in iss, line
    # an update wipes the old Python and the old code, never the downloaded data under program\data
    deleted = re.findall(r'Type: filesandordirs; Name: "([^"]+)"', iss.split("[InstallDelete]")[1].split("[Files]")[0])
    assert deleted == ["{app}\\python", "{app}\\program\\gridsim", "{app}\\program\\app"]
    assert "GridSim Data" not in "".join(x for x in iss.splitlines() if not x.lstrip().startswith(";"))
    assert iss.count("begin") == iss.count("end;")
    for section in ("[Setup]", "[InstallDelete]", "[Files]", "[Icons]", "[Run]", "[UninstallRun]", "[UninstallDelete]",
                    "[Code]"):
        assert len(re.findall(r"(?m)^" + re.escape(section) + r"$", iss)) == 1, section
    assert "[" not in "".join(re.findall(r"\{[^}#]*\}", iss.split("[Code]")[1]))      # no section name inside a comment
    assert "skipifnotsilent; Check: WantsRelaunch" in iss and "{param:RELAUNCH|0}" in iss


def test_the_stop_script_only_ends_processes_of_the_private_python_and_never_the_uninstaller():
    stop = _text(PKG / "stop-gridsim.ps1")
    assert "$prefix = $Dir.TrimEnd('\\') + '\\python\\'" in stop
    assert "-notlike 'unins*'" in stop and "$_.ProcessId -ne $PID" in stop
    # "still running" has a code of its own. 1 is what PowerShell ends with when it cannot run a script at all (a
    # policy, a virus scanner): were that taken for "still running", the installer would refuse for ever on such a PC.
    assert stop.rstrip().endswith("exit 3") and "exit 0" in stop and "exit 1" not in stop
    iss = _text(PKG / "gridsim.iss")
    assert iss.count("stop-gridsim.ps1") >= 4 and "(RC = 3)" in iss and "(RC = 1)" not in iss


def test_an_install_that_stops_because_gridsim_is_still_running_switches_the_jobs_back_on():
    iss = _text(PKG / "gridsim.iss")
    stopped = iss.split("(RC = 3) then", 1)[1]
    for name in installed.TASK_NAMES:                       # paused before the check, so given back when it stops
        assert iss.index(f"QuietTask('{name}');") < iss.index("(RC = 3) then")
        assert f"WakeTask('{name}');" in stopped
    assert stopped.index("WakeTask(") < stopped.index("Result := 'GridSim is still running")
    assert "/ENABLE" in iss.split("procedure WakeTask", 1)[1].split("end;", 1)[0]


# ---- the smoke test and the workflow --------------------------------------------------------------------------------------

def test_the_smoke_test_runs_every_planned_test_and_every_file_it_names_exists():
    driver = _text(CI / "smoke.ps1")
    parts = re.findall(r"Invoke-Part -File '([^']+)'", driver)
    assert parts == ["10-runtime.ps1", "20-install.ps1", "30-window.ps1", "40-process.ps1", "50-schedule.ps1",
                     "60-update.ps1", "70-uninstall.ps1"]
    ids = re.findall(r"Add-Result -Id '(\d\d)'", driver) + re.findall(r"Invoke-Test -Id '([^']+)'", driver)
    for part in parts:
        assert (SMOKE / part).exists(), part
        ids += re.findall(r"Invoke-Test -Id '([^']+)'", _text(SMOKE / part))
    assert sorted(ids) == ["00", "01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12", "13", "14a",
                           "14b", "15a", "15b", "16", "17"]
    assert len(ids) == len(set(ids))
    for helper in ("lib.ps1", "win32.cs", "spawn_under.py"):
        assert (SMOKE / helper).exists() and f"smoke\\{helper}" in driver + "".join(_text(SMOKE / p) for p in parts)
    # one failing test must not stop the others, and the run must still end red
    assert "Invoke-Quietly -What 'summary' -Do { Write-SummaryFile }" in driver
    assert "throw \"The smoke test found problems in:" in driver


def test_the_smoke_test_uses_the_names_of_the_contract():
    driver = _text(CI / "smoke.ps1")
    everything = driver + "".join(_text(p) for p in sorted(SMOKE.glob("*.ps1")))
    names = re.findall(r"'([^']+)'", re.search(r"\$ctx\.TaskNames = @\(([^)]*)\)", driver).group(1))
    assert tuple(names) == installed.TASK_NAMES
    assert f"{{{APP_GUID}}}_is1" in driver                                   # the uninstall entry of the installer's AppId
    assert "$env:GRIDSIM_DATA = $ctx.Data" in driver and "$env:GRIDSIM_STATE = $ctx.State" in driver
    assert "Join-Path $env:LOCALAPPDATA 'Programs\\GridSim'" in driver
    for needle in ("'-m gridsim.desktop'", "GRIDSIM_SMOKE", "GRIDSIM_WINDOW", "desktop.json", "desktop.log",
                   "server.log", "schedule --install", "schedule --remove", "schedule --repoint", "schedule --status",
                   "update --check", "update.request_install(", "update.pending()", "update.download(info)",
                   "update.check(force=True)", "/SILENT /NOCANCEL /NORESTART /RELAUNCH=1", "old_install.txt",
                   "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-", "_stcore/health"):
        assert needle in everything, needle
    for field in ("pid", "server_pid", "port", "host", "window", "started_utc"):
        assert f"'{field}'" in _text(SMOKE / "30-window.ps1")
    # The app-started update is requested under a version LATER than the installed one: gridsim.update.pending()
    # drops a request for a version that is not newer, and the launcher would then never hand over.
    upd = _text(SMOKE / "60-update.ps1")
    assert "$newer = '9999.1.1'" in upd and "update.request_install(r'$copy', '$newer')" in upd
    assert "request_install(r'$copy', '$($ctx.Version)')" not in upd
    # the release-dependent test skips, it does not fail, while there is no release
    assert "Skip-Test -Reason \"there is no published release" in _text(SMOKE / "60-update.ps1")
    # the test never points the installed program at a real data folder
    assert "Join-Path $temp 'GridSim Data'" in driver and "Join-Path $temp 'GridSim State'" in driver


def test_the_smoke_test_and_the_program_agree_where_the_parts_meet():
    lib, driver = _text(SMOKE / "lib.ps1"), _text(CI / "smoke.ps1")
    process, sched, upd = _text(SMOKE / "40-process.ps1"), _text(SMOKE / "50-schedule.ps1"), _text(SMOKE / "60-update.ps1")
    program = ROOT / "gridsim"

    # the first line of "schedule --status" is compared word for word
    said = "Background jobs are on."
    assert f"$firstLine -eq '{said}'" in sched and f'"{said}"' in _text(program / "schedule.py")

    # the update check takes its token from GRIDSIM_GITHUB_TOKEN; the workflow hands its own over as GH_TOKEN
    assert 'os.environ.get("GRIDSIM_GITHUB_TOKEN"' in _text(program / "update.py")
    assert "@{ GRIDSIM_GITHUB_TOKEN = $env:GH_TOKEN }" in lib
    assert "'-m gridsim update --check' -TimeoutSec 180 -Environment $tokenEnv" in upd
    assert "update.check(force=True)))' -TimeoutSec 180 -Environment (Get-GitHubTokenEnv)" in upd
    wf = yaml.safe_load(_text(ROOT / ".github" / "workflows" / "windows-installer.yml"))
    smoke_step = next(s for s in wf["jobs"]["build"]["steps"] if s.get("name") == "Smoke test")
    assert smoke_step["env"] == {"GH_TOKEN": "${{ github.token }}"}
    # "update --check" ends with 0 whatever happened, so the test also asks what the check found
    for state in ("available", "newest", "not_ready", "none_published"):
        assert f"'{state}'" in upd and f'"{state}"' in _text(program / "update.py")
    # a real download through the program's own code, of the file the build itself fetched from a GitHub release
    build = _text(CI / "build.ps1")
    for fact in ("pbs_url", "pbs_bytes", "pbs_sha256"):
        assert f"$facts.{fact} = " in build and f"$facts.{fact}" in upd

    # A launcher that is ended while its window is still opening leaves <state>\webview.starting, and the next
    # normal start then uses the Edge window once. Tests that end a launcher wait for the window first, and the
    # clean-up between tests takes a left-over note away.
    desktop = _text(program / "desktop.py")
    assert 'state_dir() / "webview.starting"' in desktop and "Join-Path $ctx.State 'webview.starting'" in lib
    assert process.index("Wait-GridSimWindow -LauncherPid $launcher.Id") < process.index("taskkill.exe /F /PID $launcher.Id")
    assert upd.index("Wait-GridSimWindow -LauncherPid $old.Id") < upd.index("Invoke-Setup -Arguments '/SILENT")
    assert upd.index("Wait-GridSimWindow -LauncherPid $launcherPid") < upd.index("update.request_install(")
    assert 'TITLE = "GridSim"' in desktop and "MainWindowTitle -like '*GridSim*'" in lib

    # the launcher's exit codes are part of the contract: 0 is the only pass, also for the two fallback windows
    assert "EXIT_OK, EXIT_NO_SERVER, EXIT_NO_WINDOW, EXIT_STILL_RUNNING = 0, 1, 2, 3" in desktop
    assert process.count("Expect ($launcher.ExitCode -eq 0)") == 2
    assert _text(SMOKE / "30-window.ps1").count("Expect ($launcher.ExitCode -eq 0)") == 2

    # what the program writes into its state folder, and what the run keeps of it
    for name in ("desktop.log", "server.log", "schedule.log", "program.log", "old_install.txt"):
        assert f"'{name}'" in driver, name
        assert f'"{name}"' in "".join(_text(program / f) for f in ("desktop.py", "schedule.py", "cli.py")), name
    assert "Join-Path $ctx.State 'tasks'" in driver and 'state_dir() / "tasks"' in _text(program / "schedule.py")
    # a scheduled job (not a command about the program itself) still writes logs\scheduled.log in the data folder
    from gridsim import cli
    assert cli.PROGRAM_COMMANDS == ("schedule", "update") and "weekly" not in cli.PROGRAM_COMMANDS
    assert "'^=== .*gridsim weekly\\s*$'" in sched and "logs\\scheduled.log" in sched


def test_the_workflow_is_thin_and_only_runs_the_scripts():
    wf = yaml.safe_load(_text(ROOT / ".github" / "workflows" / "windows-installer.yml"))
    on = wf.get("on", wf.get(True))                                           # YAML reads the key "on" as true
    assert on["workflow_dispatch"]["inputs"]["publish"]["type"] == "boolean"
    assert "packaging/**" in on["push"]["paths"] and "gridsim/**" in on["push"]["paths"]
    assert wf["permissions"] == {"contents": "write"}
    job = wf["jobs"]["build"]
    assert job["runs-on"] == "windows-2025" and job["defaults"]["run"]["shell"] == "pwsh"
    runs = [s["run"] for s in job["steps"] if "run" in s]
    assert runs == ["./packaging/ci/probe.ps1", "./packaging/ci/build.ps1", "./packaging/ci/smoke.ps1",
                    "./packaging/ci/release.ps1"]
    for r in runs:
        assert (ROOT / r[2:]).exists(), r
    steps = {s.get("name", s.get("uses")): s for s in job["steps"]}
    assert steps["Probe runner"]["continue-on-error"] is True
    publish = steps["Publish release"]
    assert "success()" in publish["if"] and "[release]" in publish["if"] and "inputs.publish" in publish["if"]
    assert steps["Upload installer"]["with"]["path"] == "dist/GridSim-Setup.exe"
    # the logs are the only way to see what happened on the Windows machine: they are uploaded whatever happened before
    assert steps["Upload logs"]["with"]["path"] == "ci-logs/" and "always()" in steps["Upload logs"]["if"]
    assert steps["Build"]["timeout-minutes"] + steps["Smoke test"]["timeout-minutes"] < job["timeout-minutes"]


def test_the_release_keeps_the_fixed_file_name_and_the_version_tag():
    rel = _text(CI / "release.ps1")
    assert "'dist\\GridSim-Setup.exe'" in rel and '$tag = "v$version"' in rel
    assert "import gridsim; print(gridsim.__version__)" in rel
    assert "--clobber" in rel and "'--latest'" in rel and "^(FAIL|BLOCKED)\\s" in rel
    version = gridsim.__version__
    assert re.fullmatch(r"\d{4}\.\d{1,2}\.\d{1,2}", version)                  # what the installer's version field accepts


def test_the_guide_explains_the_installer_before_the_manual_steps():
    guide = _text(ROOT / "GETTING_STARTED.md")
    easy, manual = guide.index("## The easy way: the GridSim installer"), guide.index("## Step 1 ")
    assert easy < manual
    section = guide[easy:manual]
    for needle in (f"https://github.com/{installed.REPO}/releases/latest/download/GridSim-Setup.exe",
                   "Smart App Control", "Windows protected your PC", "More info", "Run anyway", "Keep anyway",
                   "Update now", "Settings → Schedule", "phone_access", "Installed apps", "GridSim Data",
                   "Steps 1 to 4"):
        assert needle in section, needle
