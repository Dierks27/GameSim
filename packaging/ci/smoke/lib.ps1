# Helpers of the smoke test. Dot-sourced by packaging\ci\smoke.ps1, which defines $ctx (paths and shared facts)
# before any function here is called. Plain ASCII; no aliases; every wait has a timeout.

$script:Results = [System.Collections.Generic.List[object]]::new()
$script:Problems = [System.Collections.Generic.List[string]]::new()
$script:Notes = [System.Collections.Generic.List[string]]::new()
$script:LogOffsets = @{}

# ---- recording results ------------------------------------------------------------------------------------------

function Note {
    # A fact worth reading in summary.txt that is not a failure.
    param([string]$Text)
    $script:Notes.Add($Text)
    Write-Host "      note: $Text"
}

function Expect {
    # One check inside a test. A check that fails is recorded and the test goes on, so one run shows everything.
    param([bool]$Ok, [string]$Expected, [string]$Found)
    if ($Ok) { return }
    $line = "expected $Expected; found $Found"
    $script:Problems.Add($line)
    Write-Host "      PROBLEM: $line"
}

function Stop-Test {
    # A failed check after which the rest of the test makes no sense.
    param([string]$Expected, [string]$Found)
    throw "expected $Expected; found $Found"
}

function Skip-Test {
    param([string]$Reason)
    throw "SKIP: $Reason"
}

function Write-SummaryFile {
    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add("GridSim windows-installer smoke test, $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') runner time, commit $($ctx.Commit), GridSim $($ctx.Version)")
    $lines.Add('PASS = as expected.  FAIL = see the PROBLEM lines.  BLOCKED = could not run because an earlier step failed.  SKIP = not applicable in this run.')
    $lines.Add('')
    foreach ($r in $script:Results) {
        $lines.Add(('{0,-8}{1,-5}{2}  ({3} s)' -f $r.Status, $r.Id, $r.Name, $r.Seconds))
        foreach ($problem in $r.Problems) { $lines.Add("             PROBLEM: $problem") }
        foreach ($remark in $r.Notes) { $lines.Add("             note: $remark") }
    }
    $lines.Add('')
    $passed = @($script:Results | Where-Object { $_.Status -eq 'PASS' }).Count
    $failed = @($script:Results | Where-Object { $_.Status -eq 'FAIL' }).Count
    $blocked = @($script:Results | Where-Object { $_.Status -eq 'BLOCKED' }).Count
    $skipped = @($script:Results | Where-Object { $_.Status -eq 'SKIP' }).Count
    $lines.Add("$passed passed, $failed failed, $blocked blocked, $skipped skipped")
    Set-Content -LiteralPath (Join-Path $ctx.Logs 'summary.txt') -Value $lines -Encoding utf8
}

function Add-Result {
    param([string]$Id, [string]$Name, [string]$Status, [int]$Seconds, [string[]]$Problems, [string[]]$Notes)
    $script:Results.Add([pscustomobject]@{
            Id       = $Id
            Name     = $Name
            Status   = $Status
            Seconds  = $Seconds
            Problems = @($Problems)
            Notes    = @($Notes)
        })
    Write-Host ('{0,-8}{1,-5}{2}  ({3} s)' -f $Status, $Id, $Name, $Seconds)
    foreach ($problem in @($Problems)) { Write-Host "        PROBLEM: $problem" }
    Write-SummaryFile
}

function Invoke-Test {
    # Runs one numbered test. Whatever happens inside (a failed check, a PowerShell error, a mistake in this
    # script) is recorded for that test only; the next test still runs.
    param(
        [Parameter(Mandatory = $true)][string]$Id,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$Body,
        [switch]$NeedsInstall
    )
    $script:Problems = [System.Collections.Generic.List[string]]::new()
    $script:Notes = [System.Collections.Generic.List[string]]::new()
    $status = 'PASS'
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    Write-Host ''
    Write-Host "::group::Test $Id - $Name"
    if ($NeedsInstall -and -not $ctx.Installed) {
        $status = 'BLOCKED'
        $script:Problems.Add('not run: GridSim is not installed on this machine (test 05 did not get that far)')
    }
    else {
        try {
            & $Body
        }
        catch {
            $message = "$($_.Exception.Message)"
            if ($message.StartsWith('SKIP: ')) {
                $status = 'SKIP'
                $script:Notes.Add($message.Substring(6))
            }
            else {
                $where = ''
                if ($_.InvocationInfo -and $_.InvocationInfo.ScriptName) {
                    $where = " [$(Split-Path -Leaf $_.InvocationInfo.ScriptName) line $($_.InvocationInfo.ScriptLineNumber)]"
                }
                $script:Problems.Add("$message$where")
            }
        }
        if ($script:Problems.Count -gt 0) { $status = 'FAIL' }
    }
    Write-Host '::endgroup::'
    $seconds = [int][math]::Round($clock.Elapsed.TotalSeconds)
    Add-Result -Id $Id -Name $Name -Status $status -Seconds $seconds -Problems @($script:Problems) -Notes @($script:Notes)
    if ($status -eq 'FAIL') {
        Save-ProcessList -Label "after-test$Id"
    }
}

# ---- processes --------------------------------------------------------------------------------------------------

function Set-EnvValue {
    # Sets or (with $null) removes an environment variable of this process.
    param([string]$Name, $Value)
    if ($null -eq $Value) {
        Remove-Item -LiteralPath ('Env:\' + $Name) -ErrorAction SilentlyContinue
    }
    else {
        Set-Item -LiteralPath ('Env:\' + $Name) -Value ([string]$Value)
    }
}

function Start-Proc {
    # Starts a program without waiting for it and returns its Process object (exit code readable later).
    # $Arguments is ONE string, already quoted where needed. $Environment is set only for the new process.
    param(
        [Parameter(Mandatory = $true)][string]$File,
        [string]$Arguments = '',
        [string]$WorkDir = '',
        [hashtable]$Environment = @{},
        [switch]$Hidden,
        [string]$StdOut = '',
        [string]$StdErr = ''
    )
    $saved = @{}
    foreach ($key in @($Environment.Keys)) {
        $saved[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        Set-EnvValue -Name $key -Value $Environment[$key]
    }
    try {
        $sp = @{ FilePath = $File; PassThru = $true }
        if ($Arguments) { $sp['ArgumentList'] = $Arguments }
        if ($WorkDir) { $sp['WorkingDirectory'] = $WorkDir }
        if ($Hidden) { $sp['WindowStyle'] = 'Hidden' }
        if ($StdOut) { $sp['RedirectStandardOutput'] = $StdOut }
        if ($StdErr) { $sp['RedirectStandardError'] = $StdErr }
        $proc = Start-Process @sp
        $null = $proc.Handle          # keeps the handle, so ExitCode can be read after the process has ended
        return $proc
    }
    finally {
        foreach ($key in @($saved.Keys)) {
            Set-EnvValue -Name $key -Value $saved[$key]
        }
    }
}

function Invoke-Tool {
    # Runs a program without a window, waits at most $TimeoutSec and returns ExitCode, Lines (what it printed:
    # standard output, then standard error) and TimedOut. A program still running at the timeout is ended, so a
    # hanging program can never hang the whole test run. $Arguments is ONE string, already quoted where needed.
    param(
        [Parameter(Mandatory = $true)][string]$File,
        [string]$Arguments = '',
        [int]$TimeoutSec = 300,
        [string]$WorkDir = '',
        [hashtable]$Environment = @{}
    )
    # Python writing to a file would use the Windows ANSI code page and fail on a character outside it
    $envs = @{ PYTHONIOENCODING = 'utf-8:backslashreplace' }
    foreach ($key in @($Environment.Keys)) { $envs[$key] = $Environment[$key] }
    $stamp = [guid]::NewGuid().ToString('N')
    $outFile = Join-Path $ctx.Temp "gridsim-ci-$stamp.out.txt"
    $errFile = Join-Path $ctx.Temp "gridsim-ci-$stamp.err.txt"
    $proc = Start-Proc -File $File -Arguments $Arguments -WorkDir $WorkDir -Environment $envs -Hidden -StdOut $outFile -StdErr $errFile
    $timedOut = $false
    $code = -1
    if ($proc.WaitForExit($TimeoutSec * 1000)) {
        $proc.WaitForExit()
        $code = $proc.ExitCode
    }
    else {
        $timedOut = $true
        Stop-Tree -ProcessId $proc.Id
    }
    $lines = @()
    foreach ($file in @($outFile, $errFile)) {
        if (Test-Path -LiteralPath $file) {
            $lines += @(Get-Content -LiteralPath $file -ErrorAction SilentlyContinue)
            Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue
        }
    }
    if ($timedOut) { $lines += "(still running after $TimeoutSec s; ended by the test)" }
    return [pscustomobject]@{ ExitCode = $code; Lines = $lines; TimedOut = $timedOut; Text = (@($lines) -join ' / ') }
}

function Invoke-App {
    # "python.exe <arguments>" with the installed program's Python (for example '-m gridsim schedule --status').
    # $Environment is set for that one call only.
    param([string]$Arguments, [int]$TimeoutSec = 300, [hashtable]$Environment = @{})
    return (Invoke-Tool -File $ctx.AppPy -Arguments $Arguments -TimeoutSec $TimeoutSec -WorkDir $ctx.Program -Environment $Environment)
}

function Invoke-AppCode {
    # Runs a line of Python in the installed program's Python. The code must not contain a double quote.
    param([string]$Code, [int]$TimeoutSec = 300, [hashtable]$Environment = @{})
    return (Invoke-App -Arguments ('-c "' + $Code + '"') -TimeoutSec $TimeoutSec -Environment $Environment)
}

function Get-GitHubTokenEnv {
    # GridSim's update check sends GRIDSIM_GITHUB_TOKEN to api.github.com when it is set (test machines share
    # internet addresses and GitHub's limit for anonymous questions). The workflow hands its token over as GH_TOKEN.
    if ($env:GH_TOKEN) { return @{ GRIDSIM_GITHUB_TOKEN = $env:GH_TOKEN } }
    return @{}
}

function Test-Alive {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return $false }
    return ($null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue))
}

function Wait-Gone {
    # Waits until a process id no longer exists. Returns the seconds it took, or $null on timeout.
    param([int]$ProcessId, [int]$TimeoutSec)
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    while ($clock.Elapsed.TotalSeconds -lt $TimeoutSec) {
        if (-not (Test-Alive -ProcessId $ProcessId)) { return [math]::Max(0.1, [math]::Round($clock.Elapsed.TotalSeconds, 1)) }
        Start-Sleep -Milliseconds 200
    }
    return $null
}

function Stop-Tree {
    # Ends a process and everything it started, by force.
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return }
    & taskkill.exe /PID $ProcessId /T /F 2>&1 | Out-Null
}

function Get-ProcsUnder {
    # Every running process whose program file lies under $Dir (Win32_Process rows).
    param([string]$Dir)
    $prefix = $Dir.TrimEnd('\') + '\'
    $all = @(Get-CimInstance -ClassName Win32_Process)
    return @($all | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase) })
}

function Format-Proc {
    param($Row)
    return "pid $($Row.ProcessId) (parent $($Row.ParentProcessId)) $($Row.Name): $($Row.CommandLine)"
}

function Stop-Launchers {
    # Clean slate between tests: ends everything that runs from the installed program, by force.
    for ($round = 0; $round -lt 40; $round++) {
        $left = @(Get-ProcsUnder -Dir $ctx.App)
        if ($left.Count -eq 0) { break }
        foreach ($row in $left) {
            Stop-Process -Id $row.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Milliseconds 250
    }
    # A launcher that is ended while its WebView2 window is still opening leaves the note "webview.starting" in the
    # state folder, and the next normal start then uses the Edge window once (that is what the note is for after a
    # real crash). Here the test ended the launcher, so the note goes: every test starts from the same place.
    $note = Join-Path $ctx.State 'webview.starting'
    if (Test-Path -LiteralPath $note) {
        Remove-Item -LiteralPath $note -Force -ErrorAction SilentlyContinue
        Write-Host '      (removed the note webview.starting that a launcher ended by the test left behind)'
    }
}

function Get-Leftovers {
    # After a launcher has ended: waits up to $TimeoutSec for the installed program's processes to disappear.
    # Returns Bad (anything that should not be there) and Background (jobs the app starts on purpose and that
    # may outlive the window: "gridsim autopilot", "gridsim tune", and scheduled jobs).
    param([int]$TimeoutSec = 20)
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    $left = @()
    while ($true) {
        $left = @(Get-ProcsUnder -Dir $ctx.App)
        if ($left.Count -eq 0 -or $clock.Elapsed.TotalSeconds -ge $TimeoutSec) { break }
        Start-Sleep -Milliseconds 500
    }
    $bad = [System.Collections.Generic.List[string]]::new()
    $background = [System.Collections.Generic.List[string]]::new()
    foreach ($row in $left) {
        $line = Format-Proc -Row $row
        if ("$($row.CommandLine)" -match '-m gridsim (autopilot|tune|weekly|predict-week|scan|watch)') { $background.Add($line) } else { $bad.Add($line) }
    }
    return [pscustomobject]@{ Bad = $bad.ToArray(); Background = $background.ToArray() }
}

function Save-ProcessList {
    param([string]$Label)
    try {
        $rows = Get-CimInstance -ClassName Win32_Process | Sort-Object -Property ProcessId | ForEach-Object {
            '{0,7} {1,7}  {2,-28} {3}' -f $_.ProcessId, $_.ParentProcessId, $_.Name, $_.CommandLine
        }
        Set-Content -LiteralPath (Join-Path $ctx.Logs "processes-$Label.txt") -Value $rows -Encoding utf8
    }
    catch {
        Write-Host "      (the process list could not be saved: $($_.Exception.Message))"
    }
}

# ---- the launcher and its server --------------------------------------------------------------------------------

function Read-JsonFile {
    # The parsed content of a JSON file, or $null when it is not there or is being written right now.
    param([string]$Path)
    try {
        if (-not (Test-Path -LiteralPath $Path)) { return $null }
        $text = [System.IO.File]::ReadAllText($Path)
        if (-not $text) { return $null }
        return ($text | ConvertFrom-Json)
    }
    catch {
        return $null
    }
}

function Start-Launcher {
    # Starts GridSim the way its shortcut does: pythonw -m gridsim.desktop, working directory <root>\program.
    param([switch]$Smoke, [string]$Window = '')
    $envs = @{}
    if ($Smoke) { $envs['GRIDSIM_SMOKE'] = '1' }
    if ($Window) { $envs['GRIDSIM_WINDOW'] = $Window }
    return (Start-Proc -File $ctx.AppPyw -Arguments '-m gridsim.desktop' -WorkDir $ctx.Program -Environment $envs)
}

function Wait-DesktopJson {
    # Waits for <state>\desktop.json written by a launcher that is alive now (a file left by a killed launcher
    # does not count). $LauncherPid: accept only this launcher (0 = any). $NotPids: launchers that do not count.
    # Returns the parsed file, or $null on timeout or when the launcher it waits for has ended.
    param([int]$LauncherPid = 0, [int]$TimeoutSec = 180, [int[]]$NotPids = @())
    $path = Join-Path $ctx.State 'desktop.json'
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    while ($clock.Elapsed.TotalSeconds -lt $TimeoutSec) {
        $d = Read-JsonFile -Path $path
        if ($d -and $d.pid -and $d.port) {
            $owner = [int]$d.pid
            $wanted = ($LauncherPid -eq 0) -or ($owner -eq $LauncherPid)
            if ($wanted -and ($NotPids -notcontains $owner) -and (Test-Alive -ProcessId $owner)) { return $d }
        }
        if ($LauncherPid -gt 0 -and -not (Test-Alive -ProcessId $LauncherPid)) { return $null }
        Start-Sleep -Milliseconds 300
    }
    return $null
}

function Get-Health {
    # The body of the server's health page ("ok" when it is up), or '' when nothing answers.
    param([int]$Port)
    $body = & curl.exe -s --noproxy '*' --max-time 3 "http://127.0.0.1:$Port/_stcore/health" 2>$null
    if ($LASTEXITCODE -ne 0) { return '' }
    return ("$body").Trim()
}

function Wait-Health {
    # Waits for "ok". Returns the seconds it took, or $null on timeout or when $ServerPid has ended.
    param([int]$Port, [int]$TimeoutSec = 120, [int]$ServerPid = 0)
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    while ($clock.Elapsed.TotalSeconds -lt $TimeoutSec) {
        if ((Get-Health -Port $Port) -eq 'ok') { return [math]::Max(0.1, [math]::Round($clock.Elapsed.TotalSeconds, 1)) }
        if ($ServerPid -gt 0 -and -not (Test-Alive -ProcessId $ServerPid)) { return $null }
        Start-Sleep -Milliseconds 500
    }
    return $null
}

function Test-PortOpen {
    param([int]$Port)
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync('127.0.0.1', $Port)
        if ($task.Wait(1000) -and $client.Connected) { return $true }
        return $false
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Wait-PortClosed {
    # Returns the seconds until nothing listens on the port any more, or $null on timeout.
    param([int]$Port, [int]$TimeoutSec = 30)
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    while ($clock.Elapsed.TotalSeconds -lt $TimeoutSec) {
        if (-not (Test-PortOpen -Port $Port)) { return [math]::Max(0.1, [math]::Round($clock.Elapsed.TotalSeconds, 1)) }
        Start-Sleep -Milliseconds 300
    }
    return $null
}

function Find-GridSimWindow {
    # The process that owns a visible top-level window with "GridSim" in its title (the launcher itself for the
    # pywebview window, a browser process for the fallbacks), or $null.
    param([int]$PreferPid = 0)
    $owners = @(Get-Process | Where-Object { $_.MainWindowHandle -ne [IntPtr]::Zero -and $_.MainWindowTitle -like '*GridSim*' -and $_.Id -ne $PID })
    if ($owners.Count -eq 0) { return $null }
    $preferred = @($owners | Where-Object { $_.Id -eq $PreferPid })
    if ($preferred.Count -gt 0) { return $preferred[0] }
    return $owners[0]
}

function Wait-GridSimWindow {
    # Waits until the launcher's own window is on the screen (desktop.json is written BEFORE the window opens).
    # Tests that end the launcher from outside wait for this first: a launcher that is ended while its window is
    # still opening leaves a note that makes the next start use the Edge window once. Returns the process that
    # owns the window, or $null on timeout or when the launcher has ended.
    param([int]$LauncherPid, [int]$TimeoutSec = 60)
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    while ($clock.Elapsed.TotalSeconds -lt $TimeoutSec) {
        $owner = Find-GridSimWindow -PreferPid $LauncherPid
        if ($owner) {
            Start-Sleep -Seconds 2
            return $owner
        }
        if ($LauncherPid -gt 0 -and -not (Test-Alive -ProcessId $LauncherPid)) { return $null }
        Start-Sleep -Milliseconds 500
    }
    return $null
}

function Get-WindowList {
    # Every process with a visible main window, as text (for the log).
    return @(Get-Process | Where-Object { $_.MainWindowHandle -ne [IntPtr]::Zero -and $_.MainWindowTitle } | ForEach-Object { "pid $($_.Id) $($_.ProcessName): '$($_.MainWindowTitle)'" })
}

function Save-Screenshot {
    # A picture of the whole screen into ci-logs (so a person can see what the window really looks like).
    param([Parameter(Mandatory = $true)][string]$Name)
    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        $bitmap = New-Object -TypeName System.Drawing.Bitmap -ArgumentList $bounds.Width, $bounds.Height
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        try {
            $graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
            $bitmap.Save((Join-Path $ctx.Logs $Name), [System.Drawing.Imaging.ImageFormat]::Png)
        }
        finally {
            $graphics.Dispose()
            $bitmap.Dispose()
        }
        Note "picture of the screen saved: ci-logs\$Name ($($bounds.Width) x $($bounds.Height))"
    }
    catch {
        Note "no picture of the screen ($Name): $($_.Exception.Message)"
    }
}

function Save-LogSlice {
    # Copies what desktop.log and server.log gained since the last call to ci-logs\desktop-test<Id>.log and
    # ci-logs\server-test<Id>.log, so every launcher test has its own piece of the logs.
    param([string]$Id)
    foreach ($name in @('desktop.log', 'server.log')) {
        $source = Join-Path $ctx.State $name
        if (-not (Test-Path -LiteralPath $source)) { continue }
        try {
            $share = [System.IO.FileShare]'ReadWrite, Delete'
            $stream = [System.IO.FileStream]::new($source, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, $share)
            try {
                $start = [long]0
                if ($script:LogOffsets.ContainsKey($name)) { $start = [long]$script:LogOffsets[$name] }
                if ($start -gt $stream.Length) { $start = [long]0 }
                $null = $stream.Seek($start, [System.IO.SeekOrigin]::Begin)
                $count = [int]($stream.Length - $start)
                $bytes = [byte[]]::new($count)
                $read = 0
                while ($read -lt $count) {
                    $got = $stream.Read($bytes, $read, $count - $read)
                    if ($got -le 0) { break }
                    $read += $got
                }
                $script:LogOffsets[$name] = $start + $read
                if ($read -gt 0) {
                    if ($read -lt $count) { [System.Array]::Resize([ref]$bytes, $read) }
                    $base = [System.IO.Path]::GetFileNameWithoutExtension($name)
                    $target = Join-Path $ctx.Logs ('{0}-test{1}.log' -f $base, $Id)
                    [System.IO.File]::WriteAllBytes($target, $bytes)
                    Write-Host "      ${name}: $read new bytes saved as ci-logs\$base-test$Id.log; the last lines:"
                    Get-Content -LiteralPath $target -Tail 15 | ForEach-Object { Write-Host "        | $_" }
                }
            }
            finally {
                $stream.Dispose()
            }
        }
        catch {
            Write-Host "      (${name} could not be copied: $($_.Exception.Message))"
        }
    }
}

function Start-AutopilotPlaceholder {
    # The app starts "gridsim autopilot" in the background at its first page load: it would download a season of
    # football data and simulate a week of games on the test machine, for many minutes, in every launcher test.
    # GridSim never runs two of those at once: it skips the start while <data>\logs\autopilot\run.pid names a
    # live python process. So a sleeping python.exe from the build folder (NOT from the installed program) is
    # named there for the length of the smoke test. Nothing in the installed program is changed for this.
    if ($ctx.Placeholder -and -not $ctx.Placeholder.HasExited) { return }
    $proc = Start-Proc -File $ctx.StagePy -Arguments '-c "import time; time.sleep(14400)"' -Hidden
    $ctx.Placeholder = $proc
    $dir = Join-Path $ctx.Data 'logs\autopilot'
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $stamp = [DateTime]::UtcNow.ToString('s') + '+00:00'
    $json = '{"pid": ' + $proc.Id + ', "reserved": true, "started_utc": "' + $stamp + '"}'
    [System.IO.File]::WriteAllText((Join-Path $dir 'run.pid'), $json)
    Write-Host "      (background autopilot kept off for the test: placeholder python pid $($proc.Id))"
}

# ---- the installer ----------------------------------------------------------------------------------------------

function Invoke-Setup {
    # Runs dist\GridSim-Setup.exe with the given switches plus /LOG, waits (with a timeout) and returns
    # ExitCode, Seconds and Log. Never waits for programs the installer starts.
    param([string]$Arguments, [string]$LogName, [int]$TimeoutMin = 20)
    $log = Join-Path $ctx.Logs $LogName
    $all = '{0} /LOG="{1}"' -f $Arguments, $log
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    $proc = Start-Proc -File $ctx.Setup -Arguments $all
    if (-not $proc.WaitForExit($TimeoutMin * 60 * 1000)) {
        Stop-Tree -ProcessId $proc.Id
        throw "expected the installer to finish within $TimeoutMin minutes; found it still running (ended by the test; see ci-logs\$LogName)"
    }
    $proc.WaitForExit()
    return [pscustomobject]@{ ExitCode = $proc.ExitCode; Seconds = [int][math]::Round($clock.Elapsed.TotalSeconds); Log = $log }
}

function Read-Shortcut {
    param([string]$Path)
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($Path)
    return [pscustomobject]@{
        Target    = "$($link.TargetPath)"
        Arguments = "$($link.Arguments)"
        WorkDir   = "$($link.WorkingDirectory)"
        Icon      = "$($link.IconLocation)"
    }
}

function Get-TreeStamp {
    # path -> "size|time of last change" for every file under $Dir.
    param([string]$Dir)
    $map = @{}
    if (-not (Test-Path -LiteralPath $Dir)) { return $map }
    foreach ($file in [System.IO.Directory]::EnumerateFiles($Dir, '*', [System.IO.SearchOption]::AllDirectories)) {
        $info = [System.IO.FileInfo]::new($file)
        $map[$file] = "$($info.Length)|$($info.LastWriteTimeUtc.Ticks)"
    }
    return $map
}

function Compare-TreeStamp {
    # The differences between two Get-TreeStamp results, one text per file.
    param([hashtable]$Before, [hashtable]$After)
    $diff = [System.Collections.Generic.List[string]]::new()
    foreach ($key in $After.Keys) {
        if (-not $Before.ContainsKey($key)) { $diff.Add("new: $key") }
        elseif ($Before[$key] -ne $After[$key]) { $diff.Add("rewritten: $key") }
    }
    foreach ($key in $Before.Keys) {
        if (-not $After.ContainsKey($key)) { $diff.Add("gone: $key") }
    }
    return $diff.ToArray()
}

function Get-WebView2Version {
    # The version of Microsoft's WebView2 runtime, or '' when it is not installed (the same check the launcher makes).
    $client = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    foreach ($key in @("HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$client", "HKCU:\Software\Microsoft\EdgeUpdate\Clients\$client")) {
        if (Test-Path -LiteralPath $key) {
            $item = Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue
            if ($item -and $item.PSObject.Properties['pv']) {
                $pv = "$($item.pv)"
                if ($pv -and $pv -ne '0.0.0.0') { return $pv }
            }
        }
    }
    return ''
}

# ---- scheduled jobs ---------------------------------------------------------------------------------------------

function Test-TaskExists {
    param([string]$TaskName)
    $null = & schtasks.exe /Query /TN $TaskName 2>&1
    return ($LASTEXITCODE -eq 0)
}

function Remove-Task {
    param([string]$TaskName)
    & schtasks.exe /Delete /F /TN $TaskName 2>&1 | Out-Null
}

function Get-TaskFacts {
    # What Windows has stored for a scheduled job, read from "schtasks /Query /XML" (never from translated text).
    # $null when there is no such job.
    param([string]$TaskName)
    $lines = & schtasks.exe /Query /TN $TaskName /XML 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    $text = (@($lines) -join "`n").Trim()
    try {
        $doc = [xml]$text
    }
    catch {
        return [pscustomobject]@{ Xml = $text; ParseError = "$($_.Exception.Message)"; Command = ''; Arguments = ''; WorkingDirectory = ''; LogonType = ''; UserId = ''; Enabled = $false; DisallowStartIfOnBatteries = ''; StopIfGoingOnBatteries = ''; StartWhenAvailable = ''; WakeToRun = ''; ExecutionTimeLimit = '' }
    }
    $exec = $doc.Task.Actions.Exec
    $settings = $doc.Task.Settings
    $principal = $doc.Task.Principals.Principal
    return [pscustomobject]@{
        Xml                        = $text
        ParseError                 = ''
        Command                    = ("$($exec.Command)").Trim().Trim('"')
        Arguments                  = ("$($exec.Arguments)").Trim()
        WorkingDirectory           = ("$($exec.WorkingDirectory)").Trim().Trim('"')
        LogonType                  = "$($principal.LogonType)"
        UserId                     = "$($principal.UserId)"
        Enabled                    = ("$($settings.Enabled)" -ne 'false')
        DisallowStartIfOnBatteries = "$($settings.DisallowStartIfOnBatteries)"
        StopIfGoingOnBatteries     = "$($settings.StopIfGoingOnBatteries)"
        StartWhenAvailable         = "$($settings.StartWhenAvailable)"
        WakeToRun                  = "$($settings.WakeToRun)"
        ExecutionTimeLimit         = "$($settings.ExecutionTimeLimit)"
    }
}

function Get-TaskLastRun {
    # When Windows last started the job, as text ('' when it never ran or there is no such job).
    param([string]$TaskName)
    $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($info -and $info.LastRunTime) { return $info.LastRunTime.ToString('o') }
    return ''
}

function Wait-TaskRun {
    # After "schtasks /Run": waits until the job's "last run" differs from $Before (Get-TaskLastRun, taken before
    # the run) and the job is no longer running. Returns its last result code (0 = it ended normally), or $null
    # on timeout.
    param([string]$TaskName, [string]$Before, [int]$TimeoutSec = 180)
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    while ($clock.Elapsed.TotalSeconds -lt $TimeoutSec) {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task -and $info -and "$($task.State)" -ne 'Running' -and (Get-TaskLastRun -TaskName $TaskName) -ne $Before) {
            Start-Sleep -Milliseconds 500
            $final = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
            # 267009 = the job is running, 267045 = it is waiting to start: not a result yet
            if ($final -and @(267009, 267045) -notcontains [long]$final.LastTaskResult) { return [long]$final.LastTaskResult }
        }
        Start-Sleep -Milliseconds 500
    }
    return $null
}

# ---- small things -----------------------------------------------------------------------------------------------

function Invoke-Quietly {
    # For the clean-up at the end: one step that fails must not stop the others.
    param([string]$What, [scriptblock]$Do)
    try {
        & $Do
    }
    catch {
        Write-Host "clean-up, ${What}: $($_.Exception.Message)"
    }
}

function Save-Text {
    # Writes the output of a program (lines, possibly error records) to a file in ci-logs and returns the lines.
    param([string]$Name, $Lines)
    $texts = @($Lines | ForEach-Object { "$_" })
    Set-Content -LiteralPath (Join-Path $ctx.Logs $Name) -Value $texts -Encoding utf8
    return $texts
}
