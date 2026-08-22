namespace RailGo.AI.Services;

/// <summary>Configuration and lifecycle contract for the local RailGo bridge.</summary>
public interface IRailGoBridgeHost
{
    string PipeName { get; }
    Task StartAsync(CancellationToken cancellationToken = default);
    Task StopAsync();
}
