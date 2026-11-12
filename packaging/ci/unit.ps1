# GridSim's own unit tests, run on Windows with an ordinary Python (the way a folder install runs GridSim).
#
# The smoke tests prove that the installed program starts, shows its window, updates and uninstalls. They do not
# exercise GridSim's own logic. This does: the same test suite that is run on Linux during development, here on
# Windows, where file locking, path names and text encodings differ.
#
# Each test file is run by itself with a time limit, so one file that gets stuck cannot hide the others. The
# result of every file, and the output of the ones that did not pass, is in ci-logs\unit-tests.txt. The script
# ends with exit code 0 only when every file passed.
#
# Not run here: tests\test_desktop.py. Those tests stand in for Windows with fakes and are written for a machine
# without it; on real Windows some of them reach the real thing (a message box nobody can click) and never end.
# What the launcher really does on Windows is what the smoke tests check.

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$logs = Join-Path $repo 'ci-logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$out = Join-Path $logs 'unit-tests.txt'
$skipOnWindows = @('test_desktop.py')
$limitSec = 600

$venv = Join-Path $env:RUNNER_TEMP 'unit venv'
& python -m venv $venv
if ($LASTEXITCODE -ne 0) { throw "python -m venv ended with exit code $LASTEXITCODE" }
$py = Join-Path $venv 'Scripts\python.exe'

Push-Location -LiteralPath $repo
try {
    & $py -m pip install --disable-pip-version-check --quiet -e '.[dev]'
    if ($LASTEXITCODE -ne 0) { throw "pip install ended with exit code $LASTEXITCODE" }
}
finally {
    Pop-Location
}
$version = "$(& $py --version)"
Set-Content -LiteralPath $out -Value "unit tests with $version on Windows, one test file at a time (limit $limitSec s each)" -Encoding utf8

$env:GRIDSIM_STATE = Join-Path $env:RUNNER_TEMP 'unit state'
$env:PYTHONIOENCODING = 'utf-8'
$work = Join-Path $env:RUNNER_TEMP 'unit out'
New-Item -ItemType Directory -Force -Path $work | Out-Null

$bad = 0
$files = @(Get-ChildItem -LiteralPath (Join-Path $repo 'tests') -File -Filter 'test_*.py' | Sort-Object -Property Name)
foreach ($file in $files) {
    if ($skipOnWindows -contains $file.Name) {
        Add-Content -LiteralPath $out -Value "SKIP     $($file.Name)  (written for a machine without Windows; the smoke tests cover the real thing)" -Encoding utf8
        continue
    }
    $stdout = Join-Path $work "$($file.BaseName).out.txt"
    $stderr = Join-Path $work "$($file.BaseName).err.txt"
    $arguments = @('-X', 'faulthandler', '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-o', 'faulthandler_timeout=180', "tests/$($file.Name)")
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    $proc = Start-Process -FilePath $py -ArgumentList $arguments -WorkingDirectory $repo -NoNewWindow -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $null = $proc.Handle
    $finished = $proc.WaitForExit($limitSec * 1000)
    if ($finished) {
        $proc.WaitForExit()
        $code = $proc.ExitCode
    }
    else {
        & taskkill.exe /PID $proc.Id /T /F *> $null
        $code = -1
    }
    $seconds = [int]$clock.Elapsed.TotalSeconds
    $lines = @()
    if (Test-Path -LiteralPath $stdout) { $lines += @(Get-Content -LiteralPath $stdout) }
    if (Test-Path -LiteralPath $stderr) { $lines += @(Get-Content -LiteralPath $stderr) }
    $last = "$(@($lines | Where-Object { $_ -match 'passed|failed|error|no tests ran' }) | Select-Object -Last 1)"
    if ($code -eq 0 -or $code -eq 5) {
        Add-Content -LiteralPath $out -Value "PASS     $($file.Name)  ($seconds s)  $last" -Encoding utf8
    }
    else {
        $bad += 1
        $word = 'FAIL    '
        if ($code -eq -1) { $word = 'TIMEOUT ' }
        Add-Content -LiteralPath $out -Value "$word $($file.Name)  ($seconds s, exit code $code)  $last" -Encoding utf8
        Add-Content -LiteralPath $out -Value @($lines | Select-Object -Last 150 | ForEach-Object { "    | $_" }) -Encoding utf8
    }
}
Add-Content -LiteralPath $out -Value "$bad test file(s) did not pass." -Encoding utf8
Get-Content -LiteralPath $out | Select-String -Pattern '^(PASS|FAIL|TIMEOUT|SKIP)' | ForEach-Object { Write-Host $_.Line }
Write-Host "$bad test file(s) did not pass; details in ci-logs\unit-tests.txt"
if ($bad -gt 0) { exit 1 }
exit 0
