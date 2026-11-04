using CommunityToolkit.Mvvm.ComponentModel;
using Microsoft.UI.Dispatching;
using RailGo.AI.Models;
using RailGo.AI.Services;
using RailGo.Contracts.ViewModels;

namespace RailGo.ViewModels.Pages.Chat;

public partial class ChatViewModel : ObservableObject, INavigationAware
{
    private readonly IRailGptStartupCoordinator _startupCoordinator;
    private readonly IRailGptRuntimeManager _runtimeManager;
    private readonly IRailGptDiagnostics _diagnostics;
    private readonly DispatcherQueue _dispatcher;
    private long _navigationVersion;

    public event Action<RailGptStartResult, int?>? Prepared;
    public event Action<RailGptRuntimeStatus>? RuntimeStatusChanged;

    public string BaseUrl => _runtimeManager.BaseUrl;
    public string RuntimeDescription => _runtimeManager.RuntimeDescription;
    public RailGptRuntimeStatus RuntimeStatus => _runtimeManager.Status;

    [ObservableProperty]
    private string _statusText = "正在启动 RailGPT…";

    [ObservableProperty]
    private int? _conversationId;

    public ChatViewModel(
        IRailGptStartupCoordinator startupCoordinator,
        IRailGptRuntimeManager runtimeManager,
        IRailGptDiagnostics diagnostics)
    {
        _startupCoordinator = startupCoordinator;
        _runtimeManager = runtimeManager;
        _diagnostics = diagnostics;
        _dispatcher = DispatcherQueue.GetForCurrentThread();
        _runtimeManager.StatusChanged += OnRuntimeStatusChanged;
    }

    public void OnNavigatedTo(object parameter)
    {
        ConversationId = parameter switch
        {
            int cid => cid,
            string text when int.TryParse(text, out var cid) => cid,
            _ => null,
        };

        var version = Interlocked.Increment(ref _navigationVersion);
        _ = PrepareObservedAsync(version, ConversationId);
    }

    public void OnNavigatedFrom()
    {
        // ChatPage is cached intentionally. Runtime and WebView subscriptions
        // remain alive so returning to RailGPT does not rebuild Chromium.
    }

    public void SelectConversation(int conversationId)
    {
        ConversationId = conversationId;
    }

    public async Task<RailGptStartResult> RetryAsync(CancellationToken cancellationToken = default)
        => await _startupCoordinator.RetryAsync(cancellationToken);

    private async Task PrepareObservedAsync(long version, int? conversationId)
    {
        RailGptStartResult result;
        try
        {
            result = await _startupCoordinator.StartAsync();
        }
        catch (Exception ex)
        {
            _diagnostics.Error("Chat workspace preparation failed.", ex);
            result = new RailGptStartResult(_runtimeManager.Status);
        }

        if (version != Volatile.Read(ref _navigationVersion))
            return;

        _dispatcher.TryEnqueue(() => Prepared?.Invoke(result, conversationId));
    }

    private void OnRuntimeStatusChanged(object? sender, RailGptRuntimeStatus status)
    {
        _dispatcher.TryEnqueue(() =>
        {
            OnPropertyChanged(nameof(RuntimeStatus));
            OnPropertyChanged(nameof(BaseUrl));
            OnPropertyChanged(nameof(RuntimeDescription));
            StatusText = status.Message;
            RuntimeStatusChanged?.Invoke(status);
        });
    }
}
