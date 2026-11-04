using System.IO.Pipes;
using System.Text;
using System.Text.Json;
using RailGo.AI.Services;

namespace RailGo.Services;

/// <summary>
/// Local JSON-lines RPC bridge used by the RailGPT process. The named pipe is
/// local-only and avoids exposing RailGo.Core query operations on a TCP port.
/// </summary>
public sealed class RailGoBridgeServer : IRailGoBridgeHost, IAsyncDisposable
{
    private readonly QueryService _queryService;
    private readonly object _lifecycleSync = new();
    private CancellationTokenSource? _stop;
    private Task? _acceptLoop;

    public RailGoBridgeServer(QueryService queryService)
    {
        _queryService = queryService;
        PipeName = $"RailGoBridge-{Environment.ProcessId}-{Guid.NewGuid():N}";
    }

    public string PipeName { get; }

    public Task StartAsync(CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        lock (_lifecycleSync)
        {
            if (_acceptLoop is { IsCompleted: false })
                return Task.CompletedTask;

            _stop?.Dispose();
            _stop = new CancellationTokenSource();
            _acceptLoop = AcceptLoopAsync(_stop.Token);
        }
        return Task.CompletedTask;
    }

    private async Task AcceptLoopAsync(CancellationToken cancellationToken)
    {
        while (!cancellationToken.IsCancellationRequested)
        {
            await using var pipe = new NamedPipeServerStream(
                PipeName,
                PipeDirection.InOut,
                1,
                PipeTransmissionMode.Byte,
                PipeOptions.Asynchronous);
            try
            {
                await pipe.WaitForConnectionAsync(cancellationToken);
                using var reader = new StreamReader(pipe, Encoding.UTF8, leaveOpen: true);
                await using var writer = new StreamWriter(pipe, new UTF8Encoding(false), leaveOpen: true)
                {
                    AutoFlush = true,
                };

                var line = await reader.ReadLineAsync(cancellationToken);
                if (!string.IsNullOrWhiteSpace(line))
                    await writer.WriteLineAsync(await DispatchAsync(line));
            }
            catch (OperationCanceledException) { break; }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"RailGo bridge error: {ex.Message}");
            }
        }
    }

    private async Task<string> DispatchAsync(string line)
    {
        string? requestId = null;
        try
        {
            using var document = JsonDocument.Parse(line);
            var root = document.RootElement;
            requestId = root.TryGetProperty("id", out var idValue) ? idValue.GetString() : null;
            var method = root.GetProperty("method").GetString() ?? string.Empty;
            var parameters = root.TryGetProperty("params", out var paramsValue)
                ? paramsValue
                : default;
            var result = await ExecuteAsync(method, parameters);
            return JsonSerializer.Serialize(new { id = requestId, ok = true, result });
        }
        catch (Exception ex)
        {
            return JsonSerializer.Serialize(new { id = requestId, ok = false, error = ex.Message });
        }
    }

    private async Task<object?> ExecuteAsync(string method, JsonElement parameters)
    {
        string StringParam(string name, string fallback = "") =>
            parameters.ValueKind == JsonValueKind.Object && parameters.TryGetProperty(name, out var value)
                ? value.GetString() ?? fallback
                : fallback;

        bool BoolParam(string name, bool fallback = false)
        {
            if (parameters.ValueKind != JsonValueKind.Object ||
                !parameters.TryGetProperty(name, out var value) ||
                (value.ValueKind != JsonValueKind.True && value.ValueKind != JsonValueKind.False))
                return fallback;
            return value.GetBoolean();
        }

        return method switch
        {
            "station.search" => await _queryService.QueryStationPreselectAsync(StringParam("keyword")),
            "station.get" => await _queryService.QueryStationQueryAsync(StringParam("telecode")),
            "train.search" => await _queryService.QueryTrainPreselectAsync(StringParam("keyword")),
            "train.get" => await _queryService.QueryTrainQueryAsync(StringParam("train")),
            "route.search" => await _queryService.QueryStationToStationQueryAsync(
                StringParam("from"), StringParam("to"), StringParam("date"), BoolParam("city", true)),
            "station.board" => await _queryService.QueryGetBigScreenDataAsync(StringParam("telecode")),
            "emu.query" => await _queryService.QueryEmuQueryAsync(StringParam("type"), StringParam("keyword")),
            "emu.assignment" => await _queryService.QueryEmuAssignmentQueryAsync(
                StringParam("type"), StringParam("keyword"), int.TryParse(StringParam("cursor"), out var cursor) ? cursor : 0),
            "train.delay" => await _queryService.QueryTrainDelayAsync(
                StringParam("date"), StringParam("train"), StringParam("from"), StringParam("to")),
            _ => throw new InvalidOperationException($"Unsupported RailGo bridge method: {method}"),
        };
    }

    public async Task StopAsync()
    {
        CancellationTokenSource? stop;
        Task? acceptLoop;
        lock (_lifecycleSync)
        {
            stop = _stop;
            acceptLoop = _acceptLoop;
            _stop = null;
            _acceptLoop = null;
        }

        if (stop == null)
            return;

        stop.Cancel();
        if (acceptLoop != null)
        {
            try
            {
                await acceptLoop.WaitAsync(TimeSpan.FromSeconds(2));
            }
            catch (OperationCanceledException)
            {
            }
            catch (TimeoutException)
            {
                // An in-flight RailGo query may not support cancellation.
                // Do not block application shutdown indefinitely.
            }
        }
        stop.Dispose();
    }

    public async ValueTask DisposeAsync()
    {
        await StopAsync();
    }
}
