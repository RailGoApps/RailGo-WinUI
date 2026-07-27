using RailGo.AI.Models;

namespace RailGo.AI.Services;

public interface IRailGptStartupCoordinator
{
    Task<RailGptStartResult> ReadyTask { get; }
    void WarmUp();
    Task<RailGptStartResult> StartAsync(CancellationToken cancellationToken = default);
    Task<RailGptStartResult> RetryAsync(CancellationToken cancellationToken = default);
}
