# Test 12: the scheduled background jobs, through the program's own command (python -m gridsim schedule ...).

Invoke-Test -Id '12' -Name 'Scheduled jobs: gridsim schedule --install / --status / --repoint / --remove, and Windows really runs them' -NeedsInstall -Body {
    # A scheduled job gets the signed-in user's environment, not this script's: point that at the test folders too.
    [Environment]::SetEnvironmentVariable('GRIDSIM_DATA', $ctx.Data, 'User')
    [Environment]::SetEnvironmentVariable('GRIDSIM_STATE', $ctx.State, 'User')
    $ctx.UserEnvSet = $true

    try {
        $run = Invoke-App -Arguments '-m gridsim schedule --install' -TimeoutSec 300
        $null = Save-Text -Name 'schedule-install.txt' -Lines $run.Lines
        Expect ($run.ExitCode -eq 0) "'python -m gridsim schedule --install' to end with exit code 0" "exit code $($run.ExitCode); it said: $($run.Text)"

        $wanted = @{
            'GridSim Weekly'  = '-m gridsim weekly'
            'GridSim Predict' = '-m gridsim predict-week'
            'GridSim Scan'    = '-m gridsim scan --notify'
            'GridSim Watch'   = '-m gridsim watch --near --notify'
        }
        foreach ($name in $ctx.TaskNames) {
            $task = Get-TaskFacts -TaskName $name
            if ($null -eq $task) {
                Expect $false "the job '$name' after schedule --install" 'no such job'
                continue
            }
            $null = Save-Text -Name ('task-' + ($name -replace ' ', '-') + '.xml') -Lines $task.Xml
            if ($task.ParseError) {
                Expect $false "readable XML for the job '$name'" $task.ParseError
                continue
            }
            Expect ($task.Command -ieq $ctx.AppPyw) "the job '$name' to start the private pythonw ($($ctx.AppPyw))" "'$($task.Command)'"
            Expect ($task.Arguments -eq $wanted[$name]) "the job '$name' to have the arguments '$($wanted[$name])'" "'$($task.Arguments)'"
            Expect ($task.WorkingDirectory -ieq $ctx.Program) "the job '$name' to start in $($ctx.Program)" "'$($task.WorkingDirectory)'"
            Expect ($task.LogonType -eq 'InteractiveToken') "the job '$name' to run only while the user is signed in (LogonType InteractiveToken)" "'$($task.LogonType)'"
            Expect ($task.DisallowStartIfOnBatteries -eq 'false') "the job '$name' to run on battery (DisallowStartIfOnBatteries false)" "'$($task.DisallowStartIfOnBatteries)'"
            Expect ($task.StopIfGoingOnBatteries -eq 'false') "the job '$name' to keep running on battery (StopIfGoingOnBatteries false)" "'$($task.StopIfGoingOnBatteries)'"
            Expect $task.Enabled "the job '$name' to be switched on" 'it is disabled'
            if ($name -in @('GridSim Weekly', 'GridSim Predict')) {
                Expect ($task.StartWhenAvailable -eq 'true') "the job '$name' to catch up a missed run (StartWhenAvailable true)" "'$($task.StartWhenAvailable)'"
            }
            Note "${name}: user '$($task.UserId)', wake the PC $($task.WakeToRun), catch up $($task.StartWhenAvailable), time limit $($task.ExecutionTimeLimit)"
        }

        $run = Invoke-App -Arguments '-m gridsim schedule --status' -TimeoutSec 300
        $null = Save-Text -Name 'schedule-status.txt' -Lines $run.Lines
        Expect ($run.ExitCode -eq 0) "'python -m gridsim schedule --status' to end with exit code 0" "exit code $($run.ExitCode); it said: $($run.Text)"
        # The program reads the four jobs back from Windows ("schtasks /Query /XML") to say this. Were that reading
        # wrong, the jobs would show as half set up for ever and be created again at every start of GridSim.
        $firstLine = ("$(@($run.Lines) | Select-Object -First 1)").Trim()
        Expect ($firstLine -eq 'Background jobs are on.') "the first line of 'schedule --status' right after --install to be 'Background jobs are on.'" "'$firstLine' (all of it: $($run.Text))"

        # ---- for information: when Windows will run them. The Tuesday and Friday jobs must not start by themselves
        #      right after they were set up; the two repeating jobs should take their next turn today.
        foreach ($name in $ctx.TaskNames) {
            $info = Get-ScheduledTaskInfo -TaskName $name -ErrorAction SilentlyContinue
            if ($info) { Note "${name}: last run '$($info.LastRunTime)', last result $($info.LastTaskResult) (267011 = has not run yet), next run '$($info.NextRunTime)'" }
        }

        # ---- does Windows start them? The position watch has nothing to do without positions and ends at once.
        # The jobs run "only while the user is signed in". A test machine that runs this script as a service
        # (Windows session 0) has no signed-in desktop, so Windows cannot start them there whatever the program
        # does: on such a machine a job that does not start is written down as not proven, not as a failure.
        $noDesktop = ((Get-Process -Id $PID).SessionId -eq 0)
        if ($noDesktop) { Note 'this script runs in Windows session 0 (no signed-in desktop): Windows may refuse to start the jobs here' }
        if (Test-TaskExists -TaskName 'GridSim Watch') {
            $before = Get-TaskLastRun -TaskName 'GridSim Watch'
            & schtasks.exe /Run /TN 'GridSim Watch' 2>&1 | Out-Null
            Expect ($LASTEXITCODE -eq 0) "'schtasks /Run' of GridSim Watch to be accepted" "exit code $LASTEXITCODE"
            $result = Wait-TaskRun -TaskName 'GridSim Watch' -Before $before -TimeoutSec 180
            if ($noDesktop -and ($null -eq $result -or $result -ne 0)) {
                Note "NOT proven on this machine (no signed-in desktop session): the job GridSim Watch did not run to a good end within 180 s (result: '$result')"
            }
            elseif ($null -eq $result) {
                Expect $false 'the job GridSim Watch to start and finish within 180 s' 'Windows shows no finished run'
            }
            else {
                Expect ($result -eq 0) 'the job GridSim Watch to end with result 0' ('result 0x{0:X}' -f $result)
            }
        }

        # ---- the weekly job writes its first line to logs\scheduled.log in the data folder as soon as it starts;
        #      that line is all this test waits for (the real job would download data and simulate for a long time).
        if (Test-TaskExists -TaskName 'GridSim Weekly') {
            $logs = @((Join-Path $ctx.Data 'logs\scheduled.log'), (Join-Path $env:USERPROFILE 'GridSim Data\logs\scheduled.log'))
            $pattern = '^=== .*gridsim weekly\s*$'
            $countBefore = @{}
            foreach ($log in $logs) {
                $countBefore[$log] = 0
                if (Test-Path -LiteralPath $log) { $countBefore[$log] = @(Select-String -LiteralPath $log -Pattern $pattern).Count }
            }
            & schtasks.exe /Run /TN 'GridSim Weekly' 2>&1 | Out-Null
            Expect ($LASTEXITCODE -eq 0) "'schtasks /Run' of GridSim Weekly to be accepted" "exit code $LASTEXITCODE"
            $hit = ''
            $clock = [System.Diagnostics.Stopwatch]::StartNew()
            while (-not $hit -and $clock.Elapsed.TotalSeconds -lt 120) {
                foreach ($log in $logs) {
                    if ((Test-Path -LiteralPath $log) -and (@(Select-String -LiteralPath $log -Pattern $pattern).Count -gt $countBefore[$log])) { $hit = $log }
                }
                if (-not $hit) { Start-Sleep -Milliseconds 500 }
            }
            & schtasks.exe /End /TN 'GridSim Weekly' 2>&1 | Out-Null
            Stop-Launchers
            if ($hit) {
                Note "the weekly job started by Task Scheduler wrote its first line to $hit after $([int]$clock.Elapsed.TotalSeconds) s (then it was ended)"
                if ($hit -ne $logs[0]) { Note 'Task Scheduler did not pass the user-level GRIDSIM_DATA to the job; it used the default data folder of this test machine' }
            }
            elseif ($noDesktop) {
                Note 'NOT proven on this machine: the job GridSim Weekly wrote no line to logs\scheduled.log within 120 s (no signed-in desktop session)'
            }
            else {
                Expect $false "a new line '=== <time> gridsim weekly' in logs\scheduled.log within 120 s after 'schtasks /Run'" 'none (the job did not start, or could not write its log)'
            }
        }

        # ---- --repoint must switch a paused job back on (the installer pauses the jobs and relies on this)
        if (Test-TaskExists -TaskName 'GridSim Scan') {
            & schtasks.exe /Change /TN 'GridSim Scan' /DISABLE 2>&1 | Out-Null
            $paused = Get-TaskFacts -TaskName 'GridSim Scan'
            $run = Invoke-App -Arguments '-m gridsim schedule --repoint' -TimeoutSec 300
            Expect ($run.ExitCode -eq 0) "'python -m gridsim schedule --repoint' to end with exit code 0" "exit code $($run.ExitCode); it said: $($run.Text)"
            $again = Get-TaskFacts -TaskName 'GridSim Scan'
            if ($paused -and -not $paused.Enabled) {
                Expect ($again -and $again.Enabled) 'schedule --repoint to switch a paused job back on' 'GridSim Scan is still disabled'
            }
            else {
                Note 'the job GridSim Scan could not be paused for the --repoint check'
            }
        }

        # ---- for information: what Windows accepts as task XML (settles two open questions in one run)
        $sample = Get-TaskFacts -TaskName 'GridSim Weekly'
        if ($sample -and -not $sample.ParseError) {
            $file = Join-Path $ctx.Temp 'gridsim-ci-task.xml'
            $noUser = $sample.Xml -replace '(?s)<UserId>.*?</UserId>', ''
            [System.IO.File]::WriteAllText($file, $noUser, [System.Text.Encoding]::Unicode)
            $said = & schtasks.exe /Create /TN 'GridSim CI NoUserId' /XML $file /F 2>&1
            Note "task XML without <UserId> (UTF-16): schtasks /Create exit code $LASTEXITCODE"
            Remove-Task -TaskName 'GridSim CI NoUserId'
            $plain = $sample.Xml -replace '^\s*<\?xml[^>]*\?>\s*', ''
            [System.IO.File]::WriteAllText($file, $plain, (New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false))
            $said = & schtasks.exe /Create /TN 'GridSim CI Utf8' /XML $file /F 2>&1
            Note "task XML as UTF-8 without the <?xml ...?> line: schtasks /Create exit code $LASTEXITCODE"
            Remove-Task -TaskName 'GridSim CI Utf8'
        }
        $null = & schtasks.exe /Query /TN 'GridSim CI Missing' 2>&1
        $queryCode = $LASTEXITCODE
        $null = & schtasks.exe /Delete /F /TN 'GridSim CI Missing' 2>&1
        $deleteCode = $LASTEXITCODE
        Note "exit codes for a job that does not exist: /Query $queryCode, /Delete $deleteCode"
        Expect ($queryCode -ne 0) "a non-zero exit code from 'schtasks /Query' for a job that does not exist (the program decides by it)" 'exit code 0'

        # ---- --remove, then --install again (the update and uninstall tests need the jobs)
        $run = Invoke-App -Arguments '-m gridsim schedule --remove' -TimeoutSec 300
        $null = Save-Text -Name 'schedule-remove.txt' -Lines $run.Lines
        Expect ($run.ExitCode -eq 0) "'python -m gridsim schedule --remove' to end with exit code 0" "exit code $($run.ExitCode); it said: $($run.Text)"
        foreach ($name in $ctx.TaskNames) {
            Expect (-not (Test-TaskExists -TaskName $name)) "no job '$name' after schedule --remove" 'it still exists'
        }
        $run = Invoke-App -Arguments '-m gridsim schedule --repoint' -TimeoutSec 300
        Note "schedule --repoint with no jobs at all: exit code $($run.ExitCode)"
        foreach ($name in $ctx.TaskNames) {
            Expect (-not (Test-TaskExists -TaskName $name)) "schedule --repoint to create no job the user does not have ('$name')" 'it created the job'
        }
        $run = Invoke-App -Arguments '-m gridsim schedule --install' -TimeoutSec 300
        Expect ($run.ExitCode -eq 0) "a second 'schedule --install' to end with exit code 0" "exit code $($run.ExitCode); it said: $($run.Text)"
    }
    finally {
        Stop-Launchers
    }
}
