using System.Runtime.InteropServices;
using System.Text;

namespace RailGo.AI.Services;

public sealed class RailGptDiagnostics : IRailGptDiagnostics
{
    private const long MaxLogBytes = 5 * 1024 * 1024;
    private const int MaxLogFiles = 5;
    private readonly object _sync = new();

    public string LogDirectory { get; } = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "RailGo",
        "Logs");

    public string CurrentLogPath => Path.Combine(LogDirectory, "railgpt.log");

    public RailGptDiagnostics()
    {
        Directory.CreateDirectory(LogDirectory);
        RotateIfNeeded();
        Info($"Diagnostics initialized. OS={Environment.OSVersion}; Arch={RuntimeInformation.ProcessArchitecture}; Base={AppContext.BaseDirectory}");
    }

    public void Info(string message) => Write("INF", message, null);
    public void Warning(string message) => Write("WRN", message, null);
    public void Error(string message, Exception? exception = null) => Write("ERR", message, exception);

    private void Write(string level, string message, Exception? exception)
    {
        try
        {
            lock (_sync)
            {
                RotateIfNeeded();
                var line = $"{DateTimeOffset.Now:O} [{level}] {message}";
                if (exception != null)
                    line += $"{Environment.NewLine}{exception}";
                File.AppendAllText(CurrentLogPath, line + Environment.NewLine, new UTF8Encoding(false));
            }
        }
        catch
        {
            // Diagnostics must never crash the host application.
        }
    }

    private void RotateIfNeeded()
    {
        Directory.CreateDirectory(LogDirectory);
        var current = new FileInfo(CurrentLogPath);
        if (!current.Exists || current.Length < MaxLogBytes)
            return;

        for (var index = MaxLogFiles - 1; index >= 1; index--)
        {
            var source = Path.Combine(LogDirectory, index == 1 ? "railgpt.log" : $"railgpt.{index - 1}.log");
            var target = Path.Combine(LogDirectory, $"railgpt.{index}.log");
            if (File.Exists(source))
                File.Move(source, target, true);
        }
    }
}
