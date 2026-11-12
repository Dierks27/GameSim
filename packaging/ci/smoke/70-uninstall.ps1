# Tests 16 and 17: uninstall, and the list of which program files carry a signature.

Invoke-Test -Id '16' -Name 'Uninstall: program, shortcuts and scheduled jobs go; the data folder stays' -NeedsInstall -Body {
    $uninstaller = Join-Path $ctx.App 'unins000.exe'
    if (-not (Test-Path -LiteralPath $uninstaller)) {
        Stop-Test -Expected 'unins000.exe in the install folder' -Found 'no such file'
    }
    $jobsBefore = @($ctx.TaskNames | Where-Object { Test-TaskExists -TaskName $_ })
    Note "scheduled jobs before the uninstall: $($jobsBefore -join ', ')"
    # something still running from the program (a scheduled job, a forgotten window) must not block the uninstall
    $sleeper = Start-Proc -File $ctx.AppPyw -Arguments '-c "import time; time.sleep(900)"' -WorkDir $ctx.Program
    Start-Sleep -Seconds 2
    $log = Join-Path $ctx.Logs 'inno-uninstall.log'
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    $proc = Start-Proc -File $uninstaller -Arguments ('/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="{0}"' -f $log)
    try {
        if (-not $proc.WaitForExit(10 * 60 * 1000)) {
            Stop-Tree -ProcessId $proc.Id
            Stop-Test -Expected 'the uninstaller to finish within 10 minutes' -Found 'it was still running (ended by the test; see ci-logs\inno-uninstall.log)'
        }
        $proc.WaitForExit()
        Expect ($proc.ExitCode -eq 0) 'uninstaller exit code 0' "exit code $($proc.ExitCode) (see ci-logs\inno-uninstall.log)"

        # the uninstaller's second half (a copy of itself in the temp folder) finishes a little after the exit code
        $pythonDir = Join-Path $ctx.App 'python'
        $wait = [System.Diagnostics.Stopwatch]::StartNew()
        while ((Test-Path -LiteralPath $pythonDir) -and $wait.Elapsed.TotalSeconds -lt 60) { Start-Sleep -Milliseconds 500 }
        Expect (-not (Test-Path -LiteralPath $pythonDir)) 'the folder python\ to be gone within 60 s after the uninstaller ended' 'it is still there'
        Note "the uninstall took $([int]$clock.Elapsed.TotalSeconds) s"
        Start-Sleep -Seconds 3
        foreach ($rel in @('program', 'tools', 'gridsim.ico', 'unins000.exe')) {
            Expect (-not (Test-Path -LiteralPath (Join-Path $ctx.App $rel))) "$rel to be gone from the install folder" 'it is still there'
        }
        if (Test-Path -LiteralPath $ctx.App) {
            $rest = @(Get-ChildItem -LiteralPath $ctx.App -Recurse -Force -ErrorAction SilentlyContinue | Select-Object -First 15 | ForEach-Object { $_.FullName })
            Expect $false "the install folder $($ctx.App) to be gone" "it still holds: $($rest -join '; ')"
        }
        Expect ($sleeper.HasExited) 'a process still running from the program to be ended by the uninstaller' "pid $($sleeper.Id) is still running"
        foreach ($link in @($ctx.StartLink, $ctx.DesktopLink)) {
            Expect (-not (Test-Path -LiteralPath $link)) "the shortcut $link to be gone" 'it is still there'
        }
        foreach ($name in $ctx.TaskNames) {
            Expect (-not (Test-TaskExists -TaskName $name)) "the scheduled job '$name' to be gone" 'it still exists'
        }
        Expect (-not (Test-Path -LiteralPath $ctx.UninstallKey)) 'the entry under Installed apps to be gone' 'the registry key is still there'
        Expect ((Test-Path -LiteralPath $ctx.Marker) -and ((Get-Content -LiteralPath $ctx.Marker -Raw).Trim() -eq $ctx.MarkerText)) 'the marker file in the data folder untouched by the uninstall' 'it is gone or changed'
        $kept = @(Get-ChildItem -LiteralPath $ctx.Data -Force | ForEach-Object { $_.Name })
        Note "the data folder still holds: $($kept -join ', ')"
        if (Test-Path -LiteralPath $ctx.State) { Note "the state folder (logs, window profile) is left in place: $($ctx.State)" }
        $ctx.Installed = $false
    }
    finally {
        if (-not $sleeper.HasExited) { Stop-Tree -ProcessId $sleeper.Id }
        if (-not $proc.HasExited) { Stop-Tree -ProcessId $proc.Id }
    }
}

Invoke-Test -Id '17' -Name 'For information: which program files are signed (ci-logs\signatures.csv)' -Body {
    if (-not (Test-Path -LiteralPath $ctx.Stage)) {
        Skip-Test -Reason 'no staged program to look at'
    }
    $files = @(Get-ChildItem -LiteralPath $ctx.Stage -Recurse -File | Where-Object { $_.Extension -in @('.exe', '.dll', '.pyd') })
    $prefix = $ctx.Stage.TrimEnd('\') + '\'
    $rows = foreach ($file in $files) {
        $signature = Get-AuthenticodeSignature -LiteralPath $file.FullName
        $signer = ''
        if ($signature.SignerCertificate) { $signer = $signature.SignerCertificate.Subject }
        [pscustomobject]@{
            File   = $file.FullName.Substring($prefix.Length)
            Bytes  = $file.Length
            Status = "$($signature.Status)"
            Signer = $signer
        }
    }
    $rows = @($rows)
    $rows | Export-Csv -LiteralPath (Join-Path $ctx.Logs 'signatures.csv') -NoTypeInformation -Encoding utf8
    $signed = @($rows | Where-Object { $_.Status -eq 'Valid' }).Count
    $unsigned = @($rows | Where-Object { $_.Status -eq 'NotSigned' }).Count
    $other = $rows.Count - $signed - $unsigned
    Note "$($rows.Count) .exe/.dll/.pyd files in the program: $signed signed and valid, $unsigned not signed, $other other"
    foreach ($name in @('python\python.exe', 'python\pythonw.exe', 'python\python313.dll', 'python\vcruntime140.dll')) {
        $row = @($rows | Where-Object { $_.File -ieq $name }) | Select-Object -First 1
        if ($row) { Note "${name}: $($row.Status)" }
    }
    if (Test-Path -LiteralPath $ctx.Setup) {
        Note "GridSim-Setup.exe: $((Get-AuthenticodeSignature -LiteralPath $ctx.Setup).Status)"
    }
}
