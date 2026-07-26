using System.Diagnostics;
using System.Net;
using System.Net.Http;
using System.Net.Sockets;
using Microsoft.Extensions.Logging;

namespace RailGo.AI.Services;

/// <summary>
/// Owns the local RailGPT server started for the WinUI host.
/// The published application uses RailGPT.Runtime.exe; source checkouts may
/// fall back to a local Python interpreter for development.
/// </summary>
public sealed class PythonProcessManager : IPythonProcessManager, IDisposable
{
    private const int DefaultPort = 5033;
    private const int MaxPortTries = 100;
    private const int MaxRestartAttempts = 3;
    private const int HealthCheckIntervalMs = 5000;

    private readonly HttpClient _httpClient;
    private readonly IRailGoBridgeHost _bridgeHost;
    private readonly ILogger<PythonProcessManager>? _logger;
    private CancellationTokenSource? _healthCts;
    private Process? _process;
    private int _actualPort = DefaultPort;
    private int _restartCount;
    private bool _ownsProcess;
    private bool _disposed;

    public PythonProcessManager(
        HttpClient httpClient,
        IRailGoBridgeHost bridgeHost,
        ILogger<PythonProcessManager>? logger = null)
    {
        _httpClient = httpClient;
        _bridgeHost = bridgeHost;
        _httpClient.Timeout = TimeSpan.FromSeconds(5);
        _logger = logger;
    }

    public string BaseUrl => $"http://127.0.0.1:{_actualPort}";
    public string RuntimeDescription => FindRailGptDirectories().FirstOrDefault() ?? "RailGPT";
    public bool IsRunning { get; private set; }
    public event EventHandler<bool>? StatusChanged;

    public async Task StartAsync()
    {
        if (IsRunning)
            return;

        await _bridgeHost.StartAsync();
        var compatiblePort = await FindEmbeddedCompatiblePortAsync();
        if (compatiblePort is int externalPort)
        {
            // This is an external service. Never stop it from this host.
            _actualPort = externalPort;
            _ownsProcess = false;
            SetRunning(true);
            StartHealthChecks();
            return;
        }

        _actualPort = DefaultPort;
        if (await HealthCheckAsync())
            _logger?.LogInformation("Existing RailGPT service on port {Port} does not support embedded mode; starting the in-repository runtime on another port.", _actualPort);

        var runtime = FindRuntime();
        if (runtime == null)
        {
            _logger?.LogError("RailGPT runtime was not found. Expected RailGPT.Runtime.exe or server_entry.py under the app directory.");
            return;
        }

        _actualPort = await FindAvailablePortAsync(DefaultPort);
        var startInfo = CreateStartInfo(runtime, _actualPort, _bridgeHost.PipeName);
        _process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
        _ownsProcess = true;
        _process.OutputDataReceived += (_, e) => LogProcessLine(e.Data, false);
        _process.ErrorDataReceived += (_, e) => LogProcessLine(e.Data, true);
        _process.Exited += OnProcessExited;

        try
        {
            if (!_process.Start())
            {
                _logger?.LogError("RailGPT runtime process could not be started.");
                return;
            }

            _process.BeginOutputReadLine();
            _process.BeginErrorReadLine();

            if (!await WaitForServerAsync(TimeSpan.FromSeconds(60)))
            {
                _logger?.LogError("RailGPT runtime did not become healthy on port {Port}.", _actualPort);
                await StopAsync();
                return;
            }

            _restartCount = 0;
            SetRunning(true);
            StartHealthChecks();
        }
        catch (Exception ex)
        {
            _logger?.LogError(ex, "Failed to start RailGPT runtime.");
            await StopAsync();
        }
    }

    public async Task StopAsync()
    {
        _healthCts?.Cancel();
        _healthCts?.Dispose();
        _healthCts = null;

        // Only terminate a process created by this manager. An existing
        // service discovered on port 5033 belongs to somebody else.
        if (_ownsProcess && _process is { HasExited: false })
        {
            try
            {
                await _httpClient.PostAsync($"{BaseUrl}/api/shutdown", null);
            }
            catch { }

            try
            {
                if (!_process.HasExited)
                {
                    _process.Kill(entireProcessTree: true);
                    await _process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(5));
                }
            }
            catch { }
        }

        SetRunning(false);
        _ownsProcess = false;
        await _bridgeHost.StopAsync();
    }

    public async Task<bool> HealthCheckAsync()
    {
        try
        {
            using var response = await _httpClient.GetAsync($"{BaseUrl}/api/status");
            return response.IsSuccessStatusCode;
        }
        catch { return false; }
    }

    private async Task<bool> IsEmbeddedCompatibleAsync()
        => await IsEmbeddedCompatibleAsync(_actualPort, CancellationToken.None);

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
        catch { return false; }
    }

    private async Task<int?> FindEmbeddedCompatiblePortAsync()
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(1.5));
        var probes = Enumerable.Range(DefaultPort, 8)
            .Select(async port => await IsEmbeddedCompatibleAsync(port, timeout.Token) ? (int?)port : null);
        var results = await Task.WhenAll(probes);
        return results.FirstOrDefault(port => port.HasValue);
    }

    private void StartHealthChecks()
    {
        _healthCts?.Cancel();
        _healthCts?.Dispose();
        _healthCts = new CancellationTokenSource();
        _ = PeriodicHealthCheckAsync(_healthCts.Token);
    }

    private async Task PeriodicHealthCheckAsync(CancellationToken ct)
    {
        try
        {
            while (!ct.IsCancellationRequested)
            {
                await Task.Delay(HealthCheckIntervalMs, ct);
                if (!IsRunning || ct.IsCancellationRequested)
                    break;

                if (!await HealthCheckAsync())
                    await TryRestartAsync();
            }
        }
        catch (OperationCanceledException) { }
    }

    private async Task TryRestartAsync()
    {
        if (_restartCount >= MaxRestartAttempts)
        {
            _logger?.LogError("RailGPT restart limit reached.");
            SetRunning(false);
            return;
        }

        _restartCount++;
        await StopAsync();
        await Task.Delay(1000);
        await StartAsync();
    }

    private void OnProcessExited(object? sender, EventArgs e)
    {
        if (_disposed)
            return;

        _logger?.LogWarning("RailGPT runtime exited with code {ExitCode}.", _process?.ExitCode);
        SetRunning(false);
    }

    private void SetRunning(bool running)
    {
        if (IsRunning == running)
            return;

        IsRunning = running;
        StatusChanged?.Invoke(this, running);
    }

    private void LogProcessLine(string? line, bool error)
    {
        if (string.IsNullOrWhiteSpace(line))
            return;
        if (error) _logger?.LogWarning("RailGPT: {Line}", line);
        else _logger?.LogInformation("RailGPT: {Line}", line);
    }

    private static ProcessStartInfo CreateStartInfo(RuntimeInfo runtime, int port, string bridgePipeName)
    {
        var info = new ProcessStartInfo
        {
            FileName = runtime.Executable,
            WorkingDirectory = runtime.Directory,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
        };
        if (runtime.IsPython)
            info.ArgumentList.Add(runtime.EntryPoint!);

        info.Environment["RAILGPT_HOST"] = "127.0.0.1";
        info.Environment["RAILGPT_PORT"] = port.ToString();
        info.Environment["RAILGPT_MODE"] = "server";
        info.Environment["RAILGO_BRIDGE_PIPE"] = bridgePipeName;
        info.Environment["RAILGPT_DATA_ROOT"] = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "RailGPT");
        return info;
    }

    private static RuntimeInfo? FindRuntime()
    {
        foreach (var directory in FindRailGptDirectories())
        {
            var bundled = Path.Combine(directory, "RailGPT.Runtime.exe");
            var nestedBundled = Path.Combine(directory, "runtime", "RailGPT.Runtime.exe");
            if (File.Exists(bundled))
                return new RuntimeInfo(directory, bundled, null, false, null);
            if (File.Exists(nestedBundled))
                return new RuntimeInfo(directory, nestedBundled, null, false, null);

            var entryPoint = Path.Combine(directory, "server_entry.py");
            if (File.Exists(entryPoint))
            {
                foreach (var python in FindPythonCandidates())
                {
                    if (CanExecute(python, "-c", "import flask, werkzeug"))
                        return new RuntimeInfo(directory, python, entryPoint, true, null);
                }
            }
        }
        return null;
    }

    private static IEnumerable<string> FindRailGptDirectories()
    {
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var candidates = new List<string>
        {
            Path.Combine(AppContext.BaseDirectory, "RailGPT"),
            Path.Combine(Directory.GetCurrentDirectory(), "RailGPT"),
        };

        var current = new DirectoryInfo(AppContext.BaseDirectory);
        for (var i = 0; i < 8 && current != null; i++, current = current.Parent!)
            candidates.Add(Path.Combine(current.FullName, "RailGPT"));

        foreach (var path in candidates)
        {
            var full = Path.GetFullPath(path);
            if (seen.Add(full) && Directory.Exists(full))
                yield return full;
        }
    }

    private static IEnumerable<string> FindPythonCandidates()
    {
        var configured = Environment.GetEnvironmentVariable("RAILGPT_PYTHON");
        if (!string.IsNullOrWhiteSpace(configured))
            yield return configured;

        yield return "python.exe";
        yield return "python3.exe";
        yield return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Python", "Python312", "python.exe");
        yield return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "anaconda3", "python.exe");

        var userName = Environment.UserName;
        foreach (var drive in DriveInfo.GetDrives().Where(drive => drive.IsReady))
            yield return Path.Combine(drive.RootDirectory.FullName, "Users", userName, "anaconda3", "python.exe");
    }

    private static bool CanExecute(string executable, params string[] arguments)
    {
        try
        {
            var startInfo = new ProcessStartInfo
            {
                FileName = executable,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
            };
            foreach (var argument in arguments)
                startInfo.ArgumentList.Add(argument);
            using var process = Process.Start(startInfo);
            process?.WaitForExit(5000);
            return process?.ExitCode == 0;
        }
        catch { return false; }
    }

    private static async Task<int> FindAvailablePortAsync(int startPort)
    {
        for (var port = startPort; port < startPort + MaxPortTries; port++)
        {
            try
            {
                var listener = new TcpListener(IPAddress.Loopback, port);
                listener.Start();
                listener.Stop();
                return port;
            }
            catch (SocketException) { await Task.Yield(); }
        }

        var fallback = new TcpListener(IPAddress.Loopback, 0);
        fallback.Start();
        var portNumber = ((IPEndPoint)fallback.LocalEndpoint).Port;
        fallback.Stop();
        return portNumber;
    }

    private async Task<bool> WaitForServerAsync(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            if (await HealthCheckAsync())
                return true;
            await Task.Delay(500);
        }
        return false;
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        _healthCts?.Cancel();
        if (_ownsProcess)
        {
            try { StopAsync().GetAwaiter().GetResult(); } catch { }
        }
        _process?.Dispose();
        _healthCts?.Dispose();
    }

    private sealed record RuntimeInfo(string Directory, string Executable, string? EntryPoint, bool IsPython, string? BridgePipeName);
}
