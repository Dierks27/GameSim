@echo off
rem ===========================================================================
rem  Setup GridSim  -  run this ONCE (double-click it). Safe to run again.
rem    1. finds Python 3.11 or newer
rem    2. creates a private Python environment in the .venv folder
rem    3. installs GridSim and everything it needs
rem    4. downloads the NFL data (a few minutes, about 150 MB)
rem    5. puts a "GridSim" icon on your desktop
rem ===========================================================================
setlocal
cd /d "%~dp0"
title GridSim setup
echo.
echo  ===============================================
echo    GridSim one-time setup  (about 5-10 minutes)
echo  ===============================================
echo.

rem ---- 1. find Python 3.11+ ---------------------------------------------------
set "PY="
set "HASPY="
where py >nul 2>nul && set "HASPY=1"
if defined HASPY call :try_py "py -3.13"
if defined HASPY if not defined PY call :try_py "py -3.12"
if defined HASPY if not defined PY call :try_py "py -3.11"
if not defined PY call :try_py "python"
if not defined PY (
    echo  [!] Python 3.11 or newer was not found.
    echo.
    echo      1. Your browser will open the Python download page.
    echo      2. Download "Python 3.12" for Windows and run the installer.
    echo      3. IMPORTANT: tick "Add python.exe to PATH" at the bottom of the first screen.
    echo      4. When it finishes, double-click "Setup GridSim" again.
    echo.
    start "" "https://www.python.org/downloads/windows/"
    pause
    exit /b 1
)
echo  [1/5] Found Python: %PY%

rem ---- 2. private environment -------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo  [2/5] Creating the GridSim Python environment ...
    %PY% -m venv .venv
    if errorlevel 1 goto :fail
) else (
    echo  [2/5] GridSim Python environment already exists.
)
set "VPY=.venv\Scripts\python.exe"

rem ---- 3. install ---------------------------------------------------------------
echo  [3/5] Installing GridSim and its libraries (needs internet) ...
"%VPY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
if errorlevel 1 goto :fail
"%VPY%" -m pip install -e . --quiet --disable-pip-version-check
if errorlevel 1 goto :fail

rem ---- 4. data ------------------------------------------------------------------
echo  [4/5] Downloading NFL data (play-by-play 2019-now, schedules, rosters, injuries) ...
"%VPY%" -m gridsim refresh-data
if errorlevel 1 (
    echo  [!] The data download did not finish. Check your internet connection, then run Setup again.
    goto :fail
)

rem ---- 5. desktop shortcut -----------------------------------------------------
echo  [5/5] Adding a "GridSim" icon to your desktop ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'GridSim.lnk')); $s.TargetPath=(Join-Path (Get-Location) 'Start GridSim.bat'); $s.WorkingDirectory=(Get-Location).Path; $s.IconLocation=$env:SystemRoot+'\System32\imageres.dll,13'; $s.Description='Start the GridSim NFL simulator'; $s.Save()"
if errorlevel 1 echo      (Could not create the shortcut - you can always double-click "Start GridSim" in this folder.)

echo.
echo  ===============================================
echo    Done!  Double-click the GridSim icon on your
echo    desktop (or "Start GridSim" in this folder).
echo  ===============================================
echo.
pause
exit /b 0

rem ---- helpers --------------------------------------------------------------------
:try_py
rem %~1 is a python command; accept it only if it runs and is version 3.11 or newer.
%~1 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=%~1"
exit /b 0

:fail
echo.
echo  [!] Setup stopped because of the error shown above.
echo      Take a screenshot of this window if you want help with it.
echo.
pause
exit /b 1
