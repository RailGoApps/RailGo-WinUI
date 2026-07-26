using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.UI.Dispatching;
using RailGo.AI.Services;
using RailGo.Contracts.ViewModels;

namespace RailGo.ViewModels.Pages.Chat;

/// <summary>
/// Thin ViewModel for the Chat page — the actual chat UI is rendered by
/// RailGPT's web frontend via WebView2. This class only manages the
/// Python backend lifecycle and exposes a BackendReady event.
/// </summary>
public partial class ChatViewModel : ObservableObject, INavigationAware
{
    private readonly IPythonProcessManager _processManager;
    private readonly DispatcherQueue _dispatcher;
    private EventHandler<bool>? _statusHandler;

    /// <summary>Fired when the RailGPT backend is ready, providing the URL for WebView2.</summary>
    public event Action<string>? BackendReady;

    [ObservableProperty]
    private bool _isBackendRunning;

    [ObservableProperty]
    private string _statusText = "正在启动 RailGPT 后端…";

    public ChatViewModel(IPythonProcessManager processManager)
    {
        _processManager = processManager;
        _dispatcher = DispatcherQueue.GetForCurrentThread();
    }

    public async Task InitializeAsync()
    {
        // Subscribe to backend status changes
        _statusHandler = (_, running) =>
        {
            _dispatcher.TryEnqueue(() =>
            {
                IsBackendRunning = running;
                StatusText = running ? "就绪" : "后端未连接";
            });
        };
        _processManager.StatusChanged += _statusHandler;

        // Start backend if not already running
        if (!_processManager.IsRunning)
        {
            await _processManager.StartAsync();
        }

        // Notify WebView2 to load the RailGPT frontend
        if (_processManager.IsRunning)
        {
            IsBackendRunning = true;
            StatusText = "就绪";
            var url = _processManager.BaseUrl;
            _dispatcher.TryEnqueue(() => BackendReady?.Invoke(url));
        }
        else
        {
            // Retry a few times
            for (int i = 0; i < 5 && !_processManager.IsRunning; i++)
            {
                await Task.Delay(1000);
            }
            if (_processManager.IsRunning)
            {
                IsBackendRunning = true;
                StatusText = "就绪";
                var url = _processManager.BaseUrl;
                _dispatcher.TryEnqueue(() => BackendReady?.Invoke(url));
            }
            else
            {
                StatusText = "RailGPT 后端启动失败";
            }
        }
    }

    public void Cleanup()
    {
        if (_statusHandler != null)
            _processManager.StatusChanged -= _statusHandler;
    }

    /// <summary>Called by the navigation framework when navigating to this page.</summary>
    public async void OnNavigatedTo(object parameter)
    {
        await InitializeAsync();
    }

    /// <summary>Called by the navigation framework when navigating away from this page.</summary>
    public void OnNavigatedFrom()
    {
        Cleanup();
    }
}
