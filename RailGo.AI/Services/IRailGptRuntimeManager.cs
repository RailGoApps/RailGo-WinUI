using RailGo.AI.Models;

namespace RailGo.AI.Services;

public interface IRailGptRuntimeManager
{
    string BaseUrl { get; }
    string RuntimeDescription { get; }
    RailGptRuntimeStatus Status { get; }
    event EventHandler<RailGptRuntimeStatus>? StatusChanged;

    Task<RailGptStartResult> StartAsync(CancellationToken cancellationToken = default);
    Task StopAsync(CancellationToken cancellationToken = default);
    Task<bool> HealthCheckAsync(CancellationToken cancellationToken = default);
}
