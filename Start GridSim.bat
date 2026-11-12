@echo off
rem ---------------------------------------------------------------------------
rem  Start GridSim: launches the GridSim app in a window of its own
rem  (Microsoft Edge's app window; your normal browser if that is not there).
rem  Double-click this file (or the GridSim icon that Setup put on your desktop).
rem  To stop GridSim, close this black window.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"
title GridSim - close this window to stop
set "VPY=.venv\Scripts\python.exe"
set "VPYW=.venv\Scripts\pythonw.exe"
if not exist "%VPYW%" set "VPYW=%VPY%"
if not exist "%VPY%" (
    echo.
    echo  GridSim is not set up yet on this PC.
    echo  Double-click "Setup GridSim" in this folder first, then try again.
    echo.
    pause
    exit /b 1
)

rem Already running in another window? Then just open the browser to it.
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>nul
if not errorlevel 1 (
    echo  GridSim is already running - opening its window.
    start "" "%VPYW%" -m gridsim.appwindow
    timeout /t 4 >nul
    exit /b 0
)

echo.
"%VPY%" -c "import gridsim; print('  GridSim version ' + gridsim.__version__)"
echo  Starting GridSim ... its window opens in a few seconds.
echo  Leave this window open while you use GridSim; close it to stop.
echo  For your phone (same Wi-Fi): use the "Network URL" line shown below.
echo.
rem The window is opened by a small helper once the server answers (so the server itself opens no browser tab).
start "" "%VPYW%" -m gridsim.appwindow
"%VPY%" -m streamlit run app\streamlit_app.py --server.address 0.0.0.0 --server.port 8501 --server.showEmailPrompt false --server.headless true
if errorlevel 1 pause
