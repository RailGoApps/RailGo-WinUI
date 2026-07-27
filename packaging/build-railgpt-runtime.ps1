param(
    [string]$Python = "python",
    [string]$DistDir = "",
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$railGptRoot = Join-Path $repoRoot "RailGPT"
$spec = Join-Path $railGptRoot "RailGPT.Runtime.spec"
$outputDir = if ([string]::IsNullOrWhiteSpace($DistDir)) { $railGptRoot } else { $DistDir }

if (-not (Test-Path $spec)) { throw "RailGPT runtime spec not found: $spec" }
if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) { throw "Python was not found: $Python" }

& $Python -m PyInstaller --clean --noconfirm --distpath $outputDir --workpath (Join-Path $repoRoot "artifacts\railgpt-build") $spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

$runtimePath = Join-Path $outputDir "RailGPT.Runtime.exe"
if (-not (Test-Path $runtimePath)) {
    throw "PyInstaller completed without producing $runtimePath"
}

if (-not $SkipSmokeTest) {
    & (Join-Path $PSScriptRoot "test-railgpt-runtime.ps1") -RuntimePath $runtimePath
    if ($LASTEXITCODE -ne 0) { throw "RailGPT Runtime smoke test failed." }
}

Write-Host "RailGPT Runtime created and verified: $runtimePath"
