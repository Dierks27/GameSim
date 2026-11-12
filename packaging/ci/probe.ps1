# Test 00 of the windows-installer workflow: facts about the machine the tests run on.
# Informational only: every line is "label: value", a line that cannot be read says so, and the script always
# ends with exit code 0. The output goes to the job log and to ci-logs\probe.txt.
$ErrorActionPreference = 'Continue'

$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$logs = Join-Path $repo 'ci-logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$out = Join-Path $logs 'probe.txt'
Set-Content -LiteralPath $out -Value "probe $(Get-Date -Format o)" -Encoding utf8

function Show {
    param([string]$Label, [scriptblock]$Get)
    try {
        $value = & $Get
        if ($null -eq $value -or "$value" -eq '') { $value = '(nothing)' }
    }
    catch {
        $value = "(could not be read: $($_.Exception.Message))"
    }
    $text = ($value | Out-String).Trim()
    $line = "${Label}: $text"
    Write-Host $line
    Add-Content -LiteralPath $out -Value $line -Encoding utf8
}

function Get-RegistryValue {
    param([string]$Path, [string]$Name)
    if (-not (Test-Path -LiteralPath $Path)) { return '(key not present)' }
    $item = Get-ItemProperty -LiteralPath $Path -ErrorAction Stop
    $prop = $item.PSObject.Properties[$Name]
    if ($null -eq $prop) { return '(value not present)' }
    return $prop.Value
}

$webview2 = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'

Show 'Windows' { (Get-CimInstance -ClassName Win32_OperatingSystem | ForEach-Object { "$($_.Caption) $($_.Version) build $($_.BuildNumber)" }) }
Show 'PowerShell' { $PSVersionTable.PSVersion.ToString() }
Show 'User' { "$env:USERNAME (profile $env:USERPROFILE)" }
Show 'Administrator' { ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) }
Show 'Session id' { (Get-Process -Id $PID).SessionId }
Show 'UserInteractive' { [Environment]::UserInteractive }
Show 'explorer.exe running' { @(Get-Process -Name explorer -ErrorAction SilentlyContinue).Count -gt 0 }
Show 'Screen' {
    Add-Type -AssemblyName System.Windows.Forms
    $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    "$($b.Width) x $($b.Height)"
}
Show 'Time zone' { (Get-TimeZone).Id }
Show 'Processors' { [Environment]::ProcessorCount }
Show 'Memory (GB)' { [math]::Round((Get-CimInstance -ClassName Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1) }
Show 'Free disk space (GB)' { Get-PSDrive -PSProvider FileSystem | ForEach-Object { "$($_.Name): $([math]::Round($_.Free / 1GB, 1))" } }
Show 'TEMP' { $env:TEMP }
Show 'RUNNER_TEMP' { $env:RUNNER_TEMP }
Show 'LOCALAPPDATA' { $env:LOCALAPPDATA }
Show 'Defender real-time protection' { (Get-MpComputerStatus -ErrorAction Stop).RealTimeProtectionEnabled }
Show 'Long paths enabled' { Get-RegistryValue 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' 'LongPathsEnabled' }

Show 'iscc on PATH' { (Get-Command -Name iscc -ErrorAction Stop).Source }
Show 'where.exe iscc' { & where.exe iscc 2>&1 }
Show 'Inno Setup 6 folder' {
    $p = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'
    if (Test-Path -LiteralPath $p) { "$p  (version $((Get-Item -LiteralPath $p).VersionInfo.ProductVersion))" } else { "$p is not there" }
}
Show 'choco' { (& choco --version 2>&1 | Select-Object -First 1) }
Show 'gh' { (& gh --version 2>&1 | Select-Object -First 1) }
Show 'git' { (& git --version 2>&1 | Select-Object -First 1) }
Show 'curl.exe' { (& curl.exe --version 2>&1 | Select-Object -First 1) }
Show 'System32 tar.exe' { (& (Join-Path $env:SystemRoot 'System32\tar.exe') --version 2>&1 | Select-Object -First 1) }
Show 'python on PATH (not used by the build)' { (Get-Command -Name python -ErrorAction Stop).Source }

Show 'Edge (App Paths, HKLM)' { Get-RegistryValue 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe' '(default)' }
Show 'Edge (App Paths, HKCU)' { Get-RegistryValue 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe' '(default)' }
Show 'Edge version' {
    $found = foreach ($base in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, $env:LOCALAPPDATA)) {
        if ($base) {
            $p = Join-Path $base 'Microsoft\Edge\Application\msedge.exe'
            if (Test-Path -LiteralPath $p) { "$p  (version $((Get-Item -LiteralPath $p).VersionInfo.ProductVersion))" }
        }
    }
    if ($found) { $found } else { 'msedge.exe not found in the standard folders' }
}
Show 'Chrome (App Paths, HKLM)' { Get-RegistryValue 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe' '(default)' }
Show 'WebView2 runtime pv (HKLM WOW6432Node)' { Get-RegistryValue "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$webview2" 'pv' }
Show 'WebView2 runtime pv (HKCU)' { Get-RegistryValue "HKCU:\Software\Microsoft\EdgeUpdate\Clients\$webview2" 'pv' }
Show 'WebView2 folder' {
    $p = Join-Path ${env:ProgramFiles(x86)} 'Microsoft\EdgeWebView\Application'
    if (Test-Path -LiteralPath $p) { (Get-ChildItem -LiteralPath $p -Directory | ForEach-Object { $_.Name }) -join ', ' } else { "$p is not there" }
}
Show '.NET Framework release' { Get-RegistryValue 'HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full' 'Release' }
Show 'GridSim scheduled tasks already present' {
    $t = @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -like 'GridSim*' } | ForEach-Object { $_.TaskName })
    if ($t.Count) { $t -join ', ' } else { 'none' }
}
Show 'Port 8501 in use' { @(Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue).Count -gt 0 }

Write-Host "probe written to $out"
exit 0
