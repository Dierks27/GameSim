# Tests 09, 10 and 11: what happens to the hidden server when the launcher dies, a second start, the fallback windows.

try {
    Start-AutopilotPlaceholder
}
catch {
    Write-Host "the autopilot placeholder could not be started: $($_.Exception.Message)"
}

Invoke-Test -Id '09' -Name 'A killed launcher takes the hidden server with it; a job started to outlive the window survives' -NeedsInstall -Body {
    $launcher = Start-Launcher
    $children = @()
    try {
        $d = Wait-DesktopJson -LauncherPid $launcher.Id -TimeoutSec 180
        if ($null -eq $d) {
            Stop-Test -Expected 'desktop.json from the launcher within 180 s' -Found 'none (see ci-logs\desktop-test09.log)'
        }
        $port = [int]$d.port
        $serverPid = [int]$d.server_pid
        Expect ($null -ne (Wait-Health -Port $port -TimeoutSec 120 -ServerPid $serverPid)) "the server to answer 'ok' on port $port" 'no answer within 120 s'
        Expect (Test-Alive -ProcessId $serverPid) "the server process (desktop.json server_pid $serverPid) to be running" 'no such process'
        # (the kill comes only once the window is up: a launcher ended while its window is still opening leaves
        #  a note behind that makes the next start use the Edge window once)
        if ($null -eq (Wait-GridSimWindow -LauncherPid $launcher.Id -TimeoutSec 60)) { Note 'the GridSim window was not on the screen 60 s after the server was ready; the launcher is killed all the same' }

        # Two sleeping test processes started AS CHILDREN OF THE SERVER (see spawn_under.py):
        #   plain      - like anything the server starts: must die together with the server;
        #   breakaway  - started the way the app starts the overnight tuning run
        #                (CREATE_BREAKAWAY_FROM_JOB | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP): must survive.
        $helper = Join-Path $ctx.Ci 'smoke\spawn_under.py'
        $sleeper = 'import time; time.sleep(900)'
        $plainOut = & $ctx.StagePy $helper $serverPid '0x08000000' $ctx.StagePy -c $sleeper 2>&1
        $plainCode = $LASTEXITCODE
        $breakOut = & $ctx.StagePy $helper $serverPid '0x09000200' $ctx.StagePy -c $sleeper 2>&1
        $breakCode = $LASTEXITCODE
        $plainPid = 0
        $breakPid = 0
        if ($plainCode -eq 0) { $plainPid = [int]("$(@($plainOut) | Select-Object -Last 1)").Trim(); $children += $plainPid }
        if ($breakCode -eq 0) { $breakPid = [int]("$(@($breakOut) | Select-Object -Last 1)").Trim(); $children += $breakPid }
        if ($plainCode -ne 0) { Note "the plain test child could not be started under the server: $(@($plainOut) -join ' ')" }
        if ($breakCode -eq 5) {
            Expect $false 'the server to be allowed to start processes that outlive the window (job limit BREAKAWAY_OK)' "Windows refused: $(@($breakOut) -join ' ')"
        }
        elseif ($breakCode -ne 0) {
            Note "the breakaway test child could not be started under the server: $(@($breakOut) -join ' ')"
        }
        Start-Sleep -Seconds 1

        & taskkill.exe /F /PID $launcher.Id 2>&1 | Out-Null
        $gone = Wait-Gone -ProcessId $serverPid -TimeoutSec 20
        if ($null -eq $gone) {
            Expect $false 'the server to end within 5 s after the launcher was killed (kill-on-close job)' 'it was still running after 20 s'
        }
        else {
            Expect ($gone -le 5) 'the server to end within 5 s after the launcher was killed (kill-on-close job)' "it took $gone s"
            Note "the server ended $gone s after the launcher was killed"
        }
        Expect ($null -ne (Wait-PortClosed -Port $port -TimeoutSec 15)) "port $port to stop listening" 'it still answers'
        Start-Sleep -Seconds 2
        if ($plainPid -gt 0) {
            Expect (-not (Test-Alive -ProcessId $plainPid)) 'a process the server started in the normal way to end with the server' "the plain test child (pid $plainPid) is still running, so the server was not in a kill-on-close job"
        }
        if ($breakPid -gt 0) {
            Expect (Test-Alive -ProcessId $breakPid) 'a process started with CREATE_BREAKAWAY_FROM_JOB to survive (the tuning run must outlive the window)' "the breakaway test child (pid $breakPid) was killed with the server"
        }
        if ($plainPid -eq 0 -or $breakPid -eq 0) {
            Note 'the survive / do-not-survive check of processes started by the server is NOT proven in this run (a test child could not be started)'
        }
        $left = Get-Leftovers -TimeoutSec 10
        foreach ($line in $left.Bad) { Expect $false 'no process of the installed program left after the launcher was killed' $line }
        foreach ($line in $left.Background) { Note "still running on purpose (background job): $line" }
    }
    finally {
        foreach ($child in $children) { Stop-Tree -ProcessId $child }
        if (-not $launcher.HasExited) { Stop-Tree -ProcessId $launcher.Id }
        Stop-Launchers
        Save-LogSlice -Id '09'
    }
}

Invoke-Test -Id '10' -Name 'Only one GridSim at a time: a second start hands over to the first and exits' -NeedsInstall -Body {
    $jsonPath = Join-Path $ctx.State 'desktop.json'
    if (Test-Path -LiteralPath $jsonPath) { Note 'a desktop.json left by the launcher that test 09 killed was still there at the start (a real crash leaves the same)' }
    $first = Start-Launcher
    Start-Sleep -Seconds 1
    $second = Start-Launcher
    $third = $null
    try {
        $d = Wait-DesktopJson -TimeoutSec 180 -LauncherPid 0
        if ($null -eq $d) {
            Stop-Test -Expected 'desktop.json from one of the two launchers within 180 s' -Found 'none (see ci-logs\desktop-test10.log)'
        }
        $port = [int]$d.port
        $ownerPid = [int]$d.pid
        Expect (($ownerPid -eq $first.Id) -or ($ownerPid -eq $second.Id)) 'desktop.json to name one of the two launchers just started' "pid $ownerPid (started: $($first.Id) and $($second.Id))"
        Expect ($null -ne (Wait-Health -Port $port -TimeoutSec 120 -ServerPid ([int]$d.server_pid))) "the server to answer 'ok' on port $port" 'no answer within 120 s'

        $other = $second
        $owner = $first
        if ($ownerPid -eq $second.Id) { $other = $first; $owner = $second }
        if ($other.WaitForExit(120000)) {
            $other.WaitForExit()
            Expect ($other.ExitCode -eq 0) 'the launcher that came second to exit with code 0' "exit code $($other.ExitCode)"
        }
        else {
            Expect $false 'the launcher that came second to exit by itself within 120 s' 'it is still running'
        }
        Expect (-not $owner.HasExited) 'the first GridSim to keep running' 'it ended'
        $servers = @(Get-ProcsUnder -Dir $ctx.App | Where-Object { "$($_.CommandLine)" -match 'streamlit' })
        Expect ($servers.Count -eq 1) 'exactly one server process' "$($servers.Count): $(($servers | ForEach-Object { Format-Proc -Row $_ }) -join ' | ')"

        # the everyday case: GridSim is open, the user double-clicks the icon again
        $third = Start-Launcher
        if ($third.WaitForExit(60000)) {
            $third.WaitForExit()
            Expect ($third.ExitCode -eq 0) 'a start while GridSim is already open to exit with code 0' "exit code $($third.ExitCode)"
        }
        else {
            Expect $false 'a start while GridSim is already open to exit within 60 s' 'it is still running'
        }
        $servers = @(Get-ProcsUnder -Dir $ctx.App | Where-Object { "$($_.CommandLine)" -match 'streamlit' })
        Expect ($servers.Count -eq 1) 'still exactly one server process after the third start' "$($servers.Count)"
        Expect (-not $owner.HasExited) 'the first GridSim to keep running after the third start' 'it ended'
        $again = Read-JsonFile -Path $jsonPath
        Expect ($again -and ([int]$again.pid -eq $ownerPid) -and ([int]$again.port -eq $port)) 'desktop.json to still describe the first GridSim' "pid $($again.pid), port $($again.port)"

        $window = Wait-GridSimWindow -LauncherPid $owner.Id -TimeoutSec 60
        if ($window) {
            $null = $window.CloseMainWindow()
            if (-not $owner.WaitForExit(30000)) { Note 'the first GridSim did not exit within 30 s after its window was closed (ended by the test)' }
        }
        else {
            Note 'no GridSim window was found to close (the launchers were ended by the test)'
        }
    }
    finally {
        foreach ($proc in @($first, $second, $third)) {
            if ($proc -and -not $proc.HasExited) { Stop-Tree -ProcessId $proc.Id }
        }
        Stop-Launchers
        Save-LogSlice -Id '10'
    }
}

Invoke-Test -Id '11' -Name 'The fallback windows: Edge app window (GRIDSIM_WINDOW=edge), then the default browser (GRIDSIM_WINDOW=browser)' -NeedsInstall -Body {
    $ctx.BrowsersTouched = $true
    $launcher = Start-Launcher -Window 'edge'
    try {
        $d = Wait-DesktopJson -LauncherPid $launcher.Id -TimeoutSec 180
        if ($null -eq $d) {
            $state = 'it is still running'
            if ($launcher.HasExited) { $state = "it ended with exit code $($launcher.ExitCode)" }
            Expect $false 'desktop.json from the launcher started with GRIDSIM_WINDOW=edge within 180 s' "none; the launcher: $state"
        }
        else {
            $port = [int]$d.port
            Expect ("$($d.window)" -like '*edge*') "desktop.json window = 'edge'" "'$($d.window)'"
            Expect ($null -ne (Wait-Health -Port $port -TimeoutSec 120 -ServerPid ([int]$d.server_pid))) "the server to answer 'ok' on port $port" 'no answer within 120 s'
            $lock = $null
            $clock = [System.Diagnostics.Stopwatch]::StartNew()
            while ($null -eq $lock -and $clock.Elapsed.TotalSeconds -lt 120 -and -not $launcher.HasExited) {
                # (the WebView2 window keeps a lockfile of its own under ...\webview\EBWebView; that one does not count)
                $lock = @(Get-ChildItem -LiteralPath $ctx.State -Recurse -Depth 2 -Filter 'lockfile' -File -Force -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notlike '*webview*' }) | Select-Object -First 1
                if ($null -eq $lock) { Start-Sleep -Milliseconds 500 }
            }
            if ($lock) {
                Note "the browser's own profile is in use: $($lock.FullName)"
            }
            else {
                Expect $false "a file named 'lockfile' in the browser profile under the state folder (it shows the app window is open)" 'none within 120 s'
            }
            Start-Sleep -Seconds 5
            $browsers = @(Get-CimInstance -ClassName Win32_Process | Where-Object { $_.Name -in @('msedge.exe', 'chrome.exe') })
            $direct = @($browsers | Where-Object { $_.ParentProcessId -eq $launcher.Id })
            $directText = @($direct | ForEach-Object { $_.Name + ' pid ' + $_.ProcessId }) -join ', '
            if (-not $directText) { $directText = 'none (the browser handed the window over to another of its processes)' }
            Note "$($browsers.Count) browser processes; started directly by the launcher and still alive: $directText"
            $null = Save-Text -Name 'windows-test11.txt' -Lines (Get-WindowList)
            Expect (-not $launcher.HasExited) 'the launcher to keep running while the Edge app window is open' 'it ended (its exit code is in ci-logs\desktop-test11-edge.log)'

            # a normal close request to every browser window (no /F), as a user closing the window would do
            foreach ($image in @('msedge.exe', 'chrome.exe')) { & taskkill.exe /IM $image 2>&1 | Out-Null }
            if (-not $launcher.WaitForExit(45000)) {
                $window = Find-GridSimWindow
                if ($window) { $null = $window.CloseMainWindow() }
                if ($launcher.WaitForExit(30000)) {
                    Note 'the launcher exited only after a second close request to the window'
                }
                else {
                    Expect $false 'the launcher to exit within 75 s after the Edge app window was closed' 'it is still running'
                }
            }
            if ($launcher.HasExited) {
                $launcher.WaitForExit()
                Expect ($launcher.ExitCode -eq 0) 'launcher exit code 0 after the Edge app window was closed' "exit code $($launcher.ExitCode)"
                Expect ($null -ne (Wait-PortClosed -Port $port -TimeoutSec 20)) "port $port to stop listening after the Edge window was closed" 'it still answers'
            }
        }
    }
    finally {
        if (-not $launcher.HasExited) { Stop-Tree -ProcessId $launcher.Id }
        Stop-Launchers
        foreach ($image in @('msedge.exe', 'chrome.exe')) { & taskkill.exe /F /T /IM $image 2>&1 | Out-Null }
        Save-LogSlice -Id '11-edge'
    }

    # last resort: the default browser. With GRIDSIM_SMOKE=1 there is no "click OK to stop" box; the launcher
    # must get to that point and end by itself.
    $launcher = Start-Launcher -Smoke -Window 'browser'
    try {
        if ($launcher.WaitForExit(240000)) {
            $launcher.WaitForExit()
            Note "GRIDSIM_WINDOW=browser with GRIDSIM_SMOKE=1: the launcher ended by itself with exit code $($launcher.ExitCode)"
            # 0 = the server came up and handed out the page (no browser is opened in this mode); 1 = no server
            Expect ($launcher.ExitCode -eq 0) 'launcher exit code 0 from the last-resort run (the page was handed out)' "exit code $($launcher.ExitCode); see ci-logs\desktop-test11-browser.log"
            $left = Get-Leftovers -TimeoutSec 15
            foreach ($line in $left.Bad) { Expect $false 'no process of the installed program left after the last-resort run' $line }
        }
        else {
            Expect $false 'the launcher (GRIDSIM_WINDOW=browser, GRIDSIM_SMOKE=1) to end by itself within 240 s' 'it is still running (waiting for a click on a message box?)'
        }
    }
    finally {
        if (-not $launcher.HasExited) { Stop-Tree -ProcessId $launcher.Id }
        Stop-Launchers
        foreach ($image in @('msedge.exe', 'chrome.exe')) { & taskkill.exe /F /T /IM $image 2>&1 | Out-Null }
        Save-LogSlice -Id '11-browser'
    }
}
