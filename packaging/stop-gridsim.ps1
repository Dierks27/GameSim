# Ends every process that runs from GridSim's private Python: <Dir>\python\*.
# That covers the launcher (pythonw.exe), the hidden server (python.exe), scheduled jobs and anything they started.
#
# The installer runs this before it replaces any file (so a file in use cannot leave a half-updated program), and
# the uninstaller runs it before it removes the files. It never touches the uninstaller itself or anything outside
# <Dir>\python, and it never touches the data folder.
#
#   powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File stop-gridsim.ps1 -Dir "<install folder>"
#
# Exit code 0: nothing is running from there (any more).  Exit code 3: something is still running after 10 seconds.
# (3 and not 1 on purpose: 1 is what PowerShell itself ends with when it could not run this script at all, and the
# installer must not take that for "GridSim is still running".)
# Written for Windows PowerShell 5.1 (32- or 64-bit), the one that is part of every Windows 10 and 11.
param([Parameter(Mandatory = $true)][string]$Dir)

$prefix = $Dir.TrimEnd('\') + '\python\'
if ($prefix.Length -lt 12) { exit 0 }          # not a real install folder: do nothing rather than guess

for ($i = 0; $i -lt 20; $i++) {
    $procs = @(Get-CimInstance -ClassName Win32_Process | Where-Object {
            $_.ExecutablePath -and
            $_.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase) -and
            $_.Name -notlike 'unins*' -and
            $_.ProcessId -ne $PID
        })
    if ($procs.Count -eq 0) { exit 0 }
    foreach ($p in $procs) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 500
}
exit 3
