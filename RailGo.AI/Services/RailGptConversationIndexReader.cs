using System.Text;
using System.Text.Json;
using RailGo.AI.Models;

namespace RailGo.AI.Services;

public sealed class RailGptConversationIndexReader : IRailGptConversationIndexReader
{
    private static readonly TimeSpan[] RetryDelays =
    [
        TimeSpan.Zero,
        TimeSpan.FromMilliseconds(50),
        TimeSpan.FromMilliseconds(150),
        TimeSpan.FromMilliseconds(300),
    ];

    private readonly IRailGptDiagnostics _diagnostics;
    private readonly SemaphoreSlim _refreshGate = new(1, 1);
    private readonly object _debounceSync = new();
    private readonly string _conversationDirectory;
    private readonly string _indexPath;
    private FileSystemWatcher? _watcher;
    private CancellationTokenSource? _debounceCts;
    private bool _disposed;

    public IReadOnlyList<ChatConversation> LastKnownConversations { get; private set; } =
        Array.Empty<ChatConversation>();

    public event EventHandler<IReadOnlyList<ChatConversation>>? ConversationsChanged;

    public RailGptConversationIndexReader(IRailGptDiagnostics diagnostics)
    {
        _diagnostics = diagnostics;
        _conversationDirectory = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "RailGPT",
            "conversations");
        _indexPath = Path.Combine(_conversationDirectory, "index.json");
    }

    public void Start()
    {
        if (_disposed || _watcher != null)
            return;

        Directory.CreateDirectory(_conversationDirectory);
        _watcher = new FileSystemWatcher(_conversationDirectory, "index.json")
        {
            NotifyFilter = NotifyFilters.FileName | NotifyFilters.LastWrite | NotifyFilters.Size,
            EnableRaisingEvents = true,
        };
        _watcher.Changed += OnIndexChanged;
        _watcher.Created += OnIndexChanged;
        _watcher.Renamed += OnIndexChanged;
        _ = RefreshObservedAsync();
    }

    public async Task<IReadOnlyList<ChatConversation>> RefreshAsync(
        CancellationToken cancellationToken = default)
    {
        await _refreshGate.WaitAsync(cancellationToken);
        try
        {
            if (!File.Exists(_indexPath))
                return LastKnownConversations;

            Exception? lastError = null;
            foreach (var delay in RetryDelays)
            {
                if (delay > TimeSpan.Zero)
                    await Task.Delay(delay, cancellationToken);

                try
                {
                    var json = await ReadSharedUtf8Async(_indexPath, cancellationToken);
                    var items = JsonSerializer.Deserialize<List<ChatConversation>>(json,
                        new JsonSerializerOptions { PropertyNameCaseInsensitive = true }) ?? new();
                    var sorted = items
                        .Where(item => item.Id > 0)
                        .OrderByDescending(item => item.UpdatedAt)
                        .ToArray();
                    LastKnownConversations = sorted;
                    ConversationsChanged?.Invoke(this, sorted);
                    return sorted;
                }
                catch (Exception ex) when (ex is IOException or JsonException)
                {
                    lastError = ex;
                }
            }

            if (lastError != null)
                _diagnostics.Warning($"Conversation index remained unreadable; retaining last valid list. {lastError.Message}");
            return LastKnownConversations;
        }
        finally
        {
            _refreshGate.Release();
        }
    }

    private void OnIndexChanged(object sender, FileSystemEventArgs args)
    {
        lock (_debounceSync)
        {
            if (_disposed)
                return;
            _debounceCts?.Cancel();
            _debounceCts?.Dispose();
            _debounceCts = new CancellationTokenSource();
            _ = DebouncedRefreshAsync(_debounceCts.Token);
        }
    }

    private async Task DebouncedRefreshAsync(CancellationToken cancellationToken)
    {
        try
        {
            await Task.Delay(200, cancellationToken);
            await RefreshAsync(cancellationToken);
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception ex)
        {
            _diagnostics.Error("Conversation index watcher failed.", ex);
        }
    }

    private async Task RefreshObservedAsync()
    {
        try
        {
            await RefreshAsync();
        }
        catch (Exception ex)
        {
            _diagnostics.Error("Initial conversation index read failed.", ex);
        }
    }

    private static async Task<string> ReadSharedUtf8Async(
        string path,
        CancellationToken cancellationToken)
    {
        await using var stream = new FileStream(
            path,
            FileMode.Open,
            FileAccess.Read,
            FileShare.ReadWrite | FileShare.Delete,
            bufferSize: 4096,
            useAsync: true);
        using var reader = new StreamReader(stream, new UTF8Encoding(false, true));
        return await reader.ReadToEndAsync(cancellationToken);
    }

    public void Dispose()
    {
        if (_disposed)
            return;
        _disposed = true;

        if (_watcher != null)
        {
            _watcher.EnableRaisingEvents = false;
            _watcher.Changed -= OnIndexChanged;
            _watcher.Created -= OnIndexChanged;
            _watcher.Renamed -= OnIndexChanged;
            _watcher.Dispose();
        }
        _debounceCts?.Cancel();
        _debounceCts?.Dispose();
        // A debounced refresh may still be unwinding and releasing this gate.
        // It is process-lifetime state, so leaving it undisposed avoids a
        // shutdown-only ObjectDisposedException.
    }
}
