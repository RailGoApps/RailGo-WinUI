param(
    [string]$Configuration = "Release",
    [string]$RuntimeIdentifier = "win-x64",
    [string]$Version = "0.1.2",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$projectPath = Join-Path $repoRoot "RailGo\RailGo.csproj"
$publishDir = Join-Path $repoRoot "artifacts\publish\$RuntimeIdentifier"
$installerDir = Join-Path $repoRoot "artifacts\installer"
$installerScriptPath = Join-Path $repoRoot "installer\RailGo.iss"

if (-not (Test-Path $projectPath)) {
    throw "Cannot find project file: $projectPath"
}

if (-not (Test-Path $installerScriptPath)) {
    throw "Cannot find installer script: $installerScriptPath"
}

if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw "dotnet SDK is not installed."
}

New-Item -ItemType Directory -Force -Path $publishDir | Out-Null
New-Item -ItemType Directory -Force -Path $installerDir | Out-Null

# Restore with retries to reduce transient network failures.
$restoreSucceeded = $false
$maxRestoreRetries = 4
for ($attempt = 1; $attempt -le $maxRestoreRetries; $attempt++) {
    Write-Host "dotnet restore attempt $attempt/$maxRestoreRetries..."
    dotnet restore $projectPath --disable-parallel
    if ($LASTEXITCODE -eq 0) {
        $restoreSucceeded = $true
        break
    }

    if ($attempt -lt $maxRestoreRetries) {
        Start-Sleep -Seconds (5 * $attempt)
    }
}

if (-not $restoreSucceeded) {
    throw "dotnet restore failed after multiple attempts. Check network/proxy/VPN and NuGet source."
}

dotnet publish $projectPath `
    -c $Configuration `
    -r $RuntimeIdentifier `
    -f net8.0-windows10.0.22621.0 `
    --self-contained true `
    -p:WindowsPackageType=None `
    -p:WindowsAppSDKSelfContained=true `
    -p:PublishSingleFile=false `
    -p:PublishTrimmed=false `
    --no-restore `
    -o $publishDir

if ($LASTEXITCODE -ne 0) {
    throw "dotnet publish failed. See errors above."
}

if ($SkipInstaller) {
    Write-Host "Publish succeeded. Installer step skipped because -SkipInstaller was provided."
    Write-Host "Publish output directory: $publishDir"
    exit 0
}

$isccCandidates = @(
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:LocalAppData "Programs\Inno Setup 6\ISCC.exe")
) | Where-Object { $_ -and (Test-Path $_) }

$isccPath = $isccCandidates | Select-Object -First 1

if (-not $isccPath) {
    $isccCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($isccCommand) {
        $isccPath = $isccCommand.Source
    }
}

if (-not $isccPath) {
    throw "Inno Setup 6 is not installed. Install with: winget install -e --id JRSoftware.InnoSetup"
}

& $isccPath `
    "/DMyAppVersion=$Version" `
    "/DMyPublishDir=$publishDir" `
    "/DMyOutputDir=$installerDir" `
    $installerScriptPath

Write-Host "Installer output directory: $installerDir"
