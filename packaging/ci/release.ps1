# Publishes dist\GridSim-Setup.exe as a GitHub Release named after GridSim's version (tag "v" + gridsim.__version__).
#
# The workflow runs this only after build.ps1 and smoke.ps1 both succeeded, and only when the commit message
# contains [release] or "publish" was ticked on the Run workflow form. Running it twice for the same version
# replaces the file of the existing release (so a cancelled or repeated run does no harm).
#
# The file is always named GridSim-Setup.exe, so this link works for every version:
#     https://github.com/<owner>/<repo>/releases/latest/download/GridSim-Setup.exe
# GridSim's update check reads the same release. Never publish a test build with this script: "latest" is offered
# to the user's installed program at its next start.
#
# Needs GH_TOKEN (the workflow passes its own token; "permissions: contents: write").
$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
if ($env:GITHUB_WORKSPACE -and (Test-Path -LiteralPath $env:GITHUB_WORKSPACE)) { $repo = (Resolve-Path -LiteralPath $env:GITHUB_WORKSPACE).Path }
Set-Location -LiteralPath $repo

$setup = Join-Path $repo 'dist\GridSim-Setup.exe'
$py = Join-Path $repo 'dist\GridSim\python\python.exe'
$summary = Join-Path $repo 'ci-logs\summary.txt'
if (-not (Test-Path -LiteralPath $setup)) { throw 'There is no dist\GridSim-Setup.exe to publish.' }
if (-not (Test-Path -LiteralPath $py)) { throw 'There is no dist\GridSim\python\python.exe to read the version from.' }
if (-not $env:GH_TOKEN) { throw 'GH_TOKEN is not set; the release cannot be published.' }

# a second lock on the door: nothing is published unless the smoke test wrote a clean summary
if (-not (Test-Path -LiteralPath $summary)) { throw 'ci-logs\summary.txt is missing: the smoke test did not run, so nothing is published.' }
$notClean = @(Select-String -LiteralPath $summary -Pattern '^(FAIL|BLOCKED)\s')
if ($notClean.Count -gt 0) { throw "The smoke test reported problems ($($notClean.Count) lines in ci-logs\summary.txt), so nothing is published." }

$version = & $py -c "import gridsim; print(gridsim.__version__)"
if ($LASTEXITCODE -ne 0 -or -not $version) { throw 'The version could not be read from the staged program.' }
$version = ("$version").Trim()
$tag = "v$version"
$title = "GridSim $version"
$notes = "GridSim $version for Windows. Download GridSim-Setup.exe and open it; an installed GridSim offers this update by itself."

$repoArgs = @()
if ($env:GITHUB_REPOSITORY) { $repoArgs = @('--repo', $env:GITHUB_REPOSITORY) }
$target = "$env:GITHUB_SHA"

& gh.exe release view $tag @repoArgs *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Release $tag exists already: replacing its GridSim-Setup.exe"
    & gh.exe release upload $tag $setup --clobber @repoArgs
    if ($LASTEXITCODE -ne 0) { throw "gh release upload failed (exit code $LASTEXITCODE)." }
}
else {
    Write-Host "Creating release $tag"
    $createArgs = @('release', 'create', $tag, $setup, '--title', $title, '--notes', $notes, '--latest') + $repoArgs
    if ($target) { $createArgs += @('--target', $target) }
    & gh.exe @createArgs
    if ($LASTEXITCODE -ne 0) { throw "gh release create failed (exit code $LASTEXITCODE)." }
}

# what the user's program will see
$shown = & gh.exe release view $tag @repoArgs --json 'tagName,isDraft,isPrerelease,assets,url'
if ($LASTEXITCODE -ne 0) { throw "The release $tag cannot be read back after publishing." }
$release = (@($shown) -join "`n") | ConvertFrom-Json
$asset = @($release.assets | Where-Object { $_.name -eq 'GridSim-Setup.exe' }) | Select-Object -First 1
if ($null -eq $asset) { throw "The release $tag has no file named GridSim-Setup.exe." }
if ($release.isDraft -or $release.isPrerelease) { throw "The release $tag is a draft or a pre-release; GridSim's update check would not see it." }
$size = (Get-Item -LiteralPath $setup).Length
if ([long]$asset.size -ne $size) { throw "The published GridSim-Setup.exe has $($asset.size) bytes; the built one has $size." }
Write-Host "Published $($release.url)"
Write-Host "  GridSim-Setup.exe, $([math]::Round($size / 1MB, 1)) MB"
if ($env:GITHUB_REPOSITORY) {
    Write-Host "  download link for every version: https://github.com/$($env:GITHUB_REPOSITORY)/releases/latest/download/GridSim-Setup.exe"
}
# (GitHub ends the step with the exit code of the last program that ran: say plainly that this script succeeded)
$global:LASTEXITCODE = 0
exit 0
