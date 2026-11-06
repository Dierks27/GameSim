@echo off
rem ---------------------------------------------------------------------------
rem  Start GridSim: launches the GridSim app in your web browser.
rem  Double-click this file (or the GridSim icon that Setup put on your desktop).
rem  Phone on the same Wi-Fi: http://<this PC's IP address>:8501
rem  To stop GridSim, close this black window.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"
title GridSim - close this window to stop
set "VPY=.venv\Scripts\python.exe"
if not exist "%VPY%" (
    echo.
    echo  GridSim is not set up yet on this PC.
    echo  Double-click "Setup GridSim" in this folder first, then try again.
    echo.
    pause
    exit /b 1
)
echo.
echo  Starting GridSim ... your browser will open in a few seconds.
echo  Leave this window open while you use GridSim; close it to stop.
echo  On your phone (same Wi-Fi): http://THIS-PC-IP:8501
echo.
"%VPY%" -m streamlit run app\streamlit_app.py --server.address 0.0.0.0 --server.port 8501
if errorlevel 1 pause
