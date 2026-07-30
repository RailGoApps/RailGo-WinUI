param(
    [string]$RuntimePath = "",
    [int]$TimeoutSeconds = 75
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($RuntimePath)) {
    $RuntimePath = Join-Path $repoRoot "RailGPT\RailGPT.Runtime.exe"
}
$RuntimePath = (Resolve-Path $RuntimePath).Path

$listener = [System.Net.Sockets.TcpListener]::new(
    [System.Net.IPAddress]::Loopback,
    0)
$listener.Start()
$port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
$listener.Stop()

$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$dataRoot = Join-Path $tempRoot ("RailGPT-smoke-" + [Guid]::NewGuid().ToString("N"))
[System.IO.Directory]::CreateDirectory($dataRoot) | Out-Null
$stdoutPath = Join-Path $dataRoot "stdout.log"
$stderrPath = Join-Path $dataRoot "stderr.log"
$selfTestStdoutPath = Join-Path $dataRoot "self-test-stdout.log"
$selfTestStderrPath = Join-Path $dataRoot "self-test-stderr.log"

$environmentNames = @(
    "RAILGPT_HOST",
    "RAILGPT_PORT",
    "RAILGPT_MODE",
    "RAILGPT_DATA_ROOT",
    "PYTHONIOENCODING",
    "PYTHONUTF8"
)
$previousEnvironment = @{}
foreach ($name in $environmentNames) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}
[Environment]::SetEnvironmentVariable("RAILGPT_HOST", "127.0.0.1", "Process")
[Environment]::SetEnvironmentVariable("RAILGPT_PORT", [string]$port, "Process")
[Environment]::SetEnvironmentVariable("RAILGPT_MODE", "server", "Process")
[Environment]::SetEnvironmentVariable("RAILGPT_DATA_ROOT", $dataRoot, "Process")
[Environment]::SetEnvironmentVariable("PYTHONIOENCODING", "utf-8", "Process")
[Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "Process")

$started = $false
$passed = $false
$process = $null
$selfTestProcess = $null
try {
    $selfTestProcess = Start-Process `
        -FilePath $RuntimePath `
        -ArgumentList "--self-test" `
        -WorkingDirectory (Split-Path $RuntimePath) `
        -RedirectStandardOutput $selfTestStdoutPath `
        -RedirectStandardError $selfTestStderrPath `
        -WindowStyle Hidden `
        -PassThru
    if (-not $selfTestProcess.WaitForExit($TimeoutSeconds * 1000)) {
        $selfTestProcess.Kill()
        $selfTestProcess.WaitForExit()
        throw "RailGPT Runtime self-test timed out after $TimeoutSeconds seconds."
    }
    $selfTestProcess.WaitForExit()
    $selfTestProcess.Refresh()
    $selfTestExitCode = $selfTestProcess.ExitCode
    if ($null -ne $selfTestExitCode -and $selfTestExitCode -ne 0) {
        $selfTestError = if (Test-Path $selfTestStderrPath) {
            Get-Content $selfTestStderrPath -Raw
        } else {
            ""
        }
        throw (
            "RailGPT Runtime self-test exited with code " +
            "$selfTestExitCode.`n$selfTestError"
        )
    }
    $selfTestOutput = Get-Content $selfTestStdoutPath |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Select-Object -Last 1
    try {
        $selfTest = $selfTestOutput | ConvertFrom-Json
    }
    catch {
        throw "RailGPT Runtime self-test returned invalid JSON: $selfTestOutput"
    }
    if ($selfTest.service -ne "RailGPT" -or
        $selfTest.timezone -ne "Asia/Shanghai" -or
        $selfTest.resources -ne "ok" -or
        $selfTest.sentence_transformers -ne "imported" -or
        $selfTest.frozen -ne $true) {
        throw "RailGPT Runtime self-test returned an invalid payload: $selfTestOutput"
    }

    $process = Start-Process `
        -FilePath $RuntimePath `
        -WorkingDirectory (Split-Path $RuntimePath) `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -WindowStyle Hidden `
        -PassThru
    $started = $true

    $baseUrl = "http://127.0.0.1:$port"
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    $ready = $false
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        if ($process.HasExited) {
            $process.WaitForExit()
            $stderr = if (Test-Path $stderrPath) {
                Get-Content $stderrPath -Raw
            } else {
                ""
            }
            throw (
                "RailGPT Runtime exited before readiness with code " +
                "$($process.ExitCode).`n$stderr"
            )
        }
        try {
            $status = Invoke-RestMethod -Uri "$baseUrl/api/status" -TimeoutSec 3
            if ($status.service -eq "RailGPT" -and
                $status.embedded_supported -eq $true -and
                -not [string]::IsNullOrWhiteSpace([string]$status.version)) {
                $ready = $true
                break
            }
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $ready) {
        throw "RailGPT Runtime did not become ready within $TimeoutSeconds seconds."
    }

    try {
        Invoke-RestMethod -Method Post -Uri "$baseUrl/api/shutdown" -TimeoutSec 5 | Out-Null
    }
    catch {
        # The endpoint terminates its own process immediately; on some
        # Windows builds the socket closes before PowerShell receives JSON.
    }
    if (-not $process.WaitForExit(10000)) {
        throw "RailGPT Runtime ignored graceful shutdown."
    }
    $process.WaitForExit()
    $passed = $true
    Write-Host "RailGPT Runtime smoke test passed on port $port."
}
finally {
    if ($started -and $null -ne $process -and -not $process.HasExited) {
        $process.Kill()
        $process.WaitForExit()
    }
    if ($null -ne $process) {
        $process.Dispose()
    }
    if ($null -ne $selfTestProcess) {
        $selfTestProcess.Dispose()
    }
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable(
            $name,
            $previousEnvironment[$name],
            "Process")
    }

    $resolvedDataRoot = [System.IO.Path]::GetFullPath($dataRoot)
    if ($passed -and
        $resolvedDataRoot.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase) -and
        [System.IO.Path]::GetFileName($resolvedDataRoot).StartsWith("RailGPT-smoke-")) {
        Remove-Item -LiteralPath $resolvedDataRoot -Recurse -Force
    }
    elseif (-not $passed) {
        Write-Warning "RailGPT smoke-test diagnostics were preserved at $resolvedDataRoot"
    }
}
