# Tests 14 and 15: updating an installed, running GridSim, and the update download.

try {
    Start-AutopilotPlaceholder
}
catch {
    Write-Host "the autopilot placeholder could not be started: $($_.Exception.Message)"
}

function Get-InstallerProcs {
    # Running installers: GridSim-Setup.exe and the copy of itself that Inno Setup runs from the temp folder.
    return @(Get-CimInstance -ClassName Win32_Process | Where-Object { $_.Name -like 'GridSim-Setup*' })
}

function Test-JobsOn {
    # After an update every one of the four jobs must exist, be switched on and start the private pythonw.
    foreach ($name in $ctx.TaskNames) {
        $task = Get-TaskFacts -TaskName $name
        if ($null -eq $task) {
            Expect $false "the job '$name' to exist after the update (it existed before)" 'no such job'
        }
        else {
            Expect $task.Enabled "the job '$name' to be switched on again after the update" 'it is disabled'
            Expect ($task.Command -ieq $ctx.AppPyw) "the job '$name' to start $($ctx.AppPyw)" "'$($task.Command)'"
        }
    }
}

Invoke-Test -Id '14a' -Name 'Update over a running GridSim (installer started by hand with /SILENT /NOCANCEL /NORESTART /RELAUNCH=1)' -NeedsInstall -Body {
    $staleLib = Join-Path $ctx.App 'python\Lib\site-packages\gridsim_ci_stale.py'
    $staleCode = Join-Path $ctx.Program 'gridsim\gridsim_ci_stale_module.py'
    $cacheDir = Join-Path $ctx.Program 'data\cache'
    $cacheFile = Join-Path $cacheDir 'gridsim_ci_cache_marker.txt'
    Set-Content -LiteralPath $staleLib -Value '# a file of an older version' -Encoding ascii
    Set-Content -LiteralPath $staleCode -Value '# a module of an older version' -Encoding ascii
    New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null
    Set-Content -LiteralPath $cacheFile -Value 'downloaded data must survive an update' -Encoding ascii
    foreach ($name in $ctx.TaskNames) {
        if (-not (Test-TaskExists -TaskName $name)) { Note "the job '$name' did not exist before the update (test 12 should have created it)" }
    }

    $old = Start-Launcher
    $sleeper = Start-Proc -File $ctx.AppPyw -Arguments '-c "import time; time.sleep(900)"' -WorkDir $ctx.Program
    $newPid = 0
    try {
        $d = Wait-DesktopJson -LauncherPid $old.Id -TimeoutSec 180
        if ($null -eq $d) {
            Note 'the launcher did not come up before the update (no desktop.json); the update is tested all the same'
        }
        else {
            $null = Wait-Health -Port ([int]$d.port) -TimeoutSec 120 -ServerPid ([int]$d.server_pid)
            # (a GridSim that is ended while its window is still opening uses the Edge window at its next start)
            if ($null -eq (Wait-GridSimWindow -LauncherPid $old.Id -TimeoutSec 60)) { Note 'the GridSim window was not on the screen 60 s after the server was ready; the update is started all the same' }
        }
        Expect (-not $sleeper.HasExited) 'a second process of the installed Python (standing in for a scheduled job) to be running before the update' 'it ended'

        $run = Invoke-Setup -Arguments '/SILENT /NOCANCEL /NORESTART /RELAUNCH=1' -LogName 'inno-update-by-hand.log'
        Expect ($run.ExitCode -eq 0) 'installer exit code 0' "exit code $($run.ExitCode) (see ci-logs\inno-update-by-hand.log)"
        Note "the update took $($run.Seconds) s"

        Expect ($old.HasExited) 'the GridSim that was running to be closed by the installer' "the launcher (pid $($old.Id)) is still running"
        Expect ($sleeper.HasExited) 'the other running process of the installed Python to be ended by the installer' "pid $($sleeper.Id) is still running"
        if ($d) { Expect (-not (Test-Alive -ProcessId ([int]$d.server_pid))) 'the old server process to be gone' "pid $($d.server_pid) is still running" }
        Expect (-not (Test-Path -LiteralPath $staleLib)) 'a left-over file in python\Lib\site-packages to be gone after the update' 'it is still there'
        Expect (-not (Test-Path -LiteralPath $staleCode)) 'a left-over module in program\gridsim to be gone after the update' 'it is still there'
        Expect (Test-Path -LiteralPath $cacheFile) 'the downloaded-data folder program\data\cache to survive the update' 'the marker file in it is gone'
        Expect ((Test-Path -LiteralPath $ctx.Marker) -and ((Get-Content -LiteralPath $ctx.Marker -Raw).Trim() -eq $ctx.MarkerText)) 'the marker file in the data folder untouched by the update' 'it is gone or changed'
        Expect (Test-Path -LiteralPath $ctx.AppPyw) 'the private Python to be in place after the update' 'pythonw.exe is missing'
        Test-JobsOn

        $fresh = Wait-DesktopJson -TimeoutSec 240 -NotPids @($old.Id)
        if ($null -eq $fresh) {
            Expect $false 'GridSim to be started again by the installer (/RELAUNCH=1) within 240 s' 'no new desktop.json'
        }
        else {
            $newPid = [int]$fresh.pid
            $health = Wait-Health -Port ([int]$fresh.port) -TimeoutSec 180 -ServerPid ([int]$fresh.server_pid)
            Expect ($null -ne $health) "the restarted GridSim (pid $newPid) to answer 'ok' on port $($fresh.port)" 'no answer within 180 s'
            $ctx.RelaunchedPid = $newPid
        }
    }
    finally {
        if (-not $sleeper.HasExited) { Stop-Tree -ProcessId $sleeper.Id }
        if (-not $old.HasExited) { Stop-Tree -ProcessId $old.Id }
        Save-LogSlice -Id '14a'
    }
}

Invoke-Test -Id '14b' -Name 'Update started by the app itself (gridsim.update.request_install; the launcher hands over to the installer)' -NeedsInstall -Body {
    $jsonPath = Join-Path $ctx.State 'desktop.json'
    $launcherPid = 0
    $d = Wait-DesktopJson -TimeoutSec 5
    if ($d) {
        Note "using the GridSim that the first update started again (pid $($d.pid))"
    }
    else {
        $started = Start-Launcher
        $d = Wait-DesktopJson -LauncherPid $started.Id -TimeoutSec 180
        if ($null -eq $d) {
            Stop-Test -Expected 'a running GridSim to hand the update to' -Found 'the launcher did not come up (no desktop.json within 180 s)'
        }
    }
    $launcherPid = [int]$d.pid
    try {
        Expect ($null -ne (Wait-Health -Port ([int]$d.port) -TimeoutSec 180 -ServerPid ([int]$d.server_pid))) "the running GridSim to answer 'ok' before the update" 'no answer within 180 s'
        if ($null -eq (Wait-GridSimWindow -LauncherPid $launcherPid -TimeoutSec 60)) { Note 'the GridSim window was not on the screen 60 s after the server was ready; the update is asked for all the same' }
        $updateDir = Join-Path $ctx.State 'update'
        New-Item -ItemType Directory -Force -Path $updateDir | Out-Null
        $copy = Join-Path $updateDir 'GridSim-Setup.exe'
        Copy-Item -LiteralPath $ctx.Setup -Destination $copy -Force
        $handLog = Join-Path $updateDir 'install.log'
        if (Test-Path -LiteralPath $handLog) { Remove-Item -LiteralPath $handLog -Force }

        # Exactly what the app does after "Update now" has downloaded the installer. The version must be NEWER than
        # the installed one: gridsim.update drops a request for a version that is not (that update is done), so
        # the installer built here (the same version as the installed program) is offered under a made-up later one.
        $newer = '9999.1.1'
        $run = Invoke-AppCode -Code "from gridsim import update; update.request_install(r'$copy', '$newer'); print(update.pending())" -TimeoutSec 120
        Expect ($run.ExitCode -eq 0) "gridsim.update.request_install(path, '$newer') to work" "exit code $($run.ExitCode): $($run.Text)"
        # (None here is not a failure by itself: the running launcher may have taken the request in the same instant)
        Note "update.pending() right after request_install: $(@($run.Lines) | Select-Object -Last 1)"

        $gone = Wait-Gone -ProcessId $launcherPid -TimeoutSec 90
        if ($null -eq $gone) {
            Stop-Test -Expected 'the launcher to notice the pending update, start the installer and exit within 90 s' -Found "the launcher (pid $launcherPid) is still running"
        }
        Note "the launcher exited $gone s after the update was requested"

        $clock = [System.Diagnostics.Stopwatch]::StartNew()
        $sawInstaller = $false
        while ($clock.Elapsed.TotalSeconds -lt 60 -and -not $sawInstaller) {
            if (@(Get-InstallerProcs).Count -gt 0 -or (Test-Path -LiteralPath $handLog)) { $sawInstaller = $true } else { Start-Sleep -Milliseconds 500 }
        }
        Expect $sawInstaller 'the installer to be running after the launcher exited (it must outlive the launcher that started it)' 'no installer process and no install.log within 60 s'
        $clock = [System.Diagnostics.Stopwatch]::StartNew()
        while (@(Get-InstallerProcs).Count -gt 0 -and $clock.Elapsed.TotalMinutes -lt 20) { Start-Sleep -Seconds 2 }
        if (@(Get-InstallerProcs).Count -gt 0) {
            Stop-Test -Expected 'the installer started by the launcher to finish within 20 minutes' -Found 'it is still running'
        }
        Note "the installer started by the launcher ran for about $([int]$clock.Elapsed.TotalSeconds) s"
        if (Test-Path -LiteralPath $handLog) {
            Copy-Item -LiteralPath $handLog -Destination (Join-Path $ctx.Logs 'inno-update-by-app.log') -Force
            $ended = @(Select-String -LiteralPath $handLog -Pattern 'Installation process succeeded' -SimpleMatch).Count -gt 0
            if (-not $ended) { Note "the installer's log (ci-logs\inno-update-by-app.log) does not hold the line 'Installation process succeeded'" }
        }
        else {
            Expect $false "the installer's log where the launcher asks for it: $handLog" 'no such file'
        }
        Test-JobsOn

        $fresh = Wait-DesktopJson -TimeoutSec 240 -NotPids @($launcherPid)
        if ($null -eq $fresh) {
            Stop-Test -Expected 'GridSim to come back by itself after the update within 240 s' -Found 'no new desktop.json'
        }
        $newPid = [int]$fresh.pid
        Expect ($null -ne (Wait-Health -Port ([int]$fresh.port) -TimeoutSec 180 -ServerPid ([int]$fresh.server_pid))) "the GridSim that came back (pid $newPid) to answer 'ok'" 'no answer within 180 s'

        # it must not start the same update again
        Start-Sleep -Seconds 20
        Expect (@(Get-InstallerProcs).Count -eq 0) 'no installer running 20 s after GridSim came back (the update must not start over)' 'an installer is running again'
        Expect (Test-Alive -ProcessId $newPid) 'the GridSim that came back to still be running 20 s later' 'it is gone'
        $run = Invoke-AppCode -Code 'from gridsim import update; import sys; p = update.pending(); print(p); sys.exit(0 if p is None else 3)' -TimeoutSec 120
        Expect ($run.ExitCode -eq 0) 'gridsim.update.pending() to be None once the update is installed' "exit code $($run.ExitCode): $($run.Text)"
    }
    finally {
        Stop-Launchers
        foreach ($row in @(Get-InstallerProcs)) { Stop-Process -Id $row.ProcessId -Force -ErrorAction SilentlyContinue }
        Save-LogSlice -Id '14b'
    }
}

Invoke-Test -Id '15a' -Name 'Update check and download: no "from the internet" mark on a file Python downloads; gridsim update --check works' -NeedsInstall -Body {
    # Windows asks "are you sure?" (SmartScreen) only for files that carry the Zone.Identifier mark a browser
    # adds. A file the program downloads itself must not carry it.
    $target = Join-Path $ctx.Temp 'gridsim-ci-download.bin'
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force }
    $run = Invoke-AppCode -Code "import urllib.request; urllib.request.urlretrieve('https://github.com/', r'$target'); print('downloaded')" -TimeoutSec 180
    if ($run.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $target)) {
        Expect $false 'the private Python to download a file over HTTPS (certificates from the Windows store)' "it failed: $(@($run.Lines) | Select-Object -Last 1)"
    }
    else {
        $mark = Get-Item -LiteralPath $target -Stream 'Zone.Identifier' -ErrorAction SilentlyContinue
        Expect ($null -eq $mark) 'no Zone.Identifier stream on a file downloaded by Python' 'the stream is there'
        Note "downloaded $((Get-Item -LiteralPath $target).Length) bytes over HTTPS with the private Python; no Zone.Identifier stream"
    }

    $tokenEnv = Get-GitHubTokenEnv
    $run = Invoke-App -Arguments '-m gridsim update --check' -TimeoutSec 180 -Environment $tokenEnv
    $null = Save-Text -Name 'update-check.txt' -Lines $run.Lines
    Expect ($run.ExitCode -eq 0) "'python -m gridsim update --check' to end with exit code 0" "exit code $($run.ExitCode); it said: $($run.Text)"
    Note "gridsim update --check said: $($run.Text)"
    # The command ends with 0 also when GitHub could not be asked. What the check found is in update.status():
    # available / newest / not_ready / none_published all mean GitHub answered; unknown means it did not.
    $run = Invoke-AppCode -Code "from gridsim import update; s = update.status(); print(s['state']); print(s['error'])" -TimeoutSec 120
    $state = ("$(@($run.Lines) | Select-Object -First 1)").Trim()
    if ($tokenEnv.Count -gt 0) {
        Expect (@('available', 'newest', 'not_ready', 'none_published') -contains $state) "an answer from GitHub to the update check (update.status() state available, newest, not_ready or none_published)" "state '$state': $($run.Text)"
    }
    else {
        Note "no GH_TOKEN in this step, so the update check asked GitHub without one; update.status() state: '$state'"
    }

    # ---- a real download through gridsim.update.download(): the address checks on the way from github.com to
    #      GitHub's file servers, the size and the checksum. Until GridSim has a release of its own (test 15b), the
    #      file is the Python runtime the build downloaded from a GitHub release; its size and SHA-256 are known.
    $facts = Read-JsonFile -Path (Join-Path $ctx.Logs 'build-facts.json')
    if ($facts -and $facts.pbs_url -and $facts.pbs_bytes -and $facts.pbs_sha256) {
        $info = [ordered]@{
            version = '2099.1.1'
            url     = "$($facts.pbs_url)"
            size    = [long]$facts.pbs_bytes
            digest  = "sha256:$($facts.pbs_sha256)"
            page    = $null
        }
        $infoFile = Join-Path $ctx.Temp 'gridsim-ci-download.json'
        [System.IO.File]::WriteAllText($infoFile, ($info | ConvertTo-Json))
        $run = Invoke-AppCode -Code "import json; from gridsim import update; info = json.load(open(r'$infoFile', encoding='utf-8')); print(update.download(info))" -TimeoutSec 900
        $path = ("$(@($run.Lines) | Where-Object { $_ } | Select-Object -Last 1)").Trim()
        if ($run.ExitCode -ne 0 -or -not $path -or -not (Test-Path -LiteralPath $path)) {
            Expect $false 'gridsim.update.download() to download a file of a GitHub release and return its path' "exit code $($run.ExitCode): $($run.Text)"
        }
        else {
            $file = Get-Item -LiteralPath $path
            Expect ($file.Length -eq [long]$facts.pbs_bytes) "a download of exactly $($facts.pbs_bytes) bytes" "$($file.Length) bytes"
            Expect ($file.FullName.StartsWith($ctx.State, [System.StringComparison]::OrdinalIgnoreCase)) "the download to land in the state folder ($($ctx.State)\update)" $file.FullName
            Expect ($null -eq (Get-Item -LiteralPath $path -Stream 'Zone.Identifier' -ErrorAction SilentlyContinue)) 'no Zone.Identifier stream on a file downloaded by gridsim.update.download()' 'the stream is there'
            Note "gridsim.update.download() fetched $($file.Length) bytes from a GitHub release; size and SHA-256 were checked by the program"
            Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
        }
    }
    else {
        Note 'ci-logs\build-facts.json does not name the downloaded Python runtime, so gridsim.update.download() was not tried here'
    }
}

Invoke-Test -Id '15b' -Name 'Update download from a published release (gridsim.update.download against GitHub)' -NeedsInstall -Body {
    $repoName = "$env:GITHUB_REPOSITORY"
    if (-not $repoName) { $repoName = 'Dierks27/GameSim' }
    if (-not $env:GH_TOKEN) { Skip-Test -Reason 'no GH_TOKEN in this step, so GitHub cannot be asked whether a release exists' }
    $asked = Invoke-Tool -File 'gh.exe' -Arguments "api repos/$repoName/releases/latest" -TimeoutSec 120
    if ($asked.ExitCode -ne 0) {
        Skip-Test -Reason "there is no published release of $repoName yet (or GitHub did not answer): nothing to download. This test runs by itself once the first release exists."
    }
    $release = (@($asked.Lines) -join "`n") | ConvertFrom-Json
    $asset = @($release.assets | Where-Object { $_.name -eq 'GridSim-Setup.exe' -and $_.state -eq 'uploaded' }) | Select-Object -First 1
    if ($null -eq $asset) {
        Stop-Test -Expected "the latest release ($($release.tag_name)) to hold a file named exactly GridSim-Setup.exe" -Found "files: $(@($release.assets | ForEach-Object { $_.name }) -join ', ')"
    }
    Note "latest release: $($release.tag_name), GridSim-Setup.exe $([math]::Round($asset.size / 1MB, 1)) MB, digest '$($asset.digest)'"
    Expect ("$($release.tag_name)" -match '^v\d+(\.\d+){2,3}$') "a release tag of the form v<year>.<month>.<day> (a second release on one day has a fourth number)" "'$($release.tag_name)'"

    $run = Invoke-AppCode -Code 'import json; from gridsim import update; print(json.dumps(update.check(force=True)))' -TimeoutSec 180 -Environment (Get-GitHubTokenEnv)
    Expect ($run.ExitCode -eq 0) 'gridsim.update.check(force=True) to return without an error' "exit code $($run.ExitCode): $($run.Text)"
    Note "update.check(force=True) from the installed $($ctx.Version): $(@($run.Lines) | Select-Object -Last 1)"

    $info = [ordered]@{
        version = ("$($release.tag_name)").TrimStart('v')
        url     = "$($asset.browser_download_url)"
        size    = [long]$asset.size
        digest  = $asset.digest
        page    = "$($release.html_url)"
    }
    $infoFile = Join-Path $ctx.Temp 'gridsim-ci-release.json'
    [System.IO.File]::WriteAllText($infoFile, ($info | ConvertTo-Json))
    $run = Invoke-AppCode -Code "import json; from gridsim import update; info = json.load(open(r'$infoFile', encoding='utf-8')); print(update.download(info))" -TimeoutSec 1500
    $path = ("$(@($run.Lines) | Where-Object { $_ } | Select-Object -Last 1)").Trim()
    if ($run.ExitCode -ne 0 -or -not $path -or -not (Test-Path -LiteralPath $path)) {
        Stop-Test -Expected 'gridsim.update.download(info) to download the installer and return its path' -Found "exit code $($run.ExitCode): $($run.Text)"
    }
    $file = Get-Item -LiteralPath $path
    Expect ($file.Length -eq [long]$asset.size) "a download of exactly $($asset.size) bytes" "$($file.Length) bytes"
    Expect ($null -eq (Get-Item -LiteralPath $path -Stream 'Zone.Identifier' -ErrorAction SilentlyContinue)) 'no Zone.Identifier stream on the downloaded installer' 'the stream is there'
    Expect ($file.FullName.StartsWith($ctx.State, [System.StringComparison]::OrdinalIgnoreCase)) "the download to land in the state folder ($($ctx.State)\update)" $file.FullName
    if ("$($asset.digest)" -like 'sha256:*') {
        $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        Expect ("sha256:$hash" -eq "$($asset.digest)".ToLowerInvariant()) 'the SHA-256 of the download to match the digest GitHub publishes' "sha256:$hash"
    }
    Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
}
