using System.Net;
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using RailGo.Contracts.Services;
using RailGo.ViewModels.Pages.Chat;
using RailGo.ViewModels.Pages.Trains;

namespace RailGo.Views.Pages.Chat;

public sealed partial class ChatPage : Page
{
    public ChatViewModel ViewModel { get; }
    private readonly INavigationService _navigationService;

    public ChatPage()
    {
        ViewModel = App.GetService<ChatViewModel>();
        _navigationService = App.GetService<INavigationService>();
        InitializeComponent();

        ViewModel.BackendReady += OnBackendReady;
        _ = ShowFallbackAfterTimeoutAsync();
    }

    private async void OnBackendReady(string url)
    {
        try
        {
            await RailGPTWebView.EnsureCoreWebView2Async();
            RailGPTWebView.CoreWebView2.NavigationStarting += (_, args) =>
                System.Diagnostics.Debug.WriteLine($"[WebView2] Navigating to: {args.Uri}");
            RailGPTWebView.CoreWebView2.WebMessageReceived += OnWebMessageReceived;
            RailGPTWebView.CoreWebView2.Navigate(url);
        }
        catch (Exception ex)
        {
            ShowFallbackPage($"RailGPT navigation failed: {ex.Message}");
        }
    }

    private void OnWebMessageReceived(Microsoft.Web.WebView2.Core.CoreWebView2 sender,
        Microsoft.Web.WebView2.Core.CoreWebView2WebMessageReceivedEventArgs args)
    {
        try
        {
            using var document = JsonDocument.Parse(args.WebMessageAsJson);
            var root = document.RootElement;
            if (!root.TryGetProperty("type", out var type) || type.GetString() != "open_railgo")
                return;

            if (root.TryGetProperty("uri", out var uriElement))
                OpenRailGoUri(uriElement.GetString());
        }
        catch (JsonException ex)
        {
            System.Diagnostics.Debug.WriteLine($"[WebView2] Invalid bridge message: {ex.Message}");
        }
    }

    private void OpenRailGoUri(string? value)
    {
        if (!Uri.TryCreate(value, UriKind.Absolute, out var uri) || uri.Scheme != "railgo")
            return;

        var resource = uri.Host.ToLowerInvariant();
        var identifier = uri.AbsolutePath.Trim('/');
        if (resource == "train" && !string.IsNullOrWhiteSpace(identifier))
            _navigationService.NavigateTo(typeof(Train_NumberViewModel).FullName!, identifier);
    }

    private async Task ShowFallbackAfterTimeoutAsync()
    {
        await Task.Delay(TimeSpan.FromSeconds(15));
        if (ViewModel.IsBackendRunning)
            return;

        ShowFallbackPage($"RailGPT backend did not start. Expected runtime: {ViewModel.RuntimeDescription}");
    }

    private void ShowFallbackPage(string message)
    {
        DispatcherQueue.TryEnqueue(async () =>
        {
            try
            {
                await RailGPTWebView.EnsureCoreWebView2Async();
                var safeMessage = WebUtility.HtmlEncode(message);
                RailGPTWebView.CoreWebView2.NavigateToString(
                    "<html><body style='background:#f7f9fc;color:#202124;display:flex;align-items:center;" +
                    "justify-content:center;height:100vh;font-family:Segoe UI,sans-serif'>" +
                    $"<div style='text-align:center'><h2>RailGPT</h2><p>{safeMessage}</p>" +
                    $"<p style='color:#667085;font-size:0.9em'>地址: {WebUtility.HtmlEncode(ViewModel.BaseUrl)}</p></div></body></html>");
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[WebView2] Fallback failed: {ex.Message}");
            }
        });
    }

    protected override void OnNavigatedFrom(Microsoft.UI.Xaml.Navigation.NavigationEventArgs e)
    {
        ViewModel.BackendReady -= OnBackendReady;
        if (RailGPTWebView.CoreWebView2 != null)
            RailGPTWebView.CoreWebView2.WebMessageReceived -= OnWebMessageReceived;
        base.OnNavigatedFrom(e);
    }
}
