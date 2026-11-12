# Tests 05 and 13: the first install, on a PC that still has the old folder install's scheduled job and icon.

try {
    Start-AutopilotPlaceholder
}
catch {
    Write-Host "the autopilot placeholder could not be started: $($_.Exception.Message)"
}

# ---- before the install: what the old way ("Schedule GridSim.bat", "Setup GridSim.bat") left on the PC --------------
$ctx.MigrationReady = $false
$ctx.MigrationError = ''
try {
    foreach ($name in $ctx.TaskNames) { Remove-Task -TaskName $name }
    $oldAction = '"C:\dummy\pythonw.exe" -m gridsim weekly'
    $made = & schtasks.exe /Create /F /TN 'GridSim Weekly' /SC WEEKLY /D TUE /ST '09:00' /TR $oldAction 2>&1
    if ($LASTEXITCODE -ne 0) { throw "schtasks /Create (the old way) ended with exit code ${LASTEXITCODE}: $made" }
    if (-not (Test-Path -LiteralPath $ctx.Desktop)) { New-Item -ItemType Directory -Force -Path $ctx.Desktop | Out-Null }
    $shell = New-Object -ComObject WScript.Shell
    $oldIcon = $shell.CreateShortcut($ctx.DesktopLink)
    $oldIcon.TargetPath = Join-Path $env:SystemRoot 'System32\cmd.exe'
    $oldIcon.Arguments = '/c rem the old GridSim icon'
    $oldIcon.Save()
    $ctx.MigrationReady = $true
    Write-Host 'planted: an old-style job "GridSim Weekly" (C:\dummy\pythonw.exe) and an old GridSim.lnk on the desktop'
}
catch {
    $ctx.MigrationError = "$($_.Exception.Message)"
    Write-Host "could not plant the old job and icon: $($ctx.MigrationError)"
}

Invoke-Test -Id '05' -Name 'Fresh silent install: files, shortcuts, uninstall entry, and the installed Python works' -Body {
    if (-not (Test-Path -LiteralPath $ctx.Setup)) {
        Stop-Test -Expected 'dist\GridSim-Setup.exe to install' -Found 'no such file (the build did not produce the installer)'
    }
    # the build machine runs on UTC; a user's PC does not. Shipped .pyc files must stay valid in another time zone.
    & tzutil.exe /s 'Central Standard Time' 2>&1 | Out-Null
    Note "time zone for the install and everything after it: $((& tzutil.exe /g)) (was $($ctx.OldTimeZone))"
    if (Test-Path -LiteralPath $ctx.App) { Note "the folder $($ctx.App) existed before the install" }

    $run = Invoke-Setup -Arguments '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-' -LogName 'inno-install-fresh.log'
    Expect ($run.ExitCode -eq 0) 'installer exit code 0' "exit code $($run.ExitCode) (see ci-logs\inno-install-fresh.log)"
    Note "the install took $($run.Seconds) s"

    foreach ($rel in @('python\python.exe', 'python\pythonw.exe', 'python\python313._pth', 'python\LICENSE.txt', 'program\gridsim\__init__.py', 'program\gridsim\installed.py', 'program\app\streamlit_app.py', 'program\config.yaml', 'program\regime_changes.yaml', 'program\.streamlit\config.toml', 'program\README.md', 'program\GETTING_STARTED.md', 'program\backtests', 'program\tuning', 'program\diagnose', 'gridsim.ico', 'tools\stop-gridsim.ps1', 'unins000.exe')) {
        Expect (Test-Path -LiteralPath (Join-Path $ctx.App $rel)) "$rel in the install folder" 'it is missing'
    }
    foreach ($rel in @('program\tests', 'program\packaging', 'program\.github', 'program\DECISIONS.md', 'program\Start GridSim.bat', 'python\Scripts')) {
        Expect (-not (Test-Path -LiteralPath (Join-Path $ctx.App $rel))) "no $rel in the install folder" 'it is there'
    }
    if ($run.ExitCode -eq 0 -and (Test-Path -LiteralPath $ctx.AppPy) -and (Test-Path -LiteralPath $ctx.AppPyw) -and (Test-Path -LiteralPath (Join-Path $ctx.Program 'gridsim\__init__.py'))) {
        $ctx.Installed = $true
    }
    else {
        Stop-Test -Expected "a complete install in $($ctx.App)" -Found 'see the problems above; the tests that need the installed program are skipped'
    }

    foreach ($link in @($ctx.StartLink, $ctx.DesktopLink)) {
        if (-not (Test-Path -LiteralPath $link)) {
            Expect $false "the shortcut $link" 'no such file'
            continue
        }
        $sc = Read-Shortcut -Path $link
        Expect ($sc.Target -ieq $ctx.AppPyw) "the shortcut $link to start $($ctx.AppPyw)" "$($sc.Target)"
        Expect ($sc.Arguments -eq '-m gridsim.desktop') "the shortcut's arguments to be '-m gridsim.desktop'" "'$($sc.Arguments)'"
        Expect ($sc.WorkDir -ieq $ctx.Program) "the shortcut to start in $($ctx.Program)" "$($sc.WorkDir)"
        Expect ($sc.Icon.StartsWith((Join-Path $ctx.App 'gridsim.ico'), [System.StringComparison]::OrdinalIgnoreCase)) "the shortcut's icon to be gridsim.ico" "$($sc.Icon)"
    }

    if (Test-Path -LiteralPath $ctx.UninstallKey) {
        $entry = Get-ItemProperty -LiteralPath $ctx.UninstallKey
        Expect ("$($entry.DisplayName)" -eq 'GridSim') "the entry under Installed apps to be named 'GridSim'" "'$($entry.DisplayName)'"
        Expect ("$($entry.DisplayVersion)" -eq $ctx.Version) "its version to be $($ctx.Version)" "'$($entry.DisplayVersion)'"
        Expect (("$($entry.InstallLocation)").TrimEnd('\') -ieq $ctx.App) "its install folder to be $($ctx.App)" "$($entry.InstallLocation)"
    }
    else {
        $machineKey = $ctx.UninstallKey -replace '^HKCU:', 'HKLM:'
        $found = 'no such registry key'
        if (Test-Path -LiteralPath $machineKey) { $found = 'the entry is under HKLM (installed for all users) instead' }
        Expect $false 'the per-user uninstall entry (HKCU ...\Uninstall\{8F6B5C0E-...}_is1)' $found
    }

    # the same runtime checks as test 02, now from where the installer put the program; and: does a first run
    # rewrite any shipped file (stale .pyc files would be rewritten, and a first start would be slow)?
    $libBefore = Get-TreeStamp -Dir (Join-Path $ctx.App 'python\Lib')
    $codeBefore = Get-TreeStamp -Dir (Join-Path $ctx.Program 'gridsim')
    $checker = '"' + (Join-Path $ctx.Repo 'packaging\smoke_runtime.py') + '"'
    $run = Invoke-Tool -File $ctx.AppPy -Arguments $checker -TimeoutSec 1200 -WorkDir $env:SystemRoot
    $code = $run.ExitCode
    $lines = Save-Text -Name 'runtime-installed.txt' -Lines $run.Lines
    foreach ($line in @($lines | Where-Object { $_ -like 'FAIL*' })) { Expect $false 'every check of packaging\smoke_runtime.py to pass from the installed place' $line }
    Expect ($code -eq 0) 'packaging\smoke_runtime.py to end with exit code 0 from the installed place (ci-logs\runtime-installed.txt)' "exit code $code; last line: $($lines | Select-Object -Last 1)"
    $changed = @(Compare-TreeStamp -Before $libBefore -After (Get-TreeStamp -Dir (Join-Path $ctx.App 'python\Lib')))
    $changed += @(Compare-TreeStamp -Before $codeBefore -After (Get-TreeStamp -Dir (Join-Path $ctx.Program 'gridsim')))
    Note "$($libBefore.Count) files under python\Lib, $($codeBefore.Count) under program\gridsim"
    if ($changed.Count -gt 0) {
        $null = Save-Text -Name 'files-changed-by-first-run.txt' -Lines $changed
    }
    Expect ($changed.Count -eq 0) 'a first run to add or rewrite no file under python\Lib and program\gridsim (the shipped .pyc files are valid)' "$($changed.Count) files, for example: $(($changed | Select-Object -First 4) -join '; ') (all in ci-logs\files-changed-by-first-run.txt)"
    Expect ((Get-Content -LiteralPath $ctx.Marker -Raw).Trim() -eq $ctx.MarkerText) 'the marker file in the data folder untouched by the install' 'it changed'
}

Invoke-Test -Id '13' -Name 'Moving over from the old folder install: the old scheduled job and the old desktop icon' -NeedsInstall -Body {
    if (-not $ctx.MigrationReady) {
        Stop-Test -Expected 'an old-style job and icon planted before the install' -Found "that failed: $($ctx.MigrationError)"
    }
    $task = Get-TaskFacts -TaskName 'GridSim Weekly'
    if ($null -eq $task) {
        Expect $false "the job 'GridSim Weekly' to exist after the install (it existed before)" 'no such job'
    }
    else {
        $null = Save-Text -Name 'task-weekly-after-install.xml' -Lines $task.Xml
        Expect ($task.Command -ieq $ctx.AppPyw) "the job 'GridSim Weekly' to start $($ctx.AppPyw)" "'$($task.Command)'"
        Expect ($task.Arguments -eq '-m gridsim weekly') "its arguments to be '-m gridsim weekly'" "'$($task.Arguments)'"
        Expect ($task.WorkingDirectory -ieq $ctx.Program) "its working directory to be $($ctx.Program)" "'$($task.WorkingDirectory)'"
        Expect $task.Enabled 'the job to be switched on again after the install (the installer pauses the jobs while it copies files)' 'it is disabled'
    }
    foreach ($name in @('GridSim Predict', 'GridSim Scan', 'GridSim Watch')) {
        Expect (-not (Test-TaskExists -TaskName $name)) "no job '$name' (this PC did not have it before the install)" 'the install created it'
    }
    if (Test-Path -LiteralPath $ctx.DesktopLink) {
        $sc = Read-Shortcut -Path $ctx.DesktopLink
        Expect ($sc.Target -ieq $ctx.AppPyw) 'the old desktop icon GridSim.lnk to be replaced by the new one' "it still starts $($sc.Target)"
    }
    else {
        Expect $false 'GridSim.lnk on the desktop' 'no such file'
    }
    $candidates = @((Join-Path $ctx.State 'old_install.txt'), (Join-Path $env:LOCALAPPDATA 'GridSim\old_install.txt'))
    $noted = @($candidates | Where-Object { Test-Path -LiteralPath $_ })
    if ($noted.Count -eq 0) {
        Expect $false "old_install.txt in the state folder ($($ctx.State)), holding the old program's folder C:\dummy" 'no such file'
    }
    else {
        $text = ("$(Get-Content -LiteralPath $noted[0] -Raw)").Trim()
        Expect ($text -like '*C:\dummy*') "old_install.txt to hold the old program's folder C:\dummy" "'$text' in $($noted[0])"
    }
}
