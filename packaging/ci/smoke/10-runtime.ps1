# Tests 01-04: the private Python and the staged program, before anything is installed.

Invoke-Test -Id '01' -Name 'Every library is installed for this Python (cp313 wheels), the window libraries included' -Body {
    if (-not (Test-Path -LiteralPath $ctx.StagePy)) {
        Stop-Test -Expected 'the staged program in dist\GridSim (written by build.ps1)' -Found "no $($ctx.StagePy)"
    }
    $pyVersion = & $ctx.StagePy -c "import sys; print(sys.version)"
    Note "private Python: $pyVersion"
    $list = Join-Path $ctx.Stage 'python\installed-packages.txt'
    if (Test-Path -LiteralPath $list) {
        $frozen = @(Get-Content -LiteralPath $list)
        Note "$($frozen.Count) packages installed (python\installed-packages.txt; a copy is in ci-logs)"
        $normal = (($frozen -join "`n").ToLower()) -replace '[-_.]+', '-'
        foreach ($name in @('pywebview', 'pythonnet', 'clr-loader', 'proxy-tools', 'bottle', 'streamlit', 'numpy', 'scipy', 'polars', 'pyarrow', 'pandas', 'plotly', 'optuna', 'nflreadpy', 'pyyaml', 'jinja2', 'tzdata')) {
            Expect ($normal -match ('(?m)^' + [regex]::Escape($name) + '==')) "the package $name in the private Python" 'it is not in the list of installed packages'
        }
    }
    else {
        Expect $false 'python\installed-packages.txt in the staged program' 'no such file'
    }
    $check = Invoke-Tool -File $ctx.StagePy -Arguments '-m pip check' -TimeoutSec 300
    $null = Save-Text -Name 'pip-check.txt' -Lines $check.Lines
    $complaints = @($check.Lines | Where-Object { $_ -match 'requires|has requirement|not installed' })
    if ($check.ExitCode -ne 0 -and $complaints.Count -eq 0) {
        # build.ps1 already ran "pip check" before the runtime was sealed; pip itself is not needed after that
        Note "pip did not run in the finished runtime (exit code $($check.ExitCode)); the build's own pip check passed. Last line: $(@($check.Lines) | Select-Object -Last 1)"
    }
    else {
        Expect ($check.ExitCode -eq 0) 'pip check to find no broken requirements (exit code 0)' "exit code $($check.ExitCode); $(($complaints | Select-Object -First 3) -join ' / ')"
    }
    $facts = Read-JsonFile -Path (Join-Path $ctx.Logs 'build-facts.json')
    if ($facts) {
        Note "build: runtime $($facts.pbs_asset); pip install $($facts.seconds_pip) s; compiling .pyc $($facts.seconds_compileall) s; staged $($facts.staged_files) files, $($facts.staged_mb) MB; longest path inside the program $($facts.longest_relative_path) characters"
    }
}

Invoke-Test -Id '02' -Name 'The private Python works from another folder and drive, and ignores everything else on the PC' -Body {
    if (-not (Test-Path -LiteralPath $ctx.StagePy)) {
        Stop-Test -Expected 'the staged program in dist\GridSim' -Found 'nothing to test'
    }
    $copy = Join-Path $ctx.Temp 'Grid Sim (test copy)'
    $ctx.Copy = $copy
    if (Test-Path -LiteralPath $copy) { Remove-Item -LiteralPath $copy -Recurse -Force }
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    & robocopy.exe $ctx.Stage $copy /E /MT:8 /NFL /NDL /NJH /NJS /NP /R:1 /W:1 | Out-Null
    if ($LASTEXITCODE -ge 8) {
        Stop-Test -Expected 'a copy of dist\GridSim in a folder with spaces in its name' -Found "robocopy exit code $LASTEXITCODE"
    }
    Note "copied the staged program to '$copy' in $([int]$clock.Elapsed.TotalSeconds) s"

    # what a "pip install --user" of another Python 3.13 on the same PC would leave behind
    $userSite = Join-Path $env:APPDATA 'Python\Python313\site-packages'
    New-Item -ItemType Directory -Force -Path $userSite | Out-Null
    Set-Content -LiteralPath (Join-Path $userSite 'gridsim_ci_usersite_probe.py') -Value 'PLANTED = True' -Encoding ascii
    $ctx.UserSiteProbe = $userSite

    $drive = ''
    foreach ($letter in @('X:', 'Y:', 'W:', 'V:')) {
        if (-not (Test-Path -LiteralPath ($letter + '\'))) { $drive = $letter; break }
    }
    $run = $null
    try {
        # nothing may point back at the folder the program was built in
        try {
            Rename-Item -LiteralPath $ctx.Stage -NewName 'GridSim.build'
            $ctx.StageRenamed = $true
        }
        catch {
            Note "the build folder could not be renamed for this test ($($_.Exception.Message)); the checks still run from the copy"
        }
        $root = $copy
        if ($drive) {
            & subst.exe $drive $copy
            if ($LASTEXITCODE -eq 0) { $ctx.SubstDrive = $drive; $root = $drive } else { Note "subst $drive failed (exit code $LASTEXITCODE); testing from the folder itself" }
        }
        else {
            Note 'no free drive letter for subst; testing from the folder itself'
        }
        # no other Python reachable through PATH, misleading PYTHONHOME / PYTHONPATH, an unrelated working directory
        $hostile = @{
            PATH       = "$env:SystemRoot\System32;$env:SystemRoot"
            PYTHONHOME = 'C:\does-not-exist'
            PYTHONPATH = 'C:\does-not-exist'
        }
        $relocated = $root + '\python\python.exe'      # (plain text: a drive letter made a moment ago is new to PowerShell)
        $checker = '"' + (Join-Path $ctx.Repo 'packaging\smoke_runtime.py') + '"'
        $run = Invoke-Tool -File $relocated -Arguments $checker -TimeoutSec 1200 -WorkDir $env:SystemRoot -Environment $hostile
    }
    finally {
        if ($ctx.SubstDrive) {
            & subst.exe $ctx.SubstDrive /D | Out-Null
            $ctx.SubstDrive = ''
        }
        $renamed = Join-Path (Split-Path -Parent $ctx.Stage) 'GridSim.build'
        if ((Test-Path -LiteralPath $renamed) -and -not (Test-Path -LiteralPath $ctx.Stage)) {
            Rename-Item -LiteralPath $renamed -NewName 'GridSim'
        }
        $ctx.StageRenamed = $false
    }
    if ($null -eq $run) {
        Stop-Test -Expected 'packaging\smoke_runtime.py to run in the relocated Python' -Found 'it could not be started'
    }
    $code = $run.ExitCode
    $output = $run.Lines
    $lines = Save-Text -Name 'runtime-relocated.txt' -Lines $output
    foreach ($line in $lines) {
        if ($line -like 'NOTE*') { Note ($line.Substring(4).Trim()) }
    }
    $failures = @($lines | Where-Object { $_ -like 'FAIL*' })
    foreach ($line in $failures) { Expect $false 'every check of packaging\smoke_runtime.py to pass' $line }
    Expect ($code -eq 0) 'packaging\smoke_runtime.py to end with exit code 0 (full output: ci-logs\runtime-relocated.txt)' "exit code $code; last line: $($lines | Select-Object -Last 1)"
}

Invoke-Test -Id '03' -Name 'The app server starts from the private Python (from the folder with spaces) and answers "ok"' -Body {
    Start-AutopilotPlaceholder
    $root = $ctx.Stage
    if ($ctx.Copy -and (Test-Path -LiteralPath (Join-Path $ctx.Copy 'python\python.exe'))) { $root = $ctx.Copy }
    $py = Join-Path $root 'python\python.exe'
    $program = Join-Path $root 'program'
    $appScript = Join-Path $program 'app\streamlit_app.py'
    $template = '-m streamlit run "{0}" --global.developmentMode false --server.headless true --server.address 127.0.0.1 --server.port {1} --server.fileWatcherType none --server.runOnSave false --browser.gatherUsageStats false --browser.serverAddress 127.0.0.1 --client.toolbarMode minimal{2}'
    $childEnv = @{ PYTHONIOENCODING = 'utf-8:backslashreplace'; PYTHONUNBUFFERED = '1' }

    $port = 8765
    $server = Start-Proc -File $py -Arguments ($template -f $appScript, $port, '') -WorkDir $program -Hidden -Environment $childEnv -StdOut (Join-Path $ctx.Logs 'server-test03.out.log') -StdErr (Join-Path $ctx.Logs 'server-test03.err.log')
    try {
        $seconds = Wait-Health -Port $port -TimeoutSec 120 -ServerPid $server.Id
        if ($null -eq $seconds) {
            $state = 'it is still running'
            if ($server.HasExited) { $state = "it ended with exit code $($server.ExitCode)" }
            Expect $false "the body 'ok' from http://127.0.0.1:$port/_stcore/health within 120 s" "no 'ok'; the server: $state (see ci-logs\server-test03.*.log)"
        }
        else {
            Note "the server answered 'ok' after $seconds s (first start on this machine, started from $root)"
            $status = & curl.exe -s --noproxy '*' -o NUL -w '%{http_code}' --max-time 20 "http://127.0.0.1:$port/"
            Expect ("$status" -eq '200') 'HTTP 200 for the start page' "HTTP $status"
        }
    }
    finally {
        Stop-Tree -ProcessId $server.Id
    }

    # For information only: let Streamlit run the app script once without a browser and say how that went.
    # On a machine with no football data yet the script may stop early; that is not a failure of the program.
    $port = 8766
    $second = $null
    try {
        $second = Start-Proc -File $py -Arguments ($template -f $appScript, $port, ' --server.scriptHealthCheckEnabled true') -WorkDir $program -Hidden -Environment $childEnv -StdOut (Join-Path $ctx.Logs 'server-test03b.out.log') -StdErr (Join-Path $ctx.Logs 'server-test03b.err.log')
        if ($null -ne (Wait-Health -Port $port -TimeoutSec 120 -ServerPid $second.Id)) {
            $answer = & curl.exe -s --noproxy '*' --max-time 240 -w ' [HTTP %{http_code}]' "http://127.0.0.1:$port/_stcore/script-health-check"
            $text = ("$answer").Trim()
            if ($text.Length -gt 300) { $text = $text.Substring(0, 300) + ' ...' }
            Note "script health check (the app script run once, no data downloaded yet): $text"
        }
        else {
            Note 'script health check: the second server start (with --server.scriptHealthCheckEnabled true) did not answer; see ci-logs\server-test03b.*.log'
        }
    }
    catch {
        Note "script health check could not be made: $($_.Exception.Message)"
    }
    finally {
        if ($second) { Stop-Tree -ProcessId $second.Id }
    }

    if ($ctx.Copy -and (Test-Path -LiteralPath $ctx.Copy)) {
        Start-Sleep -Seconds 2
        try {
            Remove-Item -LiteralPath $ctx.Copy -Recurse -Force
        }
        catch {
            Note "the test copy could not be removed completely: $($_.Exception.Message)"
        }
    }
}

Invoke-Test -Id '04' -Name 'The installer was compiled' -Body {
    if (-not (Test-Path -LiteralPath $ctx.Setup)) {
        Stop-Test -Expected 'dist\GridSim-Setup.exe (compiled by build.ps1 with Inno Setup)' -Found 'no such file; see ci-logs\iscc.txt'
    }
    $file = Get-Item -LiteralPath $ctx.Setup
    $mb = [math]::Round($file.Length / 1MB, 1)
    Expect ($file.Length -gt 50MB) 'an installer of at least 50 MB (it holds Python and every library)' "$mb MB"
    Expect ($file.Length -lt 2000MB) 'an installer under 2 GB (the limit for a GitHub release file)' "$mb MB"
    $facts = Read-JsonFile -Path (Join-Path $ctx.Logs 'build-facts.json')
    if ($facts) {
        Note "GridSim-Setup.exe: $mb MB, compiled in $($facts.seconds_iscc) s by $($facts.iscc)"
    }
    else {
        Note "GridSim-Setup.exe: $mb MB"
    }
    $info = $file.VersionInfo
    $shown = "product version '$($info.ProductVersion)', file version '$($info.FileVersion)'"
    Expect ($shown.Contains($ctx.Version)) "the installer's version to be GridSim's ($($ctx.Version))" $shown
    Note "signature of the installer: $((Get-AuthenticodeSignature -LiteralPath $ctx.Setup).Status)"
}
