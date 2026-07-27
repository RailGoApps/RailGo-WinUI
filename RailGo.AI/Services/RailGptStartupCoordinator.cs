using RailGo.AI.Models;

namespace RailGo.AI.Services;

public sealed class RailGptStartupCoordinator : IRailGptStartupCoordinator
{
    private readonly IRailGptRuntimeManager _runtimeManager;
    private readonly IRailGptDiagnostics _diagnostics;
    private readonly object _sync = new();
    private Task<RailGptStartResult>? _readyTask;

    public RailGptStartupCoordinator(
        IRailGptRuntimeManager runtimeManager,
        IRailGptDiagnostics diagnostics)
    {
        _runtimeManager = runtimeManager;
        _diagnostics = diagnostics;
    }

    public Task<RailGptStartResult> ReadyTask
    {
        get
        {
            lock (_sync)
                return _readyTask ?? Task.FromResult(new RailGptStartResult(_runtimeManager.Status));
        }
    }

    public void WarmUp()
    {
        _ = StartAsync();
    }

    public Task<RailGptStartResult> StartAsync(CancellationToken cancellationToken = default)
    {
        Task<RailGptStartResult> sharedTask;
        lock (_sync)
        {
            if (_readyTask == null ||
                _readyTask.IsCanceled ||
                _readyTask.IsFaulted ||
                (_readyTask.IsCompleted && _runtimeManager.Status.IsTerminalFailure))
            {
                // The host owns startup lifetime. A page cancellation may stop
                // waiting, but must not cancel the process shared by all pages.
                _readyTask = StartObservedAsync(CancellationToken.None);
            }
            sharedTask = _readyTask;
        }

        return cancellationToken.CanBeCanceled
            ? sharedTask.WaitAsync(cancellationToken)
            : sharedTask;
    }

    public async Task<RailGptStartResult> RetryAsync(CancellationToken cancellationToken = default)
    {
        await _runtimeManager.StopAsync(cancellationToken);
        Task<RailGptStartResult> sharedTask;
        lock (_sync)
        {
            _readyTask = StartObservedAsync(CancellationToken.None);
            sharedTask = _readyTask;
        }
        return cancellationToken.CanBeCanceled
            ? await sharedTask.WaitAsync(cancellationToken)
            : await sharedTask;
    }

    private async Task<RailGptStartResult> StartObservedAsync(CancellationToken cancellationToken)
    {
        try
        {
            return await _runtimeManager.StartAsync(cancellationToken);
        }
        catch (OperationCanceledException)
        {
            throw;
        }
        catch (Exception ex)
        {
            _diagnostics.Error("Unhandled RailGPT startup failure.", ex);
            return new RailGptStartResult(_runtimeManager.Status);
        }
    }
}
