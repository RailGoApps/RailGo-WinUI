using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text;
using RailGo.AI.Models;

namespace RailGo.AI.Services;

public sealed class RailGptRuntimeManager : IRailGptRuntimeManager, IDisposable
{
    private const int DefaultPort = 5033;
    private const int HealthCheckIntervalMs = 5000;
    private const int MaxConsecutiveHealthFailures = 3;
    private const int MaxRestartsPerWindow = 2;
    private static readonly TimeSpan RestartWindow = TimeSpan.FromMinutes(5);

    private readonly HttpClient _httpClient = new() { Timeout = TimeSpan.FromSeconds(5) };
    private readonly IRailGoBridgeHost _bridgeHost;
    private readonly IRailGptDiagnostics _diagnostics;
    private readonly SemaphoreSlim _startGate = new(1, 1);
    private readonly object _startupCancellationSync = new();
    private readonly Queue<DateTimeOffset> _restartHistory = new();
    private CancellationTokenSource? _healthCts;
    private CancellationTokenSource? _startupCts;
    private Process? _process;
    private int _actualPort = DefaultPort;
    private bool _ownsProcess;
    private bool _stopping;
    private volatile bool _shutdownRequested;
    private int _recoveryInProgress;
    private bool _disposed;

    public string BaseUrl => $"http://127.0.0.1:{_actualPort}";
    public string RuntimeDescription => Status.RuntimePath;
    public RailGptRuntimeStatus Status { get; private set; }
    public event EventHandler<RailGptRuntimeStatus>? StatusChanged;

    public RailGptRuntimeManager(
        IRailGoBridgeHost bridgeHost,
        IRailGptDiagnostics diagnostics)
    {
        _bridgeHost = bridgeHost;
        _diagnostics = diagnostics;
        Status = CreateStatus(RailGptRuntimeState.Stopped, "RailGPT 尚未启动。");
    }

    public Task<RailGptStartResult> StartAsync(CancellationToken cancellationToken = default)
        => StartCoreAsync(explicitStart: true, cancellationToken);

    private async Task<RailGptStartResult> StartCoreAsync(
        bool explicitStart,
        CancellationToken cancellationToken)
    {
        await _startGate.WaitAsync(cancellationToken);
        CancellationTokenSource? startupCts = null;
        try
        {
            startupCts = BeginStartupOperation(explicitStart, cancellationToken);
            if (startupCts == null)
                return new RailGptStartResult(Status);

            var operationToken = startupCts.Token;

            if (Status.IsReady && await HealthCheckAsync(operationToken))
                return new RailGptStartResult(Status);

            if (RuntimeInformation.ProcessArchitecture != Architecture.X64)
            {
                SetStatus(RailGptRuntimeState.UnsupportedArchitecture,
                    $"RailGPT Runtime 首轮仅支持 x64，当前架构为 {RuntimeInformation.ProcessArchitecture}。");
                return new RailGptStartResult(Status);
            }

            SetStatus(RailGptRuntimeState.Starting, "正在后台启动 RailGPT…");
            _stopping = false;
            CancelHealthChecks();
            if (_ownsProcess || _process != null)
            {
                await StopOwnedProcessAsync(CancellationToken.None);
                _ownsProcess = false;
            }

            try
            {
                await _bridgeHost.StartAsync();
            }
            catch (Exception ex)
            {
                _diagnostics.Error("RailGo bridge failed to start.", ex);
                SetStatus(RailGptRuntimeState.Failed, "RailGo Bridge 启动失败。");
                return new RailGptStartResult(Status);
            }

            var externalPort = await FindEmbeddedCompatiblePortAsync(operationToken);
            if (externalPort is int compatiblePort)
            {
                _actualPort = compatiblePort;
                _ownsProcess = false;
                SetStatus(RailGptRuntimeState.Ready, "已连接兼容的 RailGPT 服务。", "external");
                StartHealthChecks();
                return new RailGptStartResult(Status);
            }

            var runtime = FindRuntime();
            if (runtime == null)
            {
                await _bridgeHost.StopAsync();
                SetStatus(RailGptRuntimeState.MissingRuntime,
                    "未找到 RailGPT.Runtime.exe。请使用 x64 PR 构建产物或重新构建 Runtime。");
                return new RailGptStartResult(Status);
            }

            _actualPort = await FindAvailablePortAsync(DefaultPort, operationToken);
            var startInfo = CreateStartInfo(runtime, _actualPort, _bridgeHost.PipeName);
            _process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
            _ownsProcess = true;
            _process.OutputDataReceived += (_, args) => LogProcessLine(args.Data, false);
            _process.ErrorDataReceived += (_, args) => LogProcessLine(args.Data, true);
            _process.Exited += OnProcessExited;

            try
            {
                if (!_process.Start())
                    throw new InvalidOperationException("Process.Start returned false.");

                _process.BeginOutputReadLine();
                _process.BeginErrorReadLine();
                _diagnostics.Info($"RailGPT process started. PID={_process.Id}; Port={_actualPort}; Runtime={runtime.Executable}");

                if (!await WaitForServerAsync(TimeSpan.FromSeconds(60), operationToken))
                {
                    int? exitCode = _process.HasExited ? _process.ExitCode : null;
                    await StopOwnedProcessAsync(CancellationToken.None);
                    await _bridgeHost.StopAsync();
                    SetStatus(RailGptRuntimeState.Failed,
                        "RailGPT Runtime 在 60 秒内未就绪。", runtime.Executable, exitCode: exitCode);
                    return new RailGptStartResult(Status);
                }

                SetStatus(RailGptRuntimeState.Ready, "RailGPT 已就绪。", runtime.Executable, _process.Id);
                StartHealthChecks();
                return new RailGptStartResult(Status);
            }
            catch (OperationCanceledException)
            {
                // The server may not be accepting HTTP yet. Waiting for the
                // graceful endpoint here can outlive the host's close budget.
                await StopOwnedProcessAsync(CancellationToken.None, graceful: false);
                await _bridgeHost.StopAsync();
                throw;
            }
            catch (Exception ex)
            {
                _diagnostics.Error("RailGPT runtime failed to start.", ex);
                await StopOwnedProcessAsync(CancellationToken.None);
                await _bridgeHost.StopAsync();
                SetStatus(RailGptRuntimeState.Failed, $"RailGPT Runtime 启动失败：{ex.Message}", runtime.Executable);
                return new RailGptStartResult(Status);
            }
        }
        finally
        {
            EndStartupOperation(startupCts);
            _startGate.Release();
        }
    }

    public Task StopAsync(CancellationToken cancellationToken = default)
    {
        RequestShutdown();
        return StopCoreAsync(cancellationToken);
    }

    private async Task StopCoreAsync(CancellationToken cancellationToken)
    {
        await _startGate.WaitAsync(cancellationToken);
        try
        {
            if (Status.State == RailGptRuntimeState.Stopped && !_ownsProcess)
                return;

            _stopping = true;
            SetStatus(RailGptRuntimeState.Stopping, "正在停止 RailGPT…");
            CancelHealthChecks();
            await StopOwnedProcessAsync(cancellationToken);
            _ownsProcess = false;
            await _bridgeHost.StopAsync();
            SetStatus(RailGptRuntimeState.Stopped, "RailGPT 已停止。");
        }
        finally
        {
            _stopping = false;
            _startGate.Release();
        }
    }

    public async Task<bool> HealthCheckAsync(CancellationToken cancellationToken = default)
    {
        try
        {
            using var response = await _httpClient.GetAsync($"{BaseUrl}/api/status", cancellationToken);
            return response.IsSuccessStatusCode;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch
        {
            return false;
        }
    }

    private void StartHealthChecks()
    {
        CancelHealthChecks();
        _healthCts = new CancellationTokenSource();
        _ = ObserveHealthChecksAsync(_healthCts.Token);
    }

    private async Task ObserveHealthChecksAsync(CancellationToken cancellationToken)
    {
        try
        {
            await PeriodicHealthCheckAsync(cancellationToken);
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception ex)
        {
            _diagnostics.Error("RailGPT health monitor failed.", ex);
        }
    }

    private async Task PeriodicHealthCheckAsync(CancellationToken cancellationToken)
    {
        var failures = 0;
        while (!cancellationToken.IsCancellationRequested)
        {
            await Task.Delay(HealthCheckIntervalMs, cancellationToken);
            if (!Status.IsReady)
                return;

            if (await HealthCheckAsync(cancellationToken))
            {
                failures = 0;
                continue;
            }

            failures++;
            _diagnostics.Warning($"RailGPT health check failed ({failures}/{MaxConsecutiveHealthFailures}).");
            if (failures < MaxConsecutiveHealthFailures)
                continue;

            if (!_ownsProcess || !CanRestart())
            {
                SetStatus(RailGptRuntimeState.Failed,
                    _ownsProcess ? "RailGPT 自动重启次数已达上限，请手动重试。" : "外部 RailGPT 服务已断开。");
                return;
            }

            if (Interlocked.CompareExchange(ref _recoveryInProgress, 1, 0) != 0)
                return;

            _restartHistory.Enqueue(DateTimeOffset.UtcNow);
            try
            {
                CancelHealthChecks();
                await StopCoreAsync(CancellationToken.None);
                if (_shutdownRequested)
                    return;
                await Task.Delay(1000, CancellationToken.None);
                await StartCoreAsync(explicitStart: false, CancellationToken.None);
            }
            finally
            {
                Interlocked.Exchange(ref _recoveryInProgress, 0);
            }
            return;
        }
    }

    private bool CanRestart()
    {
        var threshold = DateTimeOffset.UtcNow - RestartWindow;
        while (_restartHistory.Count > 0 && _restartHistory.Peek() < threshold)
            _restartHistory.Dequeue();
        return _restartHistory.Count < MaxRestartsPerWindow;
    }

    private void OnProcessExited(object? sender, EventArgs e)
    {
        if (_disposed || _stopping || _shutdownRequested || !_ownsProcess)
            return;

        var exitCode = TryGetExitCode();
        _diagnostics.Warning($"RailGPT process exited unexpectedly. ExitCode={exitCode?.ToString() ?? "unknown"}");
        CancelHealthChecks();
        SetStatus(RailGptRuntimeState.Exited, "RailGPT Runtime 意外退出，请重试。", processId: null, exitCode: exitCode);
        if (Interlocked.CompareExchange(ref _recoveryInProgress, 1, 0) == 0)
        {
            _ = RecoverAfterUnexpectedExitAsync().ContinueWith(
                _ => Interlocked.Exchange(ref _recoveryInProgress, 0),
                CancellationToken.None,
                TaskContinuationOptions.ExecuteSynchronously,
                TaskScheduler.Default);
        }
    }

    private async Task RecoverAfterUnexpectedExitAsync()
    {
        try
        {
            if (!CanRestart())
            {
                SetStatus(RailGptRuntimeState.Failed,
                    "RailGPT 自动重启次数已达上限，请手动重试。",
                    exitCode: TryGetExitCode());
                return;
            }

            _restartHistory.Enqueue(DateTimeOffset.UtcNow);
            await StopCoreAsync(CancellationToken.None);
            if (_shutdownRequested || _disposed)
                return;
            await Task.Delay(1000);
            if (_shutdownRequested || _disposed)
                return;
            await StartCoreAsync(explicitStart: false, CancellationToken.None);
        }
        catch (Exception ex)
        {
            _diagnostics.Error("RailGPT automatic recovery failed.", ex);
            SetStatus(RailGptRuntimeState.Failed, $"RailGPT 自动恢复失败：{ex.Message}");
        }
    }

    private async Task StopOwnedProcessAsync(
        CancellationToken cancellationToken,
        bool graceful = true)
    {
        if (!_ownsProcess || _process == null)
            return;

        try
        {
            if (graceful && !_process.HasExited)
            {
                using var shutdownCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
                shutdownCts.CancelAfter(TimeSpan.FromSeconds(3));
                try
                {
                    await _httpClient.PostAsync($"{BaseUrl}/api/shutdown", null, shutdownCts.Token);
                    await _process.WaitForExitAsync(shutdownCts.Token);
                }
                catch
                {
                }
            }

            if (!_process.HasExited)
            {
                _process.Kill(entireProcessTree: true);
                await _process.WaitForExitAsync(CancellationToken.None);
            }
        }
        catch (Exception ex)
        {
            _diagnostics.Warning($"Failed to stop RailGPT process cleanly: {ex.Message}");
        }
        finally
        {
            _process.Exited -= OnProcessExited;
            _process.Dispose();
            _process = null;
            _ownsProcess = false;
        }
    }

    private async Task<int?> FindEmbeddedCompatiblePortAsync(CancellationToken cancellationToken)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(1.5));
        var probes = Enumerable.Range(DefaultPort, 8)
            .Select(async port => await IsEmbeddedCompatibleAsync(port, timeout.Token) ? (int?)port : null);
        try
        {
            var results = await Task.WhenAll(probes);
            return results.FirstOrDefault(port => port.HasValue);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            return null;
        }
    }

    private async Task<bool> IsEmbeddedCompatibleAsync(int port, CancellationToken cancellationToken)
    {
        try
        {
            var baseUrl = $"http://127.0.0.1:{port}";
            using var status = await _httpClient.GetAsync($"{baseUrl}/api/status", cancellationToken);
            if (!status.IsSuccessStatusCode)
                return false;

            using var response = await _httpClient.GetAsync($"{baseUrl}/?embedded=1", cancellationToken);
            if (!response.IsSuccessStatusCode)
                return false;
            var html = await response.Content.ReadAsStringAsync(cancellationToken);
            return html.Contains("class=\"embedded\"", StringComparison.OrdinalIgnoreCase) ||
                   html.Contains("class='embedded'", StringComparison.OrdinalIgnoreCase);
        }
        catch
        {
            return false;
        }
    }

    private static RuntimeInfo? FindRuntime()
    {
        var bundledDirectory = Path.Combine(AppContext.BaseDirectory, "RailGPT");
        var bundled = Path.Combine(bundledDirectory, "RailGPT.Runtime.exe");
        if (File.Exists(bundled))
            return new RuntimeInfo(bundledDirectory, bundled, null, false);

#if DEBUG
        var configuredPython = Environment.GetEnvironmentVariable("RAILGPT_DEV_PYTHON");
        if (!string.IsNullOrWhiteSpace(configuredPython) && File.Exists(configuredPython))
        {
            foreach (var directory in FindDevelopmentDirectories())
            {
                var entryPoint = Path.Combine(directory, "server_entry.py");
                if (File.Exists(entryPoint))
                    return new RuntimeInfo(directory, configuredPython, entryPoint, true);
            }
        }
#endif
        return null;
    }

#if DEBUG
    private static IEnumerable<string> FindDevelopmentDirectories()
    {
        var current = new DirectoryInfo(AppContext.BaseDirectory);
        for (var index = 0; index < 8 && current != null; index++, current = current.Parent)
        {
            var candidate = Path.Combine(current.FullName, "RailGPT");
            if (Directory.Exists(candidate))
                yield return candidate;
        }
    }
#endif

    private static ProcessStartInfo CreateStartInfo(RuntimeInfo runtime, int port, string bridgePipeName)
    {
        var info = new ProcessStartInfo
        {
            FileName = runtime.Executable,
            WorkingDirectory = runtime.Directory,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
            CreateNoWindow = true,
        };
        if (runtime.IsPython)
            info.ArgumentList.Add(runtime.EntryPoint!);

        info.Environment["RAILGPT_HOST"] = "127.0.0.1";
        info.Environment["RAILGPT_PORT"] = port.ToString();
        info.Environment["RAILGPT_MODE"] = "server";
        info.Environment["RAILGO_BRIDGE_PIPE"] = bridgePipeName;
        info.Environment["PYTHONIOENCODING"] = "utf-8";
        info.Environment["PYTHONUTF8"] = "1";
        info.Environment["RAILGPT_DATA_ROOT"] = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "RailGPT");
        return info;
    }

    private static async Task<int> FindAvailablePortAsync(int startPort, CancellationToken cancellationToken)
    {
        for (var port = startPort; port < startPort + 100; port++)
        {
            cancellationToken.ThrowIfCancellationRequested();
            try
            {
                var listener = new TcpListener(IPAddress.Loopback, port);
                listener.Start();
                listener.Stop();
                return port;
            }
            catch (SocketException)
            {
                await Task.Yield();
            }
        }

        var fallback = new TcpListener(IPAddress.Loopback, 0);
        fallback.Start();
        var selected = ((IPEndPoint)fallback.LocalEndpoint).Port;
        fallback.Stop();
        return selected;
    }

    private async Task<bool> WaitForServerAsync(TimeSpan timeout, CancellationToken cancellationToken)
    {
        var deadline = DateTimeOffset.UtcNow + timeout;
        while (DateTimeOffset.UtcNow < deadline)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (_process?.HasExited == true)
                return false;
            if (await HealthCheckAsync(cancellationToken))
                return true;
            await Task.Delay(500, cancellationToken);
        }
        return false;
    }

    private void LogProcessLine(string? line, bool error)
    {
        if (string.IsNullOrWhiteSpace(line))
            return;
        if (error) _diagnostics.Warning($"RailGPT: {line}");
        else _diagnostics.Info($"RailGPT: {line}");
    }

    private void SetStatus(
        RailGptRuntimeState state,
        string message,
        string? runtimePath = null,
        int? processId = null,
        int? exitCode = null)
    {
        Status = CreateStatus(state, message, runtimePath, processId, exitCode);
        _diagnostics.Info($"Runtime state={state}; Message={message}; PID={processId}; ExitCode={exitCode}");
        StatusChanged?.Invoke(this, Status);
    }

    private RailGptRuntimeStatus CreateStatus(
        RailGptRuntimeState state,
        string message,
        string? runtimePath = null,
        int? processId = null,
        int? exitCode = null)
        => new(
            state,
            message,
            BaseUrl,
            runtimePath ?? Path.Combine(AppContext.BaseDirectory, "RailGPT", "RailGPT.Runtime.exe"),
            _diagnostics.CurrentLogPath,
            processId,
            exitCode);

    private int? TryGetExitCode()
    {
        try
        {
            return _process?.HasExited == true ? _process.ExitCode : null;
        }
        catch
        {
            return null;
        }
    }

    private CancellationTokenSource? BeginStartupOperation(
        bool explicitStart,
        CancellationToken cancellationToken)
    {
        var startupCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        lock (_startupCancellationSync)
        {
            if (!explicitStart && _shutdownRequested)
            {
                startupCts.Dispose();
                return null;
            }
            if (explicitStart)
                _shutdownRequested = false;

            _startupCts?.Cancel();
            _startupCts?.Dispose();
            _startupCts = startupCts;
        }
        return startupCts;
    }

    private void EndStartupOperation(CancellationTokenSource? startupCts)
    {
        if (startupCts == null)
            return;

        lock (_startupCancellationSync)
        {
            if (ReferenceEquals(_startupCts, startupCts))
                _startupCts = null;
        }
        startupCts.Dispose();
    }

    private void RequestShutdown()
    {
        lock (_startupCancellationSync)
        {
            _shutdownRequested = true;
            _startupCts?.Cancel();
        }
    }

    private void CancelHealthChecks()
    {
        _healthCts?.Cancel();
        _healthCts?.Dispose();
        _healthCts = null;
    }

    public void Dispose()
    {
        if (_disposed)
            return;
        _disposed = true;
        RequestShutdown();
        CancelHealthChecks();
        try
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(4));
            StopAsync(timeout.Token).GetAwaiter().GetResult();
        }
        catch
        {
        }
        _process?.Dispose();
        _httpClient.Dispose();
        _startGate.Dispose();
    }

    private sealed record RuntimeInfo(string Directory, string Executable, string? EntryPoint, bool IsPython);
}
