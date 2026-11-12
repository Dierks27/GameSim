@echo off
setlocal
title GridSim - allow phone access
cd /d "%~dp0"

rem Opens GridSim's port (8501) to phones and tablets on your own home network. Nothing is opened to the internet:
rem the rule applies to networks marked "Private" only. Needs administrator rights, so Windows asks once.

net session >nul 2>&1
if errorlevel 1 goto elevate
goto work

:elevate
echo.
echo  Windows will now ask for permission - this adds one firewall rule for GridSim.
echo.
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
if errorlevel 1 goto denied
exit /b

:denied
echo.
echo  Permission was not given, so nothing was changed.
echo  Run this again and choose Yes. It needs an administrator account.
goto end

:work
echo.
echo  [1/3] Firewall rule: let devices on your home network reach GridSim on this PC.
netsh advfirewall firewall delete rule name="GridSim phone access" >nul 2>&1
netsh advfirewall firewall add rule name="GridSim phone access" dir=in action=allow protocol=TCP localport=8501 profile=private >nul
if errorlevel 1 (
    echo        The rule could not be added. Take a screenshot of this window.
    goto end
)
echo        Done.
echo.
echo  [2/3] How Windows sees the network this PC is on now:
powershell -NoProfile -Command "Get-NetConnectionProfile | ForEach-Object { '        ' + $_.Name + ': ' + $_.NetworkCategory }"
echo.
echo        The phone can only connect when this says Private. Public is right for cafes and
echo        airports, wrong for your own home network.
echo.
choice /C YN /M "       Is this your own home network, and should it be set to Private"
if errorlevel 2 goto address
powershell -NoProfile -Command "Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq 'Public' } | Set-NetConnectionProfile -NetworkCategory Private"
echo        Done.

:address
echo.
echo  [3/3] With GridSim running, type one of these into the phone's browser (same Wi-Fi):
powershell -NoProfile -Command "Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | ForEach-Object { '        http://' + $_.IPAddress + ':8501' }"
echo.
echo  GridSim has no password, so this is for your own home network only.

:end
echo.
pause
