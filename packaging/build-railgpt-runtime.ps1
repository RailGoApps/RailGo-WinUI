param(
    [string]$Python = "python",
    [string]$DistDir = ""
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

Write-Host "RailGPT runtime created under the PyInstaller dist directory."
