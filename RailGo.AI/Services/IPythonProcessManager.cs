namespace RailGo.AI.Services;

/// <summary>
/// Manages the lifecycle of the RailGPT Python backend process.
/// </summary>
public interface IPythonProcessManager
{
    /// <summary>Base URL of the running Flask server (e.g. http://localhost:5033).</summary>
    string BaseUrl { get; }

    /// <summary>Whether the Python backend is running and healthy.</summary>
    bool IsRunning { get; }

    /// <summary>Raised when the backend status changes.</summary>
    event EventHandler<bool>? StatusChanged;

    /// <summary>Start the Python backend process. Safe to call multiple times.</summary>
    Task StartAsync();

    /// <summary>Gracefully stop the Python backend.</summary>
    Task StopAsync();

    /// <summary>Check health endpoint and update IsRunning.</summary>
    Task<bool> HealthCheckAsync();
}
