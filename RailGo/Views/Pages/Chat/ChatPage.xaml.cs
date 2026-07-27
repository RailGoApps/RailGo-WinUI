using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;
using Microsoft.Web.WebView2.Core;
using RailGo.AI.Models;
using RailGo.AI.Services;
using RailGo.Contracts.Services;
using RailGo.ViewModels.Pages.Chat;
using RailGo.ViewModels.Pages.Stations;
using RailGo.ViewModels.Pages.StationToStation;
using RailGo.ViewModels.Pages.Trains;
using Windows.System;

namespace RailGo.Views.Pages.Chat;

public sealed partial class ChatPage : Page
{
    private readonly INavigationService _navigationService;
    private readonly IAIChatClient _chatClient;
    private readonly IRailGptDiagnostics _diagnostics;
    private readonly SemaphoreSlim _webViewGate = new(1, 1);
    private WebView2? _webView;
    private CoreWebView2Environment? _webViewEnvironment;
    private bool _webViewInitialized;
    private bool _documentReady;
    private bool _navigationInProgress;
    private bool _processRecoveryAttempted;
    private int? _pendingConversationId;
    private string? _pendingRequestId;
    private string? _currentBaseUrl;

    public ChatViewModel ViewModel { get; }

    public ChatPage()
    {
        ViewModel = App.GetService<ChatViewModel>();
        _navigationService = App.GetService<INavigationService>();
        _chatClient = App.GetService<IAIChatClient>();
        _diagnostics = App.GetService<IRailGptDiagnostics>();
        InitializeComponent();

        NavigationCacheMode = NavigationCacheMode.Required;
        ViewModel.Prepared += OnWorkspacePrepared;
        ViewModel.RuntimeStatusChanged += OnRuntimeStatusChanged;
    }

    private void OnWorkspacePrepared(RailGptStartResult result, int? conversationId)
    {
        _pendingConversationId = conversationId;
        _ = HandlePreparedObservedAsync(result);
    }

    private void OnRuntimeStatusChanged(RailGptRuntimeStatus status)
    {
        if (status.IsReady)
        {
            _ = HandlePreparedObservedAsync(new RailGptStartResult(status));
            return;
        }

        if (status.State is RailGptRuntimeState.Starting or RailGptRuntimeState.Stopping)
        {
            _documentReady = false;
            _navigationInProgress = false;
            ShowLoading(status.Message);
            return;
        }

        if (status.IsTerminalFailure)
        {
            _documentReady = false;
            _navigationInProgress = false;
            ShowNativeStatus(status);
        }
    }

    private async Task HandlePreparedObservedAsync(RailGptStartResult result)
    {
        try
        {
            if (!result.Success)
            {
                ShowNativeStatus(result.Status);
                return;
            }

            await EnsureWebViewAsync(result.Status);
            if (_documentReady)
                SendConversationRequest(_pendingConversationId);
        }
        catch (Exception ex)
        {
            _diagnostics.Error("RailGPT workspace initialization failed.", ex);
            TearDownWebView();
            ShowNativeStatus(result.Status with
            {
                State = RailGptRuntimeState.Failed,
                Message = $"RailGPT 页面初始化失败：{ex.Message}",
            });
        }
    }

    private async Task EnsureWebViewAsync(RailGptRuntimeStatus status)
    {
        await _webViewGate.WaitAsync();
        try
        {
            await EnsureWebViewCoreAsync(status);
        }
        finally
        {
            _webViewGate.Release();
        }
    }

    private async Task EnsureWebViewCoreAsync(RailGptRuntimeStatus status)
    {
        ShowLoading(status.Message);

        string browserVersion;
        try
        {
            browserVersion = CoreWebView2Environment.GetAvailableBrowserVersionString();
            if (string.IsNullOrWhiteSpace(browserVersion))
            {
                ShowNativeStatus(status with
                {
                    State = RailGptRuntimeState.MissingWebView2,
                    Message = "需要安装 Microsoft Edge WebView2 Runtime。安装完成后点击“重试”即可。"
                });
                return;
            }
        }
        catch (Exception ex)
        {
            _diagnostics.Error("WebView2 Evergreen Runtime is unavailable.", ex);
            ShowNativeStatus(status with
            {
                State = RailGptRuntimeState.MissingWebView2,
                Message = "未检测到 Microsoft Edge WebView2 Runtime。安装后点击重试。",
            });
            return;
        }

        _diagnostics.Info($"WebView2 available. Version={browserVersion}");
        if (!_webViewInitialized)
        {
            _webViewEnvironment ??= await CoreWebView2Environment.CreateAsync();
            var webView = new WebView2();
            _webView = webView;
            WebViewHost.Children.Add(webView);
            await webView.EnsureCoreWebView2Async(_webViewEnvironment);
            var coreWebView = webView.CoreWebView2
                ?? throw new InvalidOperationException("WebView2 initialization completed without a CoreWebView2 instance.");
            coreWebView.WebMessageReceived += OnWebMessageReceived;
            coreWebView.NavigationCompleted += OnNavigationCompleted;
            coreWebView.ProcessFailed += OnWebViewProcessFailed;
            coreWebView.Settings.AreDevToolsEnabled =
#if DEBUG
                true;
#else
                false;
#endif
            _webViewInitialized = true;
        }

        if (_currentBaseUrl != status.BaseUrl ||
            (!_documentReady && !_navigationInProgress))
        {
            _currentBaseUrl = status.BaseUrl;
            _documentReady = false;
            _navigationInProgress = true;
            var url = $"{status.BaseUrl}/?embedded=1";
            _diagnostics.Info($"Navigating embedded RailGPT workspace to {url}");
            var coreWebView = _webView?.CoreWebView2
                ?? throw new InvalidOperationException("WebView2 is unavailable after initialization.");
            coreWebView.Navigate(url);
        }
    }

    private void OnNavigationCompleted(
        CoreWebView2 sender,
        CoreWebView2NavigationCompletedEventArgs args)
    {
        _navigationInProgress = false;
        if (!args.IsSuccess)
        {
            _diagnostics.Warning($"WebView2 navigation failed. Error={args.WebErrorStatus}");
            ShowNativeStatus(ViewModel.RuntimeStatus with
            {
                State = RailGptRuntimeState.Failed,
                Message = $"RailGPT 页面加载失败：{args.WebErrorStatus}",
            });
            return;
        }

        if (!ViewModel.RuntimeStatus.IsReady)
        {
            ShowNativeStatus(ViewModel.RuntimeStatus);
            return;
        }

        _documentReady = true;
        NativeStatusPanel.Visibility = Visibility.Collapsed;
        WebViewHost.Visibility = Visibility.Visible;
        SendConversationRequest(_pendingConversationId);
    }

    private void OnWebViewProcessFailed(
        CoreWebView2 sender,
        CoreWebView2ProcessFailedEventArgs args)
    {
        _diagnostics.Warning($"WebView2 process failed. Kind={args.ProcessFailedKind}; Reason={args.Reason}");
        _documentReady = false;

        if (!_processRecoveryAttempted && ViewModel.RuntimeStatus.IsReady)
        {
            _processRecoveryAttempted = true;
            ShowLoading("WebView2 进程已退出，正在恢复…");
            _ = RecoverWebViewObservedAsync();
            return;
        }

        ShowNativeStatus(ViewModel.RuntimeStatus with
        {
            State = RailGptRuntimeState.Failed,
            Message = "WebView2 进程连续异常退出，请打开日志后重试。",
        });
    }

    private async Task RecoverWebViewObservedAsync()
    {
        try
        {
            TearDownWebView();
            await EnsureWebViewAsync(ViewModel.RuntimeStatus);
        }
        catch (Exception ex)
        {
            _diagnostics.Error("WebView2 recovery failed.", ex);
            ShowNativeStatus(ViewModel.RuntimeStatus with
            {
                State = RailGptRuntimeState.Failed,
                Message = $"WebView2 恢复失败：{ex.Message}",
            });
        }
    }

    private void TearDownWebView()
    {
        if (_webView?.CoreWebView2 != null)
        {
            _webView.CoreWebView2.WebMessageReceived -= OnWebMessageReceived;
            _webView.CoreWebView2.NavigationCompleted -= OnNavigationCompleted;
            _webView.CoreWebView2.ProcessFailed -= OnWebViewProcessFailed;
        }
        if (_webView != null)
        {
            WebViewHost.Children.Remove(_webView);
            _webView.Close();
            _webView = null;
        }
        _webViewInitialized = false;
        _documentReady = false;
        _navigationInProgress = false;
    }

    private void SendConversationRequest(int? conversationId)
    {
        if (!_documentReady || !_webViewInitialized || conversationId is not int cid)
            return;

        _pendingRequestId = Guid.NewGuid().ToString("N");
        var payload = JsonSerializer.Serialize(new
        {
            type = "conversation.load",
            requestId = _pendingRequestId,
            conversationId = cid,
        });
        _webView?.CoreWebView2.PostWebMessageAsJson(payload);
    }

    private void OnWebMessageReceived(
        CoreWebView2 sender,
        CoreWebView2WebMessageReceivedEventArgs args)
    {
        try
        {
            using var document = JsonDocument.Parse(args.WebMessageAsJson);
            var root = document.RootElement;
            if (!root.TryGetProperty("type", out var typeElement))
                return;

            switch (typeElement.GetString())
            {
                case "open_railgo":
                    if (root.TryGetProperty("uri", out var uriElement))
                        OpenRailGoUri(uriElement.GetString());
                    break;

                case "conversations_changed":
                    _chatClient.NotifyConversationsChanged();
                    break;

                case "busy.changed":
                    if (root.TryGetProperty("busy", out var busyElement))
                        _chatClient.NotifyBusyChanged(busyElement.GetBoolean());
                    break;

                case "conversation.loaded":
                    if (MatchesPendingRequest(root))
                        _chatClient.NotifyConversationsChanged();
                    break;

                case "conversation.error":
                    if (MatchesPendingRequest(root))
                    {
                        var message = root.TryGetProperty("message", out var messageElement)
                            ? messageElement.GetString()
                            : "会话加载失败";
                        _diagnostics.Warning($"Web conversation load failed: {message}");
                        _ = ShowConversationErrorObservedAsync(message ?? "会话加载失败");
                    }
                    break;
            }
        }
        catch (Exception ex)
        {
            _diagnostics.Error("Invalid WebView bridge message.", ex);
        }
    }

    private async Task ShowConversationErrorObservedAsync(string message)
    {
        try
        {
            var dialog = new ContentDialog
            {
                XamlRoot = XamlRoot,
                Title = "无法切换会话",
                Content = message,
                CloseButtonText = "知道了",
            };
            await dialog.ShowAsync();
        }
        catch (Exception ex)
        {
            _diagnostics.Warning($"Conversation error dialog could not be displayed: {ex.Message}");
        }
    }

    private bool MatchesPendingRequest(JsonElement root)
        => root.TryGetProperty("requestId", out var requestElement) &&
           string.Equals(requestElement.GetString(), _pendingRequestId, StringComparison.Ordinal);

    private void OpenRailGoUri(string? value)
    {
        if (!Uri.TryCreate(value, UriKind.Absolute, out var uri) || uri.Scheme != "railgo")
            return;

        var resource = uri.Host.ToLowerInvariant();
        var segments = uri.AbsolutePath
            .Split('/', StringSplitOptions.RemoveEmptyEntries)
            .Select(Uri.UnescapeDataString)
            .ToArray();
        var identifier = segments.FirstOrDefault();
        if (resource == "train" && !string.IsNullOrWhiteSpace(identifier))
            _navigationService.NavigateTo(typeof(Train_NumberViewModel).FullName!, identifier);
        else if (resource == "station" && !string.IsNullOrWhiteSpace(identifier))
            _navigationService.NavigateTo(typeof(Station_InformationViewModel).FullName!, identifier);
        else if (resource == "route" && segments.Length >= 2)
            _navigationService.NavigateTo(typeof(StationToStationViewModel).FullName!, segments);
    }

    private void ShowLoading(string message)
    {
        WebViewHost.Visibility = Visibility.Collapsed;
        NativeStatusPanel.Visibility = Visibility.Visible;
        StatusProgress.Visibility = Visibility.Visible;
        StatusProgress.IsActive = true;
        StatusIcon.Visibility = Visibility.Collapsed;
        StatusMessage.Text = message;
        StatusDetails.Text = $"Runtime: {ViewModel.RuntimeDescription}";
        RetryButton.Visibility = Visibility.Collapsed;
        InstallWebViewButton.Visibility = Visibility.Collapsed;
    }

    private void ShowNativeStatus(RailGptRuntimeStatus status)
    {
        WebViewHost.Visibility = Visibility.Collapsed;
        NativeStatusPanel.Visibility = Visibility.Visible;
        StatusProgress.IsActive = status.State == RailGptRuntimeState.Starting;
        StatusProgress.Visibility = status.State == RailGptRuntimeState.Starting
            ? Visibility.Visible
            : Visibility.Collapsed;
        StatusIcon.Visibility = status.State == RailGptRuntimeState.Starting
            ? Visibility.Collapsed
            : Visibility.Visible;
        StatusMessage.Text = status.Message;
        StatusDetails.Text =
            $"状态: {status.State}\nRuntime: {status.RuntimePath}\n日志: {status.LogPath}" +
            (status.ExitCode is int code ? $"\n退出码: {code}" : string.Empty);
        RetryButton.Visibility = status.State is
            RailGptRuntimeState.UnsupportedArchitecture
            ? Visibility.Collapsed
            : Visibility.Visible;
        InstallWebViewButton.Visibility = status.State == RailGptRuntimeState.MissingWebView2
            ? Visibility.Visible
            : Visibility.Collapsed;
    }

    private async void OnRetryClick(object sender, RoutedEventArgs e)
    {
        try
        {
            ShowLoading("正在重试 RailGPT…");
            _processRecoveryAttempted = false;
            TearDownWebView();
            var result = await ViewModel.RetryAsync();
            await HandlePreparedObservedAsync(result);
        }
        catch (Exception ex)
        {
            _diagnostics.Error("RailGPT retry failed.", ex);
            ShowNativeStatus(ViewModel.RuntimeStatus with
            {
                State = RailGptRuntimeState.Failed,
                Message = $"重试失败：{ex.Message}",
            });
        }
    }

    private async void OnInstallWebViewClick(object sender, RoutedEventArgs e)
    {
        try
        {
            await Launcher.LaunchUriAsync(new Uri("https://go.microsoft.com/fwlink/p/?LinkId=2124703"));
        }
        catch (Exception ex)
        {
            _diagnostics.Error("Failed to open WebView2 download page.", ex);
        }
    }

    private async void OnOpenLogsClick(object sender, RoutedEventArgs e)
    {
        try
        {
            await Launcher.LaunchFolderPathAsync(_diagnostics.LogDirectory);
        }
        catch (Exception ex)
        {
            _diagnostics.Error("Failed to open diagnostics directory.", ex);
        }
    }
}
