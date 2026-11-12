# packaging: the GridSim Windows installer

Everything that turns this repository into `GridSim-Setup.exe`, and the tests that prove the result on a real
Windows machine. Nothing here runs on the user's PC except `stop-gridsim.ps1` (inside the installer) and the icon.

**None of this can run where GridSim is developed (Linux).** GitHub Actions on `windows-2025` is the only test
bench: `.github/workflows/windows-installer.yml` calls the four scripts in `ci/` and nothing else. What can be
checked on Linux is in `tests/test_packaging.py` (script syntax through `tests/ps_lint.py`, names and paths that
must agree with `gridsim/installed.py`, the export rules, the icon).

| File | What it is |
|---|---|
| `ci/probe.ps1` | Test 00: facts about the build machine (`ci-logs\probe.txt`). Never fails the run. |
| `ci/build.ps1` | Private Python + libraries + GridSim's files → `dist\GridSim`, then Inno Setup → `dist\GridSim-Setup.exe`. |
| `ci/smoke.ps1`, `ci/smoke/*` | Installs the result and runs tests 01–17. Results: `ci-logs\summary.txt`. |
| `ci/release.ps1` | Publishes the installer as the GitHub Release `v<version>` (only after a clean smoke test). |
| `gridsim.iss` | The installer (Inno Setup 6.7). |
| `stop-gridsim.ps1` | Ends every process running from `<install folder>\python`; used by installer and uninstaller. |
| `export-ignore.gitattributes` | What the installed program does not ship (applied only by `build.ps1`). |
| `requirements-win.txt` | Windows-only libraries (the window) on top of `pyproject.toml`'s dependencies. |
| `print_deps.py` | Prints those dependencies for pip. |
| `smoke_runtime.py` | Run by the private Python: checks that it is isolated, complete and relocatable. |
| `gridsim.ico`, `make_icon.py` | The icon and the script that drew it. |

## What gets installed

```
%LOCALAPPDATA%\Programs\GridSim\
    python\      python-build-standalone CPython 3.13 with every library; python313._pth makes it see only itself
                 and ..\program (no "import site": nothing else on the PC can get onto sys.path)
    program\     gridsim\, app\, config.yaml, ... exactly as in the repository (git archive)
    gridsim.ico
    tools\stop-gridsim.ps1
    unins000.exe
```

The program's own state (logs, window profile, update download) is in `%LOCALAPPDATA%\GridSim`
(`gridsim.installed.state_dir()`); the user's data is in `C:\Users\<name>\GridSim Data` and is never touched by the
installer or the uninstaller.

## Reading a run

Open the run on GitHub → download **ci-logs** → `summary.txt`. One line per test: `PASS`, `FAIL` (with
`PROBLEM: expected ...; found ...` lines under it), `BLOCKED` (could not run because the install failed) or `SKIP`.
Every test runs even when an earlier one failed. Other files in `ci-logs`:

- `desktop-test<NN>.log`, `server-test<NN>.log`: what the launcher and the server wrote during that test;
- `inno-*.log`: the installer's and uninstaller's own logs; `iscc.txt`: the compiler's messages;
- `runtime-relocated.txt`, `runtime-installed.txt`: output of `smoke_runtime.py`;
- `task-*.xml`: the scheduled jobs as Windows stored them; `processes-*.txt`: process lists after a failed test;
- `state\`: the program's own files from the test state folder: `schedule.log` (every `schtasks` call and what
  Windows answered), `program.log` (what `gridsim schedule ...` printed when the installer and the uninstaller ran
  it), `registered-*.xml` (the task files the program handed to Windows), `update-*` (the update folder's logs);
- `build-facts.json`, `installed-packages.txt`, `signatures.csv`, `probe.txt`, `console-windows.txt`.

| Test | Proves | If it fails, look at |
|---|---|---|
| 01 | every library installed as a cp313 wheel, `pip check` clean | the Build step's log: which wheel is missing |
| 02 | the private Python runs from another folder and drive, sees nothing outside itself | `runtime-relocated.txt` |
| 03 | the Streamlit server starts from it and answers `ok` | `server-test03.*.log` |
| 04 | the installer was compiled | `iscc.txt` (an unknown directive is named there) |
| 05 | silent install, shortcuts, uninstall entry, no `.pyc` rewritten at first run | `inno-install-fresh.log`, `runtime-installed.txt` |
| 06 | the pywebview/WebView2 window really shows the app (`GRIDSIM_SMOKE=1`) | `desktop-test06.log` |
| 07 | closing the window stops the server and the launcher | `desktop-test07.log`, `windows-test07.txt` |
| 08 | no console window appeared during 06–11 | `console-windows.txt` |
| 09 | a killed launcher takes the server with it; a broken-away job survives | `desktop-test09.log` |
| 10 | a second start exits with code 0, one server only | `desktop-test10.log` |
| 11 | the Edge app-window and default-browser fallbacks | `desktop-test11-*.log` |
| 12 | `gridsim schedule --install/--status/--repoint/--remove`; `--status` says "Background jobs are on."; Windows runs the jobs | `task-*.xml`, `schedule-*.txt`, `state\schedule.log` |
| 13 | an old-style job and desktop icon are taken over by the install | `task-weekly-after-install.xml` |
| 14a/b | update over a running GridSim, by hand and started by the app itself | `inno-update-*.log`, `desktop-test14*.log` |
| 15a/b | GitHub answers the update check; `gridsim.update.download()` fetches a GitHub release file (15a: the Python runtime the build used; 15b: GridSim's own release, skipped until one exists); no "from the internet" mark | `update-check.txt` |
| 16 | uninstall removes program, icons, jobs; the data folder stays | `inno-uninstall.log` |
| 17 | which `.exe/.dll/.pyd` are signed (information) | `signatures.csv` |

The app would start its background autopilot (data download, simulations) in every launcher test. The smoke test
keeps that off by naming a sleeping placeholder process in `<test data>\logs\autopilot\run.pid`
(`Start-AutopilotPlaceholder` in `ci/smoke/lib.ps1`); the installed program itself is not changed for the test.

Two more things the tests do on purpose. Tests that end a launcher from outside (09, 14a, 14b) wait for its window
first, and the clean-up between tests removes a left-over `webview.starting` note: a launcher ended while its window
is still opening would otherwise make the next start use the Edge window once. And the update check gets the
workflow's token as `GRIDSIM_GITHUB_TOKEN` (the workflow passes it to the step as `GH_TOKEN`), because test
machines share GitHub's limit for anonymous questions.

## Routine changes

- **Pin the library versions** (after the first green run): copy `ci-logs\installed-packages.txt` to
  `packaging/constraints-win.txt`; `build.ps1` then passes it to pip with `-c`.
- **New Python runtime:** change `$PbsTag`, `$PbsAsset` and `$PbsSha256` in `build.ps1` together (the checksum is in
  the release's `SHA256SUMS`). A new minor version also changes the name `python313._pth` and the `cpython-313` names
  in `build.ps1` and `smoke_runtime.py`.
- **A library that ships a `.pth` file** stops the build on purpose (the private Python does not read them): add the
  folder it needs to `$PthLines` in `build.ps1` and `PTH_LINES` in `smoke_runtime.py`.
- **Release:** bump `gridsim.__version__` and `pyproject.toml` together and push a commit whose message contains
  `[release]`, or tick *publish* under Actions → windows-installer → Run workflow. The file is always named
  `GridSim-Setup.exe`, so `https://github.com/Dierks27/GameSim/releases/latest/download/GridSim-Setup.exe` never
  changes. Never publish a trial build as a normal release: installed copies would be offered it.
- **Never change `AppId`** in `gridsim.iss`; it is what makes a new installer an update of the old one.
- **The icon:** edit `make_icon.py`, run it (needs Pillow), commit the new `gridsim.ico`.
