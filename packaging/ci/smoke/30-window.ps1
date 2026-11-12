# Tests 06 and 07: the GridSim window. Both start GridSim the way its shortcut does (pythonw -m gridsim.desktop).

try {
    Start-AutopilotPlaceholder
}
catch {
    Write-Host "the autopilot placeholder could not be started: $($_.Exception.Message)"
}

Invoke-Test -Id '06' -Name 'The GridSim window (pywebview on WebView2) shows the app: launcher self-check with GRIDSIM_SMOKE=1' -NeedsInstall -Body {
    $webview = Get-WebView2Version
    if (-not $webview) {
        Note 'the WebView2 runtime was not on this machine; installing it with "choco install webview2-runtime" (Windows 11 has it built in)'
        $choco = Invoke-Tool -File 'choco.exe' -Arguments 'install webview2-runtime -y --no-progress' -TimeoutSec 900
        $null = Save-Text -Name 'choco-webview2.txt' -Lines $choco.Lines
        Note "choco install webview2-runtime: exit code $($choco.ExitCode) (output in ci-logs\choco-webview2.txt)"
        $webview = Get-WebView2Version
    }
    Expect ([bool]$webview) 'the WebView2 runtime on this machine (registry value pv)' 'none, also after choco install webview2-runtime'
    if ($webview) { Note "WebView2 runtime $webview" }

    $jsonPath = Join-Path $ctx.State 'desktop.json'
    $startedUtc = [DateTime]::UtcNow
    $seen = $null
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    $launcher = Start-Launcher -Smoke
    try {
        while (-not $launcher.HasExited -and $clock.Elapsed.TotalSeconds -lt 240) {
            $d = Read-JsonFile -Path $jsonPath
            if ($d -and $d.port) {
                $fresh = ([int]$d.pid -eq $launcher.Id) -or ((Get-Item -LiteralPath $jsonPath -ErrorAction SilentlyContinue).LastWriteTimeUtc -ge $startedUtc)
                if ($fresh) { $seen = $d }
            }
            Start-Sleep -Milliseconds 300
        }
        $took = [int]$clock.Elapsed.TotalSeconds
        if (-not $launcher.HasExited) {
            Expect $false 'the launcher to check itself and close within 240 s (GRIDSIM_SMOKE=1)' 'it was still running and was ended by the test; see ci-logs\desktop-test06.log'
        }
        else {
            $launcher.WaitForExit()
            Expect ($launcher.ExitCode -eq 0) 'launcher exit code 0, which means the page was drawn in the WebView2 window' "exit code $($launcher.ExitCode) after $took s; see ci-logs\desktop-test06.log"
            Note "the launcher started, showed the app and closed in $took s"
        }
        if ($seen) {
            Note "desktop.json while it ran: window '$($seen.window)', host $($seen.host), port $($seen.port), launcher pid $($seen.pid), server pid $($seen.server_pid)"
            Expect ("$($seen.window)" -like '*webview*') "the window to be the pywebview one (desktop.json window = 'webview')" "'$($seen.window)'"
            Expect ("$($seen.host)" -eq '127.0.0.1') 'the server to listen on 127.0.0.1 only (phone access is off by default)' "host '$($seen.host)'"
            Expect ([int]$seen.port -ne 8501) 'a free port chosen by Windows, not the old fixed 8501' "port $($seen.port)"
            foreach ($field in @('pid', 'server_pid', 'port', 'host', 'window', 'started_utc')) {
                Expect ($null -ne $seen.PSObject.Properties[$field]) "the field '$field' in desktop.json" 'it is missing'
            }
            if ($launcher.HasExited) {
                Expect ($null -ne (Wait-PortClosed -Port ([int]$seen.port) -TimeoutSec 15)) "nothing listening on port $($seen.port) after the launcher closed" 'the port still answers'
                Expect (-not (Test-Alive -ProcessId ([int]$seen.server_pid))) 'the server process to be gone after the launcher closed' "pid $($seen.server_pid) is still running"
            }
        }
        else {
            Expect $false 'desktop.json in the state folder while the launcher runs' "never seen in $($ctx.State)"
        }
        if ($launcher.HasExited) {
            Expect (-not (Test-Path -LiteralPath $jsonPath)) 'desktop.json to be removed when the launcher exits' 'it is still there'
            $left = Get-Leftovers -TimeoutSec 20
            foreach ($line in $left.Bad) { Expect $false 'no process of the installed program left after the window closed' $line }
            foreach ($line in $left.Background) { Note "still running on purpose (background job): $line" }
        }
        foreach ($name in @('desktop.log', 'server.log')) {
            Expect (Test-Path -LiteralPath (Join-Path $ctx.State $name)) "$name in the state folder" 'no such file'
        }
    }
    finally {
        if (-not $launcher.HasExited) { Stop-Tree -ProcessId $launcher.Id }
        Stop-Launchers
        Save-LogSlice -Id '06'
    }
}

Invoke-Test -Id '07' -Name 'Closing the window the way a user does stops everything' -NeedsInstall -Body {
    $jsonPath = Join-Path $ctx.State 'desktop.json'
    $launcher = Start-Launcher
    try {
        $d = Wait-DesktopJson -LauncherPid $launcher.Id -TimeoutSec 180
        if ($null -eq $d) {
            $state = 'it is still running'
            if ($launcher.HasExited) { $state = "it ended with exit code $($launcher.ExitCode)" }
            Stop-Test -Expected 'desktop.json from the launcher within 180 s' -Found "none; the launcher: $state"
        }
        $port = [int]$d.port
        $serverPid = [int]$d.server_pid
        $health = Wait-Health -Port $port -TimeoutSec 120 -ServerPid $serverPid
        Expect ($null -ne $health) "the server to answer 'ok' on port $port" 'no answer within 120 s'
        Expect ("$($d.window)" -like '*webview*') "the normal window to be the pywebview one (desktop.json window = 'webview')" "'$($d.window)'"

        $owner = $null
        $clock = [System.Diagnostics.Stopwatch]::StartNew()
        while ($null -eq $owner -and $clock.Elapsed.TotalSeconds -lt 120 -and -not $launcher.HasExited) {
            $owner = Find-GridSimWindow -PreferPid $launcher.Id
            if ($null -eq $owner) { Start-Sleep -Milliseconds 500 }
        }
        $windows = @(Get-WindowList)
        $null = Save-Text -Name 'windows-test07.txt' -Lines $windows
        if ($null -eq $owner) {
            Stop-Test -Expected 'a visible window with "GridSim" in its title within 120 s' -Found "none; windows on the desktop: $($windows -join ' | ')"
        }
        Note "window '$($owner.MainWindowTitle)' owned by $($owner.ProcessName) (pid $($owner.Id)); the launcher is pid $($launcher.Id)"
        Expect ($owner.Id -eq $launcher.Id) 'the window to belong to the launcher itself (pywebview), so the taskbar shows GridSim' "it belongs to $($owner.ProcessName)"
        Start-Sleep -Seconds 3

        $asked = $owner.CloseMainWindow()
        Expect $asked 'the window to accept a normal close request' 'CloseMainWindow() returned false'
        $closed = $launcher.WaitForExit(30000)
        if ($closed) {
            $launcher.WaitForExit()
            Expect ($launcher.ExitCode -eq 0) 'launcher exit code 0 after a normal close' "exit code $($launcher.ExitCode)"
        }
        else {
            Expect $false 'the launcher to exit within 30 s after its window was closed' 'it is still running'
        }
        $portTime = Wait-PortClosed -Port $port -TimeoutSec 30
        Expect ($null -ne $portTime) "port $port to stop listening within 30 s after the window was closed" 'it still answers'
        Expect ($null -ne (Wait-Gone -ProcessId $serverPid -TimeoutSec 15)) 'the server process to end' "pid $serverPid is still running"
        if ($closed) {
            Expect (-not (Test-Path -LiteralPath $jsonPath)) 'desktop.json to be removed when the launcher exits' 'it is still there'
            $left = Get-Leftovers -TimeoutSec 20
            foreach ($line in $left.Bad) { Expect $false 'no process of the installed program left after the window was closed' $line }
            foreach ($line in $left.Background) { Note "still running on purpose (background job): $line" }
        }
    }
    finally {
        if (-not $launcher.HasExited) { Stop-Tree -ProcessId $launcher.Id }
        Stop-Launchers
        Save-LogSlice -Id '07'
    }
}
