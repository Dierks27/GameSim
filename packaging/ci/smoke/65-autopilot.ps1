# Test 18: the installed program does its own upkeep for real.
#
# Every other test keeps GridSim's background upkeep ("gridsim autopilot") switched off, because it downloads a
# season of football data and simulates a week of games. Here it runs once, on purpose, with the installed
# program's own Python and a data folder of its own: download the data, lock in predictions for the coming games,
# fetch Kalshi's prices, run the weekly check-up. That is the whole chain a user's PC goes through after the first
# start - the libraries, the downloads, the simulation and the files it writes - on Windows.

Invoke-Test -Id '18' -Name 'The installed program does its own upkeep for real (gridsim autopilot: data, lock-in, prices, check-up)' -NeedsInstall -Body {
    $data = Join-Path $ctx.Temp 'GridSim Data (upkeep test)'
    $state = Join-Path $ctx.Temp 'GridSim State (upkeep test)'
    New-Item -ItemType Directory -Force -Path $data | Out-Null
    New-Item -ItemType Directory -Force -Path $state | Out-Null
    # fewer simulations per game than the real setting: the chain is what is tested, not the numbers
    Set-Content -LiteralPath (Join-Path $data 'config.local.yaml') -Value @('sim:', '  n_sims: 200') -Encoding ascii
    $envs = @{ GRIDSIM_DATA = $data; GRIDSIM_STATE = $state }

    $dry = Invoke-App -Arguments '-m gridsim autopilot --dry-run' -TimeoutSec 600 -Environment $envs
    $null = Save-Text -Name 'autopilot-dry-run.txt' -Lines $dry.Lines
    Expect ($dry.ExitCode -eq 0) 'gridsim autopilot --dry-run to exit with code 0' "exit code $($dry.ExitCode): $($dry.Text)"
    Expect (@($dry.Lines | Where-Object { "$_" -like 'due:*' }).Count -gt 0) 'something to be due in an empty data folder' "it said: $($dry.Text)"

    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    $run = Invoke-App -Arguments '-m gridsim autopilot' -TimeoutSec 1800 -Environment $envs
    $null = Save-Text -Name 'autopilot-run.txt' -Lines $run.Lines
    Note "the run took $([int]$clock.Elapsed.TotalSeconds) s and ended with exit code $($run.ExitCode)"
    if ($run.TimedOut) {
        Stop-Test -Expected 'gridsim autopilot to finish within 30 minutes' -Found 'it was still running (ended by the test; see ci-logs\autopilot-run.txt)'
    }

    $statusPath = Join-Path $data 'logs\autopilot\status.json'
    $status = Read-JsonFile -Path $statusPath
    if ($null -eq $status) {
        Stop-Test -Expected 'logs\autopilot\status.json in the data folder after the run' -Found "none; the run said: $($run.Text)"
    }
    Copy-Item -LiteralPath $statusPath -Destination (Join-Path $ctx.Logs 'autopilot-status.json') -Force
    $steps = @{}
    foreach ($step in @($status.steps)) {
        $steps["$($step.key)"] = $step
        Note "$($step.what): $($step.state) - $($step.detail)"
    }
    Expect ("$($status.state)" -eq 'done') "the run to be recorded as finished (state 'done')" "'$($status.state)'"
    foreach ($key in @('data', 'grade', 'lockin', 'prices', 'checkup')) {
        Expect ($steps.ContainsKey($key)) "a '$key' step in status.json" 'it is missing'
    }
    if ($steps.ContainsKey('data')) {
        Expect (@('done', 'skipped') -contains "$($steps['data'].state)") 'the data step to succeed (football data downloaded)' "'$($steps['data'].state)': $($steps['data'].detail)"
    }
    # with no game of this week still to come (Monday night, the offseason) there is nothing to lock in
    $nothingToLock = $steps.ContainsKey('lockin') -and "$($steps['lockin'].state)" -eq 'skipped'
    if ($nothingToLock) {
        Note "NOT proven: the lock-in ($($steps['lockin'].detail)); no game of this week was still to come when this ran"
    }
    elseif ($steps.ContainsKey('lockin')) {
        Expect ("$($steps['lockin'].state)" -eq 'done') 'the lock-in step to succeed (the coming games simulated and saved)' "'$($steps['lockin'].state)': $($steps['lockin'].detail)"
    }
    $predictions = @()
    $predDir = Join-Path $data 'predictions'
    if (Test-Path -LiteralPath $predDir) {
        $predictions = @(Get-ChildItem -LiteralPath $predDir -Recurse -File -Filter '*__*.json')
    }
    if (-not $nothingToLock) {
        Expect ($predictions.Count -gt 0) 'locked-in prediction files in the data folder' 'none'
    }
    Note "$($predictions.Count) locked-in prediction files"

    # Kalshi and the check-up depend on things outside GridSim (Kalshi may not answer a test machine; a check-up
    # needs finished weeks): reported, not required
    foreach ($key in @('prices', 'checkup', 'grade')) {
        if ($steps.ContainsKey($key) -and "$($steps[$key].state)" -eq 'failed') {
            Note "NOT proven on this machine - ${key}: $($steps[$key].detail)"
        }
    }
    $snapshots = @()
    $snapDir = Join-Path $data 'signals\snapshots'
    if (Test-Path -LiteralPath $snapDir) { $snapshots = @(Get-ChildItem -LiteralPath $snapDir -File -Filter '*.json') }
    Note "$($snapshots.Count) saved Kalshi price snapshot(s)"

    # a second run right away has nothing left to do
    $again = Invoke-App -Arguments '-m gridsim autopilot --dry-run' -TimeoutSec 600 -Environment $envs
    $null = Save-Text -Name 'autopilot-dry-run-after.txt' -Lines $again.Lines
    $stillDue = @($again.Lines | Where-Object { "$_" -like 'due:*' -and "$_" -notlike '*Kalshi*' -and "$_" -notlike '*check-up*' -and "$_" -notlike '*Grading*' })
    Expect ($stillDue.Count -eq 0) 'nothing about data or lock-in to be due right after a run' ($stillDue -join ' / ')
}
