namespace RailGo.AI.Models;

public enum RailGptRuntimeState
{
    Stopped,
    Starting,
    Ready,
    Stopping,
    MissingRuntime,
    MissingWebView2,
    UnsupportedArchitecture,
    Failed,
    Exited,
}

public sealed record RailGptRuntimeStatus(
    RailGptRuntimeState State,
    string Message,
    string BaseUrl,
    string RuntimePath,
    string LogPath,
    int? ProcessId = null,
    int? ExitCode = null)
{
    public bool IsReady => State == RailGptRuntimeState.Ready;
    public bool IsTerminalFailure => State is
        RailGptRuntimeState.MissingRuntime or
        RailGptRuntimeState.MissingWebView2 or
        RailGptRuntimeState.UnsupportedArchitecture or
        RailGptRuntimeState.Failed or
        RailGptRuntimeState.Exited;
}

public sealed record RailGptStartResult(RailGptRuntimeStatus Status)
{
    public bool Success => Status.IsReady;
}
