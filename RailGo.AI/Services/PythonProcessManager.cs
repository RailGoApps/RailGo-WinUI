using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using Microsoft.Extensions.Logging;

namespace RailGo.AI.Services;

/// <summary>
/// Manages the RailGPT Python backend lifecycle.
/// Port selection: tries 5033 first (like Jupyter Notebook auto-find),
/// increments until an available port is found. Reports actual port via <see cref="BaseUrl"/>.
/// </summary>
public class PythonProcessManager : IPythonProcessManager, IDisposable
{
    private const int DEFAULT_PORT = 5033;
    private const int MAX_PORT_TRIES = 100;  // 5033..5132
    private const int MAX_RESTART_ATTEMPTS = 3;
    private const int HEALTH_CHECK_INTERVAL_MS = 5000;
    private readonly HttpClient _httpClient;
    private readonly ILogger<PythonProcessManager>? _logger;
    private CancellationTokenSource? _healthCts;
    private Process? _process;
    private int _restartCount;
    private int _actualPort;
    private bool _disposed;

    /// <summary>Base URL of the running Flask server with actual port (e.g. http://localhost:5034).</summary>
    public string BaseUrl => $"http://localhost:{_actualPort}";

    public bool IsRunning { get; private set; }

    public event EventHandler<bool>? StatusChanged;

    public PythonProcessManager(HttpClient httpClient, ILogger<PythonProcessManager>? logger = null)
    {
        _httpClient = httpClient;
        _httpClient.Timeout = TimeSpan.FromSeconds(5);
        _logger = logger;
        _actualPort = DEFAULT_PORT;
    }

    // ================================================================
    // Public API
    // ================================================================

    public async Task StartAsync()
    {
        if (_process != null && !_process.HasExited)
        {
            _logger?.LogInformation("RailGPT backend already running on port {Port}.", _actualPort);
            return;
        }

        // Check if an existing healthy backend is already running on the default port
        _actualPort = DEFAULT_PORT;
        if (await HealthCheckAsync())
        {
            _logger?.LogInformation("Reusing existing RailGPT backend on port {Port}.", _actualPort);
            IsRunning = true;
            StatusChanged?.Invoke(this, true);
            _healthCts = new CancellationTokenSource();
            _ = PeriodicHealthCheckAsync(_healthCts.Token);
            return;
        }

        var pythonPath = FindPython();
        if (pythonPath == null)
        {
            _logger?.LogError("Python runtime not found. RailGPT backend cannot start.");
            return;
        }

        var railGptDir = FindRailGptDirectory();
        if (railGptDir == null)
        {
            _logger?.LogError("RailGPT project directory not found (web_app.py missing).");
            return;
        }

        // --- Jupyter-style auto port find ---
        _actualPort = await FindAvailablePortAsync(DEFAULT_PORT);
        _logger?.LogInformation("RailGPT binding to port {Port}.", _actualPort);

        // Build the inline Python bootstrap script
        var bootstrap = BuildBootstrapScript(railGptDir, _actualPort);

        var startInfo = new ProcessStartInfo
        {
            FileName = pythonPath,
            Arguments = $"-c \"{EscapePythonArg(bootstrap)}\"",
            WorkingDirectory = railGptDir,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
        };
        startInfo.Environment["RAILGPT_PORT"] = _actualPort.ToString();
        startInfo.Environment["RAILGPT_MODE"] = "server";

        _process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
        _process.OutputDataReceived += (_, e) =>
        {
            if (!string.IsNullOrEmpty(e.Data))
                _logger?.LogInformation("RailGPT: {Line}", e.Data);
        };
        _process.ErrorDataReceived += (_, e) =>
        {
            if (!string.IsNullOrEmpty(e.Data))
                _logger?.LogWarning("RailGPT[err]: {Line}", e.Data);
        };
        _process.Exited += OnProcessExited;

        try
        {
            _process.Start();
            _process.BeginOutputReadLine();
            _process.BeginErrorReadLine();

            var healthy = await WaitForServerAsync(TimeSpan.FromSeconds(30));
            if (healthy)
            {
                IsRunning = true;
                _restartCount = 0;
                StatusChanged?.Invoke(this, true);
                _logger?.LogInformation("RailGPT backend healthy on {Url}.", BaseUrl);

                _healthCts = new CancellationTokenSource();
                _ = PeriodicHealthCheckAsync(_healthCts.Token);
            }
            else
            {
                _logger?.LogWarning("RailGPT did not respond on port {Port} within timeout.", _actualPort);
                await StopAsync();
            }
        }
        catch (Exception ex)
        {
            _logger?.LogError(ex, "Failed to launch RailGPT process.");
        }
    }

    public async Task StopAsync()
    {
        _healthCts?.Cancel();

        if (_process != null && !_process.HasExited)
        {
            try { await _httpClient.GetAsync($"{BaseUrl}/api/shutdown"); }
            catch { /* best-effort graceful shutdown */ }

            if (!_process.HasExited)
            {
                _process.Kill(entireProcessTree: true);
                await Task.Run(() => _process.WaitForExit(5000));
            }
        }

        IsRunning = false;
        StatusChanged?.Invoke(this, false);
        _logger?.LogInformation("RailGPT backend on port {Port} stopped.", _actualPort);
    }

    public async Task<bool> HealthCheckAsync()
    {
        try
        {
            var resp = await _httpClient.GetAsync($"{BaseUrl}/api/status");
            return resp.IsSuccessStatusCode;
        }
        catch { return false; }
    }

    // ================================================================
    // Port finding (Jupyter style)
    // ================================================================

    /// <summary>
    /// Find the first available port starting from <paramref name="startPort"/>.
    /// Tries up to MAX_PORT_TRIES ports (like Jupyter's port auto-increment).
    /// </summary>
    private static async Task<int> FindAvailablePortAsync(int startPort)
    {
        for (int port = startPort; port < startPort + MAX_PORT_TRIES; port++)
        {
            if (await IsPortAvailableAsync(port))
                return port;
        }
        // Fallback: let OS pick a random port
        return FindRandomAvailablePort();
    }

    private static Task<bool> IsPortAvailableAsync(int port)
    {
        try
        {
            var listener = new TcpListener(IPAddress.Loopback, port);
            listener.Start();
            listener.Stop();
            return Task.FromResult(true);
        }
        catch (SocketException)
        {
            return Task.FromResult(false);
        }
    }

    private static int FindRandomAvailablePort()
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        int port = ((IPEndPoint)listener.LocalEndpoint).Port;
        listener.Stop();
        return port;
    }

    // ================================================================
    // Internal helpers
    // ================================================================

    private static string BuildBootstrapScript(string railGptDir, int port)
    {
        // Use Python raw string r'...' — backslashes are literal, no escaping needed
        return $"import sys; sys.path.insert(0, r'{railGptDir}'); " +
               $"from web_app import app; from app_init import build_backend; " +
               $"app = build_backend(); " +
               $"from werkzeug.serving import run_simple; " +
               $"run_simple('127.0.0.1', {port}, app, threaded=True)";
    }

    private static string EscapePythonArg(string script)
    {
        // Escape double-quotes for safe embedding in -c "..."
        return script.Replace("\"", "\\\"");
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

    private async Task PeriodicHealthCheckAsync(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            await Task.Delay(HEALTH_CHECK_INTERVAL_MS, ct);
            if (ct.IsCancellationRequested) break;

            if (!await HealthCheckAsync() && IsRunning)
            {
                _logger?.LogWarning("Health check failed for port {Port}.", _actualPort);
                await TryRestartAsync();
            }
        }
    }

    private async Task TryRestartAsync()
    {
        if (_restartCount >= MAX_RESTART_ATTEMPTS)
        {
            _logger?.LogError("RailGPT restart limit ({Max}) reached. Giving up.", MAX_RESTART_ATTEMPTS);
            IsRunning = false;
            StatusChanged?.Invoke(this, false);
            return;
        }
        _restartCount++;
        _logger?.LogInformation("Restarting RailGPT (attempt {Attempt}/{Max}).", _restartCount, MAX_RESTART_ATTEMPTS);
        await StopAsync();
        await Task.Delay(1000);
        await StartAsync();
    }

    private void OnProcessExited(object? sender, EventArgs e)
    {
        if (!_disposed)
        {
            _logger?.LogWarning("RailGPT process exited (code {Code}).", _process?.ExitCode);
            IsRunning = false;
            StatusChanged?.Invoke(this, false);
        }
    }

    // ================================================================
    // Discovery
    // ================================================================

    private static string? FindPython()
    {
        // Priority: known working paths first, then fallback to PATH lookup
        var candidates = new[]
        {
            // Known working anaconda environment (from user memory)
            @"D:\Users\tomat\anaconda3\python.exe",
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                @"anaconda3\python.exe"),
            // Standard Python installs
            @"C:\Python314\python.exe",
            @"C:\Python312\python.exe", @"C:\Python311\python.exe",
            @"D:\Python312\python.exe", @"D:\Python311\python.exe",
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                @"Programs\Python\Python312\python.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                @"Programs\Python\Python311\python.exe"),
            // Generic PATH lookup (last resort)
            @"python.exe",
            "python3",
            "python",
        };
        foreach (var c in candidates)
        {
            try
            {
                var psi = new ProcessStartInfo
                {
                    FileName = c, Arguments = "--version",
                    UseShellExecute = false, RedirectStandardOutput = true,
                    RedirectStandardError = true, CreateNoWindow = true,
                };
                using var p = Process.Start(psi);
                if (p != null)
                {
                    p.WaitForExit(5000);
                    if (p.ExitCode == 0) return c;
                }
            }
            catch { /* candidate not found or not executable */ }
        }
        return null;
    }

    private static string? FindRailGptDirectory()
    {
        var candidates = new[]
        {
            // Priority: local copy first
            @"D:\Desktop\RailGo\RailGPT",
            Path.Combine(AppContext.BaseDirectory, "RailGPT"),
            Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "RailGPT"),
        };
        foreach (var c in candidates)
        {
            if (Directory.Exists(c) && File.Exists(Path.Combine(c, "web_app.py")))
                return c;
        }
        return null;
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        _healthCts?.Cancel();
        _process?.Dispose();
        _healthCts?.Dispose();
    }
}
