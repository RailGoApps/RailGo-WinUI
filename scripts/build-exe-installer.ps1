param(
    [string]$Configuration = "Release",
    [string]$RuntimeIdentifier = "win-x64",
    [string]$Version = "0.1.3",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$projectPath = Join-Path $repoRoot "RailGo\RailGo.csproj"
$publishDir = Join-Path $repoRoot "artifacts\publish\$RuntimeIdentifier"
$installerDir = Join-Path $repoRoot "artifacts\installer"
$prerequisitesDir = Join-Path $repoRoot "artifacts\prerequisites"
$webViewBootstrapper = Join-Path $prerequisitesDir "MicrosoftEdgeWebview2Setup.exe"
$installerScriptPath = Join-Path $repoRoot "installer\RailGo.iss"
$platform = switch ($RuntimeIdentifier) {
    "win-x64" { "x64" }
    "win-x86" { "x86" }
    "win-arm64" { "arm64" }
    default { throw "Unsupported RuntimeIdentifier: $RuntimeIdentifier" }
}

if (-not (Test-Path $projectPath)) {
    throw "Cannot find project file: $projectPath"
}

if (-not (Test-Path $installerScriptPath)) {
    throw "Cannot find installer script: $installerScriptPath"
}

if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw "dotnet SDK is not installed."
}

if (Test-Path $publishDir) {
    Remove-Item -LiteralPath $publishDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $publishDir | Out-Null
New-Item -ItemType Directory -Force -Path $installerDir | Out-Null
New-Item -ItemType Directory -Force -Path $prerequisitesDir | Out-Null

if ($RuntimeIdentifier -eq "win-x64") {
    $runtimePath = Join-Path $repoRoot "RailGPT\RailGPT.Runtime.exe"
    if (-not (Test-Path $runtimePath)) {
        throw "RailGPT.Runtime.exe is missing. Run packaging\build-railgpt-runtime.ps1 first."
    }
}

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
    -p:Platform=$platform `
    -f net8.0-windows10.0.26100.0 `
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

if ((Test-Path $webViewBootstrapper) -and
    (Get-Item $webViewBootstrapper).Length -lt 100KB) {
    Remove-Item -LiteralPath $webViewBootstrapper -Force
}

if (-not (Test-Path $webViewBootstrapper)) {
    Write-Host "Downloading the official Microsoft Edge WebView2 Evergreen bootstrapper..."
    $webViewUri = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & $curl.Source -L --fail --retry 3 --output $webViewBootstrapper $webViewUri
        if ($LASTEXITCODE -ne 0) {
            throw "WebView2 bootstrapper download failed with exit code $LASTEXITCODE."
        }
    }
    else {
        Invoke-WebRequest `
            -UseBasicParsing `
            -TimeoutSec 60 `
            -Uri $webViewUri `
            -OutFile $webViewBootstrapper
    }
}

if (-not (Test-Path $webViewBootstrapper) -or
    (Get-Item $webViewBootstrapper).Length -lt 100KB) {
    throw "The WebView2 bootstrapper is missing or incomplete."
}

$innoRegistry = Get-ItemProperty `
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*" `
    -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -like "Inno Setup*" } |
    Select-Object -First 1

$isccCandidates = @(
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:LocalAppData "Programs\Inno Setup 6\ISCC.exe"),
    $(if ($innoRegistry.InstallLocation) {
        Join-Path $innoRegistry.InstallLocation "ISCC.exe"
    })
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
    "/DMyWebViewBootstrapper=$webViewBootstrapper" `
    $installerScriptPath

Write-Host "Installer output directory: $installerDir"

