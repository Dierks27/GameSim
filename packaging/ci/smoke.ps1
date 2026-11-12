# The smoke test of the Windows program: installs what build.ps1 built on this (throw-away) Windows machine and
# checks it the way a user would meet it. GitHub Actions is the only place this ever runs.
#
#   packaging\ci\build.ps1   must have run first (dist\GridSim, dist\GridSim-Setup.exe)
#   packaging\ci\smoke.ps1
#
# Results: ci-logs\summary.txt - one line per test (PASS / FAIL / BLOCKED / SKIP), and under a failed test what
# was expected and what was found. Every test runs even when an earlier one failed; the script fails at the end
# if any test failed. Logs (the launcher's and the server's, the installer's, process lists) go to ci-logs\ always.
#
# The tests live in packaging\ci\smoke\*.ps1 and are run in this order:
#   10-runtime.ps1     01 libraries   02 the private Python works from any folder   03 the app server starts
#                      04 the installer exists
#   20-install.ps1     05 fresh silent install   13 moving over from the old folder install
#   30-window.ps1      06 the GridSim window shows the app   07 closing the window stops everything
#   40-process.ps1     09 nothing survives a killed launcher (except what should)   10 one GridSim at a time
#                      11 the fallback windows
#                      08 no console window flashed during 06-11
#   50-schedule.ps1    12 the scheduled jobs
#   60-update.ps1      14 update over a running GridSim (a: by hand, b: started by the app)
#                      15 the update download (a: no "from the internet" mark, b: against a published release)
#   65-autopilot.ps1   18 the installed program does its own upkeep for real (data, lock-in, prices, check-up)
#   70-uninstall.ps1   16 uninstall   17 which files are signed
# A mistake in one of those files (even a syntax error) is reported as a failure of that file; the others still run.
#
# It uses its own data folder and state folder (GRIDSIM_DATA, GRIDSIM_STATE under the runner's temp folder), so
# nothing here ever touches a real "GridSim Data" folder.
$ErrorActionPreference = 'Stop'

$ci = $PSScriptRoot
$repo = (Resolve-Path -LiteralPath (Join-Path $ci '..\..')).Path
if ($env:GITHUB_WORKSPACE -and (Test-Path -LiteralPath $env:GITHUB_WORKSPACE)) { $repo = (Resolve-Path -LiteralPath $env:GITHUB_WORKSPACE).Path }
$temp = $env:TEMP
if ($env:RUNNER_TEMP -and (Test-Path -LiteralPath $env:RUNNER_TEMP)) { $temp = $env:RUNNER_TEMP }
Set-Location -LiteralPath $repo

$ctx = @{}
$ctx.Ci = $ci
$ctx.Repo = $repo
$ctx.Temp = $temp
$ctx.Logs = Join-Path $repo 'ci-logs'
$ctx.Stage = Join-Path $repo 'dist\GridSim'
$ctx.StagePy = Join-Path $ctx.Stage 'python\python.exe'
$ctx.Setup = Join-Path $repo 'dist\GridSim-Setup.exe'
$ctx.App = Join-Path $env:LOCALAPPDATA 'Programs\GridSim'
$ctx.AppPy = Join-Path $ctx.App 'python\python.exe'
$ctx.AppPyw = Join-Path $ctx.App 'python\pythonw.exe'
$ctx.Program = Join-Path $ctx.App 'program'
$ctx.State = Join-Path $temp 'GridSim State'
$ctx.Data = Join-Path $temp 'GridSim Data'
$ctx.Marker = Join-Path $ctx.Data 'predictions\ci-marker.txt'
$ctx.MarkerText = 'planted by the smoke test; an install, an update and an uninstall must leave this file alone'
$ctx.TaskNames = @('GridSim Weekly', 'GridSim Predict', 'GridSim Scan', 'GridSim Watch')
$ctx.Desktop = [Environment]::GetFolderPath('Desktop')
$ctx.StartMenu = [Environment]::GetFolderPath('Programs')
$ctx.DesktopLink = Join-Path $ctx.Desktop 'GridSim.lnk'
$ctx.StartLink = Join-Path $ctx.StartMenu 'GridSim.lnk'
$ctx.UninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{8F6B5C0E-3B7A-4E0B-9C55-6C1D7A2E4F10}_is1'
$ctx.Commit = "$env:GITHUB_SHA"
$ctx.Version = '?'
$ctx.Installed = $false
$ctx.Placeholder = $null
$ctx.Copy = ''
$ctx.SubstDrive = ''
$ctx.StageRenamed = $false
$ctx.UserSiteProbe = ''
$ctx.UserEnvSet = $false
$ctx.BrowsersTouched = $false
$ctx.ConsoleWatch = $false
$ctx.ConsoleWatchError = ''
$ctx.OldTimeZone = ''

New-Item -ItemType Directory -Force -Path $ctx.Logs | Out-Null

. (Join-Path $ci 'smoke\lib.ps1')

function Invoke-Part {
    # Runs one file of tests. An error of the file itself (for example a syntax error) is recorded and the
    # next file still runs.
    param([string]$File)
    $path = Join-Path $ctx.Ci "smoke\$File"
    try {
        . $path
    }
    catch {
        $where = ''
        if ($_.InvocationInfo -and $_.InvocationInfo.ScriptLineNumber) { $where = " (line $($_.InvocationInfo.ScriptLineNumber))" }
        Write-Host "::error::packaging/ci/smoke/$File could not be run: $($_.Exception.Message)$where"
        Add-Result -Id $File -Name 'this file of tests could not be run to its end (a mistake in the test script itself)' -Status 'FAIL' -Seconds 0 -Problems @("$($_.Exception.Message)$where") -Notes @()
    }
}

$fatal = ''
try {
    # ---- preparation --------------------------------------------------------------------------------------------
    $env:GRIDSIM_DATA = $ctx.Data
    $env:GRIDSIM_STATE = $ctx.State
    foreach ($name in @('GRIDSIM_SMOKE', 'GRIDSIM_WINDOW')) { Set-EnvValue -Name $name -Value $null }
    foreach ($dir in @($ctx.State, $ctx.Data)) {
        if (Test-Path -LiteralPath $dir) { Remove-Item -LiteralPath $dir -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ctx.Marker) | Out-Null
    Set-Content -LiteralPath $ctx.Marker -Value $ctx.MarkerText -Encoding ascii
    $ctx.OldTimeZone = ("$(& tzutil.exe /g)").Trim()

    if (Test-Path -LiteralPath $ctx.StagePy) {
        $version = & $ctx.StagePy -c "import gridsim; print(gridsim.__version__)"
        if ($LASTEXITCODE -eq 0 -and $version) { $ctx.Version = ("$version").Trim() }
    }
    Write-Host "GridSim $($ctx.Version), commit $($ctx.Commit)"
    Write-Host "program under test: $($ctx.App)"
    Write-Host "test data folder:   $($ctx.Data)"
    Write-Host "test state folder:  $($ctx.State)"

    try {
        Add-Type -TypeDefinition (Get-Content -LiteralPath (Join-Path $ci 'smoke\win32.cs') -Raw)
        $ctx.ConsoleWatch = $true
    }
    catch {
        $ctx.ConsoleWatchError = "$($_.Exception.Message)"
        Write-Host "the window watcher (test 08) could not be compiled: $($ctx.ConsoleWatchError)"
    }

    Add-Result -Id '00' -Name 'Facts about this machine: ci-logs\probe.txt (written by probe.ps1 before the build)' -Status $(if (Test-Path -LiteralPath (Join-Path $ctx.Logs 'probe.txt')) { 'PASS' } else { 'SKIP' }) -Seconds 0 -Problems @() -Notes @()

    # ---- the tests ----------------------------------------------------------------------------------------------
    Invoke-Part -File '10-runtime.ps1'
    Invoke-Part -File '20-install.ps1'

    if ($ctx.ConsoleWatch) { [GridSimCI.ConsoleWatch]::Start() }
    Invoke-Part -File '30-window.ps1'
    Invoke-Part -File '40-process.ps1'
    Invoke-Test -Id '08' -Name 'No console window shows up while GridSim runs (watched during tests 06, 07, 09, 10 and 11)' -NeedsInstall -Body {
        if (-not $ctx.ConsoleWatch) {
            Stop-Test -Expected 'the window watcher (packaging\ci\smoke\win32.cs) to compile' -Found $ctx.ConsoleWatchError
        }
        $hits = @([GridSimCI.ConsoleWatch]::Stop())
        $ownConsole = [GridSimCI.ConsoleWatch]::CallerHasConsole()
        Set-Content -LiteralPath (Join-Path $ctx.Logs 'console-windows.txt') -Value (@("test script has a console of its own: $ownConsole") + $hits) -Encoding utf8
        if ($hits.Count -eq 0) {
            Note 'no window of class ConsoleWindowClass or CASCADIA_HOSTING_WINDOW_CLASS appeared (looked every 25 ms)'
        }
        $appPrefix = $ctx.App.TrimEnd('\') + '\'
        foreach ($hit in $hits) {
            $fromApp = $hit.IndexOf("owner=$appPrefix", [System.StringComparison]::OrdinalIgnoreCase) -ge 0
            if ($fromApp -or $ownConsole) {
                Expect $false 'no visible console window' "one appeared: $hit"
            }
            else {
                # the test script itself has no console here, so console tools IT runs may open windows too
                Note "a console window appeared that cannot be pinned on GridSim: $hit"
            }
        }
    }

    Invoke-Part -File '50-schedule.ps1'
    Invoke-Part -File '60-update.ps1'
    Invoke-Part -File '65-autopilot.ps1'
    Invoke-Part -File '70-uninstall.ps1'
}
catch {
    $fatal = "$($_.Exception.Message)"
    if ($_.InvocationInfo -and $_.InvocationInfo.ScriptLineNumber) {
        $fatal = $fatal + ' [smoke.ps1 or a file it runs, line ' + $_.InvocationInfo.ScriptLineNumber + ']'
    }
    Write-Host "::error::the smoke test stopped early: $fatal"
}
finally {
    Write-Host ''
    Write-Host '::group::Clean-up and logs'
    if (Get-Command -Name Invoke-Quietly -ErrorAction SilentlyContinue) {
        Invoke-Quietly -What 'window watcher' -Do { if ($ctx.ConsoleWatch) { $null = [GridSimCI.ConsoleWatch]::Stop() } }
        Invoke-Quietly -What 'process list' -Do { Save-ProcessList -Label 'end' }
        Invoke-Quietly -What 'scheduled jobs' -Do {
            foreach ($name in ($ctx.TaskNames + @('GridSim CI NoUserId', 'GridSim CI Utf8'))) { Remove-Task -TaskName $name }
        }
        Invoke-Quietly -What 'GridSim processes' -Do { Stop-Launchers }
        Invoke-Quietly -What 'installer processes' -Do {
            foreach ($row in @(Get-CimInstance -ClassName Win32_Process | Where-Object { $_.Name -like 'GridSim-Setup*' -or $_.Name -like 'unins0*' })) {
                Stop-Process -Id $row.ProcessId -Force -ErrorAction SilentlyContinue
            }
        }
        Invoke-Quietly -What 'test copy processes' -Do {
            if ($ctx.Copy) {
                foreach ($row in @(Get-ProcsUnder -Dir $ctx.Copy)) { Stop-Process -Id $row.ProcessId -Force -ErrorAction SilentlyContinue }
            }
        }
        Invoke-Quietly -What 'placeholder' -Do {
            if ($ctx.Placeholder -and -not $ctx.Placeholder.HasExited) { Stop-Tree -ProcessId $ctx.Placeholder.Id }
            foreach ($row in @(Get-ProcsUnder -Dir $ctx.Stage)) { Stop-Process -Id $row.ProcessId -Force -ErrorAction SilentlyContinue }
        }
        Invoke-Quietly -What 'browsers' -Do {
            if ($ctx.BrowsersTouched) {
                foreach ($image in @('msedge.exe', 'chrome.exe')) { & taskkill.exe /F /T /IM $image 2>&1 | Out-Null }
            }
        }
        Invoke-Quietly -What 'shortcuts' -Do {
            foreach ($link in @($ctx.DesktopLink, $ctx.StartLink)) {
                if (Test-Path -LiteralPath $link) { Remove-Item -LiteralPath $link -Force }
            }
        }
        Invoke-Quietly -What 'drive letter' -Do {
            if ($ctx.SubstDrive) { & subst.exe $ctx.SubstDrive /D 2>&1 | Out-Null }
        }
        Invoke-Quietly -What 'build folder name' -Do {
            $renamed = Join-Path (Split-Path -Parent $ctx.Stage) 'GridSim.build'
            if ((Test-Path -LiteralPath $renamed) -and -not (Test-Path -LiteralPath $ctx.Stage)) { Rename-Item -LiteralPath $renamed -NewName 'GridSim' }
        }
        Invoke-Quietly -What 'per-user site-packages probe' -Do {
            if ($ctx.UserSiteProbe) {
                $probe = Join-Path $ctx.UserSiteProbe 'gridsim_ci_usersite_probe.py'
                if (Test-Path -LiteralPath $probe) { Remove-Item -LiteralPath $probe -Force }
            }
        }
        Invoke-Quietly -What 'user environment' -Do {
            if ($ctx.UserEnvSet) {
                [Environment]::SetEnvironmentVariable('GRIDSIM_DATA', [NullString]::Value, 'User')
                [Environment]::SetEnvironmentVariable('GRIDSIM_STATE', [NullString]::Value, 'User')
            }
        }
        Invoke-Quietly -What 'time zone' -Do {
            if ($ctx.OldTimeZone) { & tzutil.exe /s $ctx.OldTimeZone 2>&1 | Out-Null }
        }
        Invoke-Quietly -What 'logs' -Do {
            $stateLogs = Join-Path $ctx.Logs 'state'
            New-Item -ItemType Directory -Force -Path $stateLogs | Out-Null
            # schedule.log: every schtasks call of the program and what Windows answered; program.log: what
            # "gridsim schedule ..." printed when the installer and the uninstaller ran it without a console
            foreach ($name in @('desktop.log', 'server.log', 'desktop.json', 'old_install.txt', 'schedule.log', 'program.log')) {
                $source = Join-Path $ctx.State $name
                if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination $stateLogs -Force }
            }
            $taskFiles = Join-Path $ctx.State 'tasks'
            if (Test-Path -LiteralPath $taskFiles) {
                foreach ($file in @(Get-ChildItem -LiteralPath $taskFiles -File -Filter '*.xml')) {
                    Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $stateLogs ('registered-' + ($file.Name -replace ' ', '-'))) -Force
                }
            }
            $updateDir = Join-Path $ctx.State 'update'
            if (Test-Path -LiteralPath $updateDir) {
                foreach ($file in @(Get-ChildItem -LiteralPath $updateDir -File | Where-Object { $_.Extension -in @('.log', '.json', '.txt') })) {
                    Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $stateLogs "update-$($file.Name)") -Force
                }
            }
            $listing = @(Get-ChildItem -LiteralPath $ctx.State -Recurse -Depth 2 -ErrorAction SilentlyContinue | ForEach-Object { '{0,12}  {1}' -f $(if ($_.PSIsContainer) { '<folder>' } else { $_.Length }), $_.FullName })
            Set-Content -LiteralPath (Join-Path $ctx.Logs 'state-folder.txt') -Value $listing -Encoding utf8
            $dataLogs = Join-Path $ctx.Data 'logs'
            if (Test-Path -LiteralPath $dataLogs) { Copy-Item -LiteralPath $dataLogs -Destination (Join-Path $ctx.Logs 'data-folder-logs') -Recurse -Force }
            $profileLog = Join-Path $env:USERPROFILE 'GridSim Data\logs\scheduled.log'
            if (Test-Path -LiteralPath $profileLog) { Copy-Item -LiteralPath $profileLog -Destination (Join-Path $ctx.Logs 'profile-scheduled.log') -Force }
        }
        Invoke-Quietly -What 'summary' -Do { Write-SummaryFile }
    }
    Write-Host '::endgroup::'
}

# ---- the verdict ----------------------------------------------------------------------------------------------------
$summary = Join-Path $ctx.Logs 'summary.txt'
Write-Host ''
Write-Host '==================== summary (ci-logs\summary.txt) ===================='
if (Test-Path -LiteralPath $summary) {
    Get-Content -LiteralPath $summary | ForEach-Object { Write-Host $_ }
    if ($env:GITHUB_STEP_SUMMARY) {
        try {
            Add-Content -LiteralPath $env:GITHUB_STEP_SUMMARY -Value (@('### GridSim smoke test', '', '```') + @(Get-Content -LiteralPath $summary) + @('```')) -Encoding utf8
        }
        catch {
            Write-Host "(the job summary could not be written: $($_.Exception.Message))"
        }
    }
}
$bad = @($script:Results | Where-Object { $_.Status -eq 'FAIL' -or $_.Status -eq 'BLOCKED' })
if ($fatal) { throw "The smoke test stopped early: $fatal" }
if ($script:Results.Count -lt 5) { throw 'The smoke test recorded almost no results; see the log above.' }
if ($bad.Count -gt 0) {
    throw "The smoke test found problems in: $(@($bad | ForEach-Object { $_.Id }) -join ', '). See ci-logs\summary.txt (also in the ci-logs download of this run)."
}
Write-Host 'All smoke tests passed.'
# GitHub ends the step with the exit code of the last program that ran (a taskkill or schtasks of the clean-up that
# found nothing to do ends with a code other than 0): say plainly that this script succeeded.
$global:LASTEXITCODE = 0
exit 0
