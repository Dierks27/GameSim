# GridSim's own unit tests, run on Windows with an ordinary Python (the way a folder install runs GridSim).
#
# The smoke tests prove that the installed program starts, shows its window, updates and uninstalls. They do not
# exercise GridSim's own logic. This does: the same test suite that is run on Linux during development, here on
# Windows, where file locking, path names and text encodings differ. The result is written to
# ci-logs\unit-tests.txt. The script ends with pytest's exit code.

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$logs = Join-Path $repo 'ci-logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$out = Join-Path $logs 'unit-tests.txt'

$venv = Join-Path $env:RUNNER_TEMP 'unit venv'
& python -m venv $venv
if ($LASTEXITCODE -ne 0) { throw "python -m venv ended with exit code $LASTEXITCODE" }
$py = Join-Path $venv 'Scripts\python.exe'

Push-Location -LiteralPath $repo
try {
    & $py -m pip install --disable-pip-version-check --quiet -e '.[dev]'
    if ($LASTEXITCODE -ne 0) { throw "pip install ended with exit code $LASTEXITCODE" }
    $version = (& $py --version) 2>&1
    Set-Content -LiteralPath $out -Value "unit tests with $version on Windows" -Encoding utf8

    $env:GRIDSIM_STATE = Join-Path $env:RUNNER_TEMP 'unit state'
    $env:PYTHONIOENCODING = 'utf-8'
    $ErrorActionPreference = 'Continue'
    & $py -m pytest -q -p no:cacheprovider --durations=15 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $out -Append
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}
Write-Host "pytest ended with exit code $code"
exit $code
