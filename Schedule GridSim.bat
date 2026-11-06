@echo off
rem ===========================================================================
rem  Schedule GridSim (optional): adds Windows scheduled tasks so the weekly
rem  routine runs by itself. Your PC must be on (sleeping is fine if "wake"
rem  is allowed) at these times. Run again any time; it replaces the tasks.
rem    - Tuesday 9:00     weekly     refresh data, grade last week, diagnose, predict next week
rem    - Friday  19:00    predict    fresh locked-in predictions after the final injury reports
rem    - Thu-Sun every 30 min 8:00-24:00   scan   compare Kalshi prices with the sims (alerts only if
rem                                       you set up NTFY_TOPIC in a .env file, see GETTING_STARTED)
rem  To remove them later: double-click this file and answer R.
rem ===========================================================================
setlocal
cd /d "%~dp0"
title GridSim schedule
set "EXE=%~dp0.venv\Scripts\gridsim.exe"
if not exist "%EXE%" (
    echo  GridSim is not set up yet. Double-click "Setup GridSim" first.
    pause
    exit /b 1
)
echo.
echo  A = add / replace the GridSim scheduled tasks
echo  R = remove them
choice /C AR /N /M "  Your choice [A/R]: "
if errorlevel 2 goto :remove

schtasks /Create /F /TN "GridSim\Weekly" /SC WEEKLY /D TUE /ST 09:00 /TR "\"%EXE%\" weekly"
schtasks /Create /F /TN "GridSim\Predict" /SC WEEKLY /D FRI /ST 19:00 /TR "\"%EXE%\" predict-week"
schtasks /Create /F /TN "GridSim\Scan" /SC WEEKLY /D THU,FRI,SAT,SUN /ST 08:00 /RI 30 /DU 16:00 /TR "\"%EXE%\" scan --notify"
echo.
echo  Done. You can see them in the Windows "Task Scheduler" app under the GridSim folder.
pause
exit /b 0

:remove
schtasks /Delete /F /TN "GridSim\Weekly"
schtasks /Delete /F /TN "GridSim\Predict"
schtasks /Delete /F /TN "GridSim\Scan"
echo  Removed.
pause
exit /b 0
