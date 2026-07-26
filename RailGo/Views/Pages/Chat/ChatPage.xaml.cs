using Microsoft.UI.Xaml.Controls;
using RailGo.AI.Services;
using RailGo.ViewModels.Pages.Chat;

namespace RailGo.Views.Pages.Chat;

public sealed partial class ChatPage : Page
{
    public ChatViewModel ViewModel { get; }

    public ChatPage()
    {
        ViewModel = App.GetService<ChatViewModel>();
        InitializeComponent();

        // Hook WebView2 navigation when backend is ready, with error fallback
        ViewModel.BackendReady += async url =>
        {
            try
            {
                await RailGPTWebView.EnsureCoreWebView2Async();
                RailGPTWebView.CoreWebView2.Navigate(url);

                // Also register for navigation errors in WebView2
                RailGPTWebView.CoreWebView2.NavigationStarting += (_, args) =>
                {
                    System.Diagnostics.Debug.WriteLine($"[WebView2] Navigating to: {args.Uri}");
                };
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[WebView2] Failed to load: {ex.Message}");
                ShowFallbackPage($"导航失败: {ex.Message}");
            }
        };

        // Fallback: if backend never becomes ready, show error after 10s
        _ = ShowFallbackAfterTimeout();
    }

    private async Task ShowFallbackAfterTimeout()
    {
        await Task.Delay(10_000);
        if (!ViewModel.IsBackendRunning)
        {
            try
            {
                await RailGPTWebView.EnsureCoreWebView2Async();
                RailGPTWebView.CoreWebView2.NavigateToString(
                    "<html><body style='background:#1e1e2e;color:#cdd6f4;display:flex;align-items:center;" +
                    "justify-content:center;height:100vh;font-family:sans-serif'>" +
                    "<div style='text-align:center'><h2>RailGPT</h2>" +
                    "<p>后端服务启动失败，请检查 Python 环境和 RailGPT 项目。</p>" +
                    $"<p style='color:#a6adc8;font-size:0.9em'>端口: 5033</p></div></body></html>");
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[WebView2] Fallback error: {ex.Message}");
            }
        }
    }

    private void ShowFallbackPage(string message)
    {
        _ = Task.Run(async () =>
        {
            try
            {
                await RailGPTWebView.EnsureCoreWebView2Async();
                RailGPTWebView.CoreWebView2.NavigateToString(
                    $"<html><body style='background:#1e1e2e;color:#cdd6f4;display:flex;align-items:center;" +
                    $"justify-content:center;height:100vh;font-family:sans-serif'>" +
                    $"<div style='text-align:center'><h2>RailGPT</h2>" +
                    $"<p>{message}</p></div></body></html>");
            }
            catch { }
        });
    }
}
