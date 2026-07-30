param(
    [Parameter(Mandatory = $true)]
    [string]$MsixPath,
    [string]$ExpectedVersion = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$MsixPath = (Resolve-Path $MsixPath).Path
$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$extractRoot = Join-Path $tempRoot ("RailGo-msix-" + [Guid]::NewGuid().ToString("N"))
[System.IO.Directory]::CreateDirectory($extractRoot) | Out-Null
$runtimePath = Join-Path $extractRoot "RailGPT.Runtime.exe"
$passed = $false

try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = $null
    $archive = [System.IO.Compression.ZipFile]::OpenRead($MsixPath)
    try {
        $manifestEntry = $archive.Entries |
            Where-Object {
                $_.FullName.Replace('\', '/') -eq "AppxManifest.xml"
            } |
            Select-Object -First 1
        if (-not $manifestEntry) {
            throw "AppxManifest.xml is missing from $MsixPath"
        }

        $manifestStream = $manifestEntry.Open()
        $manifestReader = [System.IO.StreamReader]::new(
            $manifestStream,
            [System.Text.Encoding]::UTF8,
            $true)
        try {
            [xml]$manifest = $manifestReader.ReadToEnd()
        }
        finally {
            $manifestReader.Dispose()
            $manifestStream.Dispose()
        }

        $packageVersion = [string]$manifest.Package.Identity.Version
        if (-not [string]::IsNullOrWhiteSpace($ExpectedVersion) -and
            $packageVersion -ne $ExpectedVersion) {
            throw (
                "MSIX version mismatch. Expected $ExpectedVersion, " +
                "found $packageVersion."
            )
        }

        $runtimeEntry = $archive.Entries |
            Where-Object {
                $_.FullName.Replace('\', '/') -eq
                    "RailGPT/RailGPT.Runtime.exe"
            } |
            Select-Object -First 1
        if (-not $runtimeEntry -or $runtimeEntry.Length -eq 0) {
            throw "RailGPT.Runtime.exe is missing from $MsixPath"
        }

        [System.IO.Compression.ZipFileExtensions]::ExtractToFile(
            $runtimeEntry,
            $runtimePath,
            $true)
    }
    finally {
        if ($null -ne $archive) {
            $archive.Dispose()
        }
    }

    & (Join-Path $PSScriptRoot "test-railgpt-runtime.ps1") `
        -RuntimePath $runtimePath

    $passed = $true
    Write-Host (
        "RailGo MSIX validation passed: " +
        "$(Split-Path $MsixPath -Leaf)"
    )
}
finally {
    $resolvedExtractRoot = [System.IO.Path]::GetFullPath($extractRoot)
    if ($passed -and
        $resolvedExtractRoot.StartsWith(
            $tempRoot,
            [System.StringComparison]::OrdinalIgnoreCase) -and
        [System.IO.Path]::GetFileName($resolvedExtractRoot).StartsWith(
            "RailGo-msix-")) {
        Remove-Item -LiteralPath $resolvedExtractRoot -Recurse -Force
    }
    elseif (-not $passed) {
        Write-Warning "MSIX diagnostics were preserved at $resolvedExtractRoot"
    }
}
