namespace RailGo.AI.Services;

public interface IRailGptDiagnostics
{
    string LogDirectory { get; }
    string CurrentLogPath { get; }
    void Info(string message);
    void Warning(string message);
    void Error(string message, Exception? exception = null);
}
