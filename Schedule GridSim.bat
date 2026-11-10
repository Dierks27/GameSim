@echo off
rem ===========================================================================
rem  Schedule GridSim (optional): adds Windows scheduled tasks so the weekly
rem  routine runs by itself, in the background (no windows pop up; output goes
rem  to logs\scheduled.log in your data folder, %USERPROFILE%\GridSim Data).
rem    GridSim Weekly    Tuesday 9:00    refresh data, grade last week, check-up, lock in next week
rem    GridSim Predict   Friday 19:00    fresh locked-in predictions after the final injury reports
rem    GridSim Scan      every day, every 30 min, 8:00-24:00
rem                      compare Kalshi prices with the sims, save prices for grading
rem                      (phone alerts only if you set up NTFY_TOPIC, see GETTING_STARTED)
rem  They run while you are signed in to Windows (a locked screen is fine). A run
rem  missed because the PC was off or asleep happens as soon as it is back on.
rem  Run this file again any time to replace the tasks, or answer R to remove them.
rem ===========================================================================
setlocal DisableDelayedExpansion
cd /d "%~dp0"
title GridSim schedule
set "PYW=%~dp0.venv\Scripts\pythonw.exe"
set "PYW_OK="
if exist "%PYW%" set "PYW_OK=1"
setlocal EnableDelayedExpansion
if not defined PYW_OK (
    echo  GridSim is not set up yet. Double-click "Setup GridSim" first.
    pause
    exit /b 1
)
echo.
echo  A = add or replace the GridSim scheduled tasks
echo  R = remove them
choice /C AR /N /M "  Your choice [A/R]: "
if errorlevel 2 goto :remove

schtasks /Create /F /TN "GridSim Weekly" /SC WEEKLY /D TUE /ST 09:00 /TR "\"!PYW!\" -m gridsim weekly" || goto :schedfail
schtasks /Create /F /TN "GridSim Predict" /SC WEEKLY /D FRI /ST 19:00 /TR "\"!PYW!\" -m gridsim predict-week" || goto :schedfail
schtasks /Create /F /TN "GridSim Scan" /SC WEEKLY /D MON,TUE,WED,THU,FRI,SAT,SUN /ST 08:00 /RI 30 /DU 16:00 /TR "\"!PYW!\" -m gridsim scan --notify" || goto :schedfail
rem Laptop-friendly settings: run on battery, and catch up a run missed while the PC was off or asleep.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$a = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; $b = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew; Set-ScheduledTask -TaskName 'GridSim Weekly' -Settings $a | Out-Null; Set-ScheduledTask -TaskName 'GridSim Predict' -Settings $a | Out-Null; Set-ScheduledTask -TaskName 'GridSim Scan' -Settings $b | Out-Null"
if errorlevel 1 echo  Note: the tasks were added, but their laptop settings could not be changed - they still work.
echo.
echo  Done. In the Windows "Task Scheduler" app they are listed as
echo  GridSim Weekly, GridSim Predict and GridSim Scan.
echo  What they did is written to logs\scheduled.log in your GridSim Data folder.
pause
exit /b 0

:schedfail
echo.
echo  [!] Windows would not create the scheduled tasks - see the message above.
echo      Take a screenshot of this window if you want help with it.
pause
exit /b 1

:remove
for %%T in ("GridSim Weekly" "GridSim Predict" "GridSim Scan") do schtasks /Delete /F /TN %%T >nul 2>nul
echo  Removed the GridSim scheduled tasks.
pause
exit /b 0
