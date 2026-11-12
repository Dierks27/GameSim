# Builds dist\GridSim-Setup.exe on a Windows machine (GitHub Actions, windows-2025):
#
#   1. downloads a pinned, relocatable CPython (python-build-standalone) and checks its SHA-256;
#   2. installs GridSim's libraries into it with pip (wheels only; one pure-Python package from source);
#   3. copies GridSim's own files next to it:  git archive HEAD, with packaging\export-ignore.gitattributes
#      deciding what does not ship (no tests, no packaging scripts, no .bat files);
#   4. writes python313._pth, which makes that Python see nothing but itself and GridSim, wherever it is put;
#   5. compiles every .py to .pyc (so the first start on the user's PC is quick and writes nothing);
#   6. compiles packaging\gridsim.iss with Inno Setup into dist\GridSim-Setup.exe.
#
# Result:  dist\GridSim\{python, program, gridsim.ico}   the staged program (smoke.ps1 tests it)
#          dist\GridSim-Setup.exe                         the installer
#          ci-logs\build-facts.json, installed-packages.txt, compileall.txt, iscc.txt
# Any step that fails stops the build with a message that names the step.
$ErrorActionPreference = 'Stop'

# ---- the pinned Python runtime (change all three together; the SHA-256 is in the release's SHA256SUMS file) --------
$PbsTag = '20261110'
$PbsAsset = 'cpython-3.13.18+20261110-x86_64-pc-windows-msvc-install_only_stripped.tar.gz'
$PbsSha256 = '2e0fed09d48989f64c9305da9ff2d37a41c0d13ab2aa6283405b2b620dfbf805'
# The four lines of python313._pth. sys.path is exactly these folders (relative to the python folder): no
# "import site" line, so no other Python's per-user site-packages, PYTHONPATH or registry entry can get in.
# packaging\smoke_runtime.py checks the same four lines.
$PthLines = @('Lib', 'DLLs', 'Lib\site-packages', '..\program')

$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
if ($env:GITHUB_WORKSPACE -and (Test-Path -LiteralPath $env:GITHUB_WORKSPACE)) { $repo = (Resolve-Path -LiteralPath $env:GITHUB_WORKSPACE).Path }
$temp = $env:TEMP
if ($env:RUNNER_TEMP -and (Test-Path -LiteralPath $env:RUNNER_TEMP)) { $temp = $env:RUNNER_TEMP }
Set-Location -LiteralPath $repo

$dist = Join-Path $repo 'dist'
$root = Join-Path $dist 'GridSim'
$logs = Join-Path $repo 'ci-logs'
$packaging = Join-Path $repo 'packaging'
$py = Join-Path $root 'python\python.exe'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$env:PIP_NO_CACHE_DIR = '1'
$env:PIP_NO_INPUT = '1'
$facts = [ordered]@{ pbs_tag = $PbsTag; pbs_asset = $PbsAsset }

function Step {
    param([string]$Name)
    Write-Host ''
    Write-Host "==== $Name"
}

function Invoke-Native {
    # Runs a program and stops the build, naming the step, when its exit code is not 0.
    param([string]$What, [scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)." }
}

if (Test-Path -LiteralPath $dist) { Remove-Item -LiteralPath $dist -Recurse -Force }
New-Item -ItemType Directory -Force -Path $root | Out-Null
New-Item -ItemType Directory -Force -Path $logs | Out-Null

# ---- 1. the Python runtime -------------------------------------------------------------------------------------------
Step 'Download the Python runtime'
$tgz = Join-Path $temp 'gridsim-python.tar.gz'
# the '+' in the file name must be sent as %2B
$url = "https://github.com/astral-sh/python-build-standalone/releases/download/$PbsTag/" + [uri]::EscapeDataString($PbsAsset)
$clock = [System.Diagnostics.Stopwatch]::StartNew()
Invoke-Native 'Downloading the Python runtime' { & curl.exe -fsSL --retry 5 --retry-all-errors --connect-timeout 30 --max-time 600 -o $tgz $url }
$hash = (Get-FileHash -LiteralPath $tgz -Algorithm SHA256).Hash.ToLowerInvariant()
if ($hash -ne $PbsSha256) { throw "The Python runtime download has the wrong checksum: expected $PbsSha256, found $hash." }
# (smoke test 15a downloads this same file once more, through GridSim's own update code, and needs these three)
$facts.pbs_url = $url
$facts.pbs_bytes = (Get-Item -LiteralPath $tgz).Length
$facts.pbs_sha256 = $PbsSha256
# Windows' own tar (Git's tar on the PATH reads "C:\..." as a remote host)
Invoke-Native 'Unpacking the Python runtime' { & (Join-Path $env:SystemRoot 'System32\tar.exe') -xzf $tgz -C $root }
if (-not (Test-Path -LiteralPath $py)) { throw "The Python runtime did not unpack to $py." }
foreach ($name in @('pythonw.exe', 'python313.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'LICENSE.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path $root "python\$name"))) { throw "The Python runtime has no $name." }
}
Invoke-Native 'Starting the unpacked Python' { & $py -I -c "import sys; print('Python', sys.version)" }
$facts.seconds_runtime = [int]$clock.Elapsed.TotalSeconds

# ---- 2. the libraries --------------------------------------------------------------------------------------------------
Step 'Install the libraries'
$clock = [System.Diagnostics.Stopwatch]::StartNew()
$req = Join-Path $temp 'gridsim-requirements.txt'
$deps = & $py -I (Join-Path $packaging 'print_deps.py')
if ($LASTEXITCODE -ne 0 -or -not $deps) { throw 'packaging\print_deps.py could not read the dependency list from pyproject.toml.' }
Set-Content -LiteralPath $req -Value $deps -Encoding ascii
Write-Host "from pyproject.toml: $(@($deps) -join ', ')"
# Wheels only, so a missing wheel stops the build at once instead of compiling on this machine. The one exception:
# proxy_tools (needed by pywebview) exists only as a source package; it is pure Python.
# --no-compile: the .pyc files are written in step 5, in the form that stays valid after the install.
$pipArgs = @('-I', '-m', 'pip', 'install', '--only-binary=:all:', '--no-binary=proxy_tools', '--no-compile', '--no-warn-script-location', '-r', $req, '-r', (Join-Path $packaging 'requirements-win.txt'))
$constraints = Join-Path $packaging 'constraints-win.txt'
if (Test-Path -LiteralPath $constraints) {
    $pipArgs += @('-c', $constraints)
    Write-Host 'versions pinned by packaging\constraints-win.txt'
}
else {
    Write-Host 'no packaging\constraints-win.txt yet: pip takes the newest versions (commit ci-logs\installed-packages.txt under that name to pin them)'
}
Invoke-Native 'Installing the libraries (pip install)' { & $py @pipArgs }
Invoke-Native 'Checking the libraries (pip check)' { & $py -I -m pip check }
$frozen = & $py -I -m pip freeze
if ($LASTEXITCODE -ne 0) { throw 'pip freeze failed.' }
Set-Content -LiteralPath (Join-Path $root 'python\installed-packages.txt') -Value $frozen -Encoding ascii
Set-Content -LiteralPath (Join-Path $logs 'installed-packages.txt') -Value $frozen -Encoding ascii
$facts.packages = @($frozen).Count
$facts.seconds_pip = [int]$clock.Elapsed.TotalSeconds

# ---- 3. GridSim's own files --------------------------------------------------------------------------------------------
Step 'Stage the program files'
$zip = Join-Path $temp 'gridsim-src.zip'
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
# "git archive" leaves out what packaging\export-ignore.gitattributes marks (tests, these scripts, the .bat files).
# The rules are handed to git as <.git>\info\attributes for the length of this one command; they are not in the
# repository's .gitattributes because GitHub's "Download ZIP" would then leave the same files out.
$infoAttributes = ("$(& git rev-parse --git-path info/attributes)").Trim()
if ($LASTEXITCODE -ne 0 -or -not $infoAttributes) { throw 'git could not say where its info\attributes file is.' }
if (-not [System.IO.Path]::IsPathRooted($infoAttributes)) { $infoAttributes = Join-Path $repo $infoAttributes }
$infoBefore = $null
if (Test-Path -LiteralPath $infoAttributes) { $infoBefore = [System.IO.File]::ReadAllText($infoAttributes) }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $infoAttributes) | Out-Null
try {
    Copy-Item -LiteralPath (Join-Path $packaging 'export-ignore.gitattributes') -Destination $infoAttributes -Force
    # no line-ending conversion: the files ship exactly as they are in the repository
    Invoke-Native 'Exporting the program files (git archive)' { & git -c core.autocrlf=false -c core.eol=lf archive --format=zip -o $zip HEAD }
}
finally {
    if ($null -eq $infoBefore) { Remove-Item -LiteralPath $infoAttributes -Force -ErrorAction SilentlyContinue }
    else { [System.IO.File]::WriteAllText($infoAttributes, $infoBefore) }
}
$program = Join-Path $root 'program'
Expand-Archive -LiteralPath $zip -DestinationPath $program -Force
foreach ($rel in @('gridsim\__init__.py', 'gridsim\installed.py', 'app\streamlit_app.py', 'config.yaml', 'regime_changes.yaml', '.streamlit\config.toml', 'README.md', 'GETTING_STARTED.md')) {
    if (-not (Test-Path -LiteralPath (Join-Path $program $rel))) { throw "The staged program has no $rel (check packaging\export-ignore.gitattributes)." }
}
foreach ($rel in @('tests', 'packaging', '.github', 'DECISIONS.md', 'Start GridSim.bat')) {
    if (Test-Path -LiteralPath (Join-Path $program $rel)) { throw "The staged program contains $rel, which must not ship (check packaging\export-ignore.gitattributes)." }
}
Copy-Item -LiteralPath (Join-Path $packaging 'gridsim.ico') -Destination $root

# ---- 4. make the Python private ------------------------------------------------------------------------------------------
Step 'Make the runtime self-contained'
# pip's .exe launchers hold this machine's path to python.exe; everything is started as "python -m ..." instead
$scripts = Join-Path $root 'python\Scripts'
if (Test-Path -LiteralPath $scripts) { Remove-Item -LiteralPath $scripts -Recurse -Force }
foreach ($rel in @('python\share\jupyter', 'python\etc\jupyter')) {
    $extra = Join-Path $root $rel
    if (Test-Path -LiteralPath $extra) { Remove-Item -LiteralPath $extra -Recurse -Force }
}
# .pth files are read by "site", which this Python does not import: a library that needs one would not work
$pthFiles = @(Get-ChildItem -LiteralPath (Join-Path $root 'python\Lib\site-packages') -Filter '*.pth' -File | Where-Object { $_.Name -ne 'distutils-precedence.pth' })
if ($pthFiles.Count -gt 0) {
    throw "These libraries install a .pth file, which the private Python does not read: $(@($pthFiles | ForEach-Object { $_.Name }) -join ', '). Add what they need to python313._pth in build.ps1 (and smoke_runtime.py) first."
}
# the name must be python313._pth (after the DLL): python._pth would not apply to pythonw.exe. ASCII, no BOM.
Set-Content -LiteralPath (Join-Path $root 'python\python313._pth') -Value $PthLines -Encoding ascii
$version = & $py -c "import gridsim; print(gridsim.__version__)"
if ($LASTEXITCODE -ne 0 -or -not $version) { throw 'The staged Python cannot import gridsim from ..\program (python313._pth).' }
$version = ("$version").Trim()
if ($version -notmatch '^\d+(\.\d+){1,3}$') { throw "gridsim.__version__ is '$version'; the installer needs numbers separated by dots." }
Write-Host "GridSim $version"
$facts.version = $version

# ---- 5. compile to .pyc ------------------------------------------------------------------------------------------------
Step 'Compile .pyc files'
$clock = [System.Diagnostics.Stopwatch]::StartNew()
# "checked-hash": valid whatever the installer does to file times (a .pyc that goes by the file time would be judged
# stale on the user's PC and written again at every first import). -f: also replace any .pyc that is already there;
# without it compileall keeps an existing time-based one. Some libraries ship files that are not valid Python 3
# (templates, old test data): those are skipped, so the exit code is not a failure.
$compileLog = Join-Path $logs 'compileall.txt'
& $py -m compileall -q -f -j 0 --invalidation-mode checked-hash (Join-Path $root 'python\Lib') $program *> $compileLog
Write-Host "compileall exit code $LASTEXITCODE (files that are not valid Python 3 are skipped; see ci-logs\compileall.txt)"
$global:LASTEXITCODE = 0
$facts.seconds_compileall = [int]$clock.Elapsed.TotalSeconds
foreach ($rel in @('python\Lib\__pycache__\os.cpython-313.pyc', 'python\Lib\site-packages\numpy\__pycache__\__init__.cpython-313.pyc', 'program\gridsim\__pycache__\__init__.cpython-313.pyc')) {
    $pyc = Join-Path $root $rel
    if (-not (Test-Path -LiteralPath $pyc)) { throw "compileall wrote no $rel." }
    # byte 4 of a .pyc: 0 = goes by the file time, 3 = goes by a hash of the source and checks it
    $flags = [System.IO.File]::ReadAllBytes($pyc)[4]
    if ($flags -ne 3) { throw "$rel is not a 'checked-hash' .pyc (flags $flags): the installed program would compile it again." }
}

# ---- the staged tree: size and path length ---------------------------------------------------------------------------------
$staged = @(Get-ChildItem -LiteralPath $root -Recurse -File -Force)
$bytes = ($staged | Measure-Object -Property Length -Sum).Sum
$facts.staged_files = $staged.Count
$facts.staged_mb = [int]($bytes / 1MB)
# Inno Setup 6 cannot write paths longer than 260 characters. Installed under
# C:\Users\<name>\AppData\Local\Programs\GridSim (40 characters plus the name), 190 leaves room for a 20-letter name.
$longest = $staged | Sort-Object -Property { $_.FullName.Length } -Descending | Select-Object -First 1
$facts.longest_relative_path = $longest.FullName.Length - $root.Length
Write-Host "staged: $($staged.Count) files, $($facts.staged_mb) MB; longest path inside the program: $($facts.longest_relative_path) characters"
$tooLong = @($staged | Where-Object { $_.FullName.Length - $root.Length -gt 190 })
if ($tooLong.Count -gt 0) {
    $tooLong | Select-Object -First 5 | ForEach-Object { Write-Host "  too long: $($_.FullName.Substring($root.Length))" }
    throw "$($tooLong.Count) files have a path longer than 190 characters inside the program; Windows could not install them."
}

# ---- 6. the installer --------------------------------------------------------------------------------------------------
Step 'Compile the installer (Inno Setup)'
$iscc = ''
$found = Get-Command -Name iscc -ErrorAction SilentlyContinue
if ($found) { $iscc = $found.Source }
$default = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'
if (-not $iscc -and (Test-Path -LiteralPath $default)) { $iscc = $default }
if (-not $iscc) {
    Write-Host 'Inno Setup is not on this machine; installing it with choco'
    Invoke-Native 'Installing Inno Setup (choco install innosetup)' { & choco.exe install innosetup --version=6.7.1 -y --no-progress }
    if (Test-Path -LiteralPath $default) { $iscc = $default }
}
if (-not $iscc) { throw 'Inno Setup (ISCC.exe) was not found and could not be installed.' }
Write-Host "ISCC: $iscc"
$facts.iscc = $iscc
$issFile = Join-Path $packaging 'gridsim.iss'
$isccLog = Join-Path $logs 'iscc.txt'
$clock = [System.Diagnostics.Stopwatch]::StartNew()
# /Q: only warnings and errors (with /Qp the log gets one line for each of some 25,000 files)
$isccArgs = @('/Q', "/DAppVersion=$version", "/DStageDir=$root", $issFile)
& $iscc @isccArgs 2>&1 | Tee-Object -FilePath $isccLog
$code = $LASTEXITCODE
if ($code -ne 0 -and (Select-String -LiteralPath $isccLog -Pattern 'RedirectionGuard' -SimpleMatch -Quiet)) {
    Write-Host 'This Inno Setup does not know the RedirectionGuard directive; compiling again without it'
    $isccArgs = @('/Q', "/DAppVersion=$version", "/DStageDir=$root", '/DNoRedirectionGuard=1', $issFile)
    & $iscc @isccArgs 2>&1 | Tee-Object -FilePath $isccLog
    $code = $LASTEXITCODE
    $facts.redirection_guard_directive = 'not known to this compiler; left out'
}
if ($code -ne 0) { throw "Inno Setup could not compile packaging\gridsim.iss (ISCC exit code $code); its messages are above and in ci-logs\iscc.txt." }
$setup = Join-Path $dist 'GridSim-Setup.exe'
if (-not (Test-Path -LiteralPath $setup)) { throw "Inno Setup ended without an error but $setup is not there." }
$facts.seconds_iscc = [int]$clock.Elapsed.TotalSeconds
$facts.installer_mb = [math]::Round((Get-Item -LiteralPath $setup).Length / 1MB, 1)
$facts.installer_sha256 = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLowerInvariant()

Set-Content -LiteralPath (Join-Path $logs 'build-facts.json') -Value ($facts | ConvertTo-Json) -Encoding utf8
Write-Host ''
Write-Host "Built $setup"
Write-Host "  GridSim $version, $($facts.installer_mb) MB, sha256 $($facts.installer_sha256)"
Write-Host "  runtime $($facts.seconds_runtime) s, libraries $($facts.seconds_pip) s, .pyc $($facts.seconds_compileall) s, installer $($facts.seconds_iscc) s"
# (GitHub ends the step with the exit code of the last program that ran: say plainly that this script succeeded)
$global:LASTEXITCODE = 0
exit 0
