using RailGo.Contracts.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using RailGo.ViewModels.Pages.Settings;
using RailGo.Views.Pages.Settings.DataSources;
using Windows.System;
using Windows.ApplicationModel.DataTransfer;

namespace RailGo.Views.Pages.Settings;

public sealed partial class SettingsPage : Page
{
    private readonly IBackgroundImageService _backgroundImageService;

    public SettingsViewModel ViewModel { get; }

    public SettingsPage()
    {
        ViewModel = App.GetService<SettingsViewModel>();
        _backgroundImageService = App.GetService<IBackgroundImageService>();
        InitializeComponent();
        this.Loaded += OnLoad;
        Unloaded += OnUnloaded;
    }

    public void OnLoad(object sender, RoutedEventArgs e)
    {
        InitializeThemeComboBox();
        UpdateBackgroundImageUiState();
        _backgroundImageService.BackgroundImageChanged -= OnBackgroundImageChanged;
        _backgroundImageService.BackgroundImageChanged += OnBackgroundImageChanged;

        // Load AI settings from Python backend
        _ = LoadAISettingsAsync();
    }

    private void InitializeThemeComboBox()
    {
        if (ViewModel?.ElementTheme != null)
        {
            var theme = ViewModel.ElementTheme.ToString();
            foreach (ComboBoxItem item in ThemeComboBox.Items)
            {
                if (item.Tag?.ToString() == theme)
                {
                    ThemeComboBox.SelectedItem = item;
                    break;
                }
            }
        }
    }

    private void ThemeComboBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (ThemeComboBox.SelectedItem is ComboBoxItem selectedItem)
        {
            var themeString = selectedItem.Tag?.ToString();
            if (Enum.TryParse<ElementTheme>(themeString, out var theme))
            {
                if (ViewModel?.ElementTheme != theme)
                {
                    ViewModel.SwitchThemeCommand.Execute(theme);
                }
            }
        }
    }

    private void OnDataSourcesSettingsCardClicked(object sender, RoutedEventArgs e)
    {
        DataSources_ShellPage page = new();
        TabViewItem tabViewItem = new()
        {
            Header = "数据源设置面板",
            Content = page,
            CanDrag = true,
            IconSource = new FontIconSource() { Glyph = "\uE7C0" }
        };
        MainWindow.Instance.MainTabView.TabItems.Add(tabViewItem);
        MainWindow.Instance.MainTabView.SelectedItem = tabViewItem;
    }

    private void OnUnloaded(object sender, RoutedEventArgs e)
    {
        _backgroundImageService.BackgroundImageChanged -= OnBackgroundImageChanged;
    }

    private async void OnChooseBackgroundImageClicked(object sender, RoutedEventArgs e)
    {
        HideBackgroundErrorInfoBar();
        var isSuccess = await _backgroundImageService.PickAndSetBackgroundImageAsync();
        if (!isSuccess)
        {
            if (!string.IsNullOrWhiteSpace(_backgroundImageService.LastErrorMessage))
            {
                ShowBackgroundErrorInfoBar(_backgroundImageService.LastErrorMessage);
            }
            return;
        }
        UpdateBackgroundImageUiState();
    }

    private async void OnClearBackgroundImageClicked(object sender, RoutedEventArgs e)
    {
        HideBackgroundErrorInfoBar();
        try
        {
            await _backgroundImageService.ClearBackgroundImageAsync();
            UpdateBackgroundImageUiState();
        }
        catch (Exception ex)
        {
            ShowBackgroundErrorInfoBar($"{ex.GetType().Name}: {ex.Message}");
        }
    }

    private void OnBackgroundImageChanged(object? sender, string? imagePath)
    {
        if (DispatcherQueue.HasThreadAccess)
        {
            UpdateBackgroundImageUiState();
            return;
        }
        DispatcherQueue.TryEnqueue(UpdateBackgroundImageUiState);
    }

    private void UpdateBackgroundImageUiState()
    {
        var imagePath = _backgroundImageService.BackgroundImagePath;
        var hasCustomBackground = !string.IsNullOrWhiteSpace(imagePath);
        BackgroundImagePathTextBlock.Text = hasCustomBackground
            ? $"当前：{imagePath}"
            : "当前：默认背景";
        ClearBackgroundButton.IsEnabled = hasCustomBackground;
    }

    private void ShowBackgroundErrorInfoBar(string message)
    {
        BackgroundErrorInfoBar.Message = message;
        BackgroundErrorInfoBar.IsOpen = true;
    }

    private void HideBackgroundErrorInfoBar()
    {
        BackgroundErrorInfoBar.IsOpen = false;
        BackgroundErrorInfoBar.Message = string.Empty;
    }

    /// <summary>
    /// 打开官网
    /// </summary>
    private async void OnOpenWebsiteClicked(object sender, RoutedEventArgs e)
    {
        await Launcher.LaunchUriAsync(new Uri("https://railgo.dev/"));
    }

    /// <summary>
    /// 打开 RailGo Center
    /// </summary>
    private async void OnOpenRailGoCenterClicked(object sender, RoutedEventArgs e)
    {
        // 使用 WebView2 嵌入方式（位于系统浏览器打开后）
        try
        {
            await Launcher.LaunchUriAsync(new Uri("https://center.zenglingkun.cn/"));
        }
        catch
        {
            // 静默处理
        }
    }

    /// <summary>
    /// 加入交流群
    /// </summary>
    private async void OnJoinGroupClicked(object sender, RoutedEventArgs e)
    {
        await JoinGroupDialog.ShowAsync();
    }

    /// <summary>
    /// 复制群号
    /// </summary>
    private void OnCopyGroupNumberClicked(ContentDialog sender, ContentDialogButtonClickEventArgs args)
    {
        var dataPackage = new DataPackage();
        dataPackage.SetText("652032716");
        Clipboard.SetContent(dataPackage);
    }

    // ============================================================
    // AI / RailGPT API Settings handlers
    // ============================================================

    private async Task LoadAISettingsAsync()
    {
        await ViewModel.LoadAISettingsAsync();
        RefreshAIStatusDisplay();
    }

    private void RefreshAIStatusDisplay()
    {
        // Primary key status
        if (!string.IsNullOrEmpty(ViewModel.MaskedPrimaryKeyDisplay))
            PrimaryKeyStatus.Text = $"已配置: {ViewModel.MaskedPrimaryKeyDisplay}";
        else
            PrimaryKeyStatus.Text = "未配置";

        // Thinking key status
        if (!string.IsNullOrEmpty(ViewModel.MaskedThinkingKeyDisplay))
            ThinkingKeyStatus.Text = $"已配置: {ViewModel.MaskedThinkingKeyDisplay}";
        else
            ThinkingKeyStatus.Text = "未配置，将自动复用主对话 Key";

        // Config path
        ConfigPathText.Text = "%APPDATA%/RailGPT/settings.enc";

        // InfoBar
        if (ViewModel.HasApiKeyConfigured)
        {
            AISettingsInfoBar.Severity = Microsoft.UI.Xaml.Controls.InfoBarSeverity.Success;
            AISettingsInfoBar.Title = "已配置";
            AISettingsInfoBar.Message = $"Provider: {ViewModel.SelectedProvider} | 更新于: {ViewModel.AiConfigUpdatedAt}";
            AISettingsInfoBar.IsOpen = true;
        }
        else
        {
            AISettingsInfoBar.Severity = Microsoft.UI.Xaml.Controls.InfoBarSeverity.Warning;
            AISettingsInfoBar.Title = "未配置 API Key";
            AISettingsInfoBar.Message = "请输入 API Key 以启用 RailGPT 对话功能";
            AISettingsInfoBar.IsOpen = true;
        }
    }

    private async void OnSavePrimaryKeyClicked(object sender, RoutedEventArgs e)
    {
        var key = PrimaryApiKeyBox.Password?.Trim();
        if (string.IsNullOrEmpty(key))
        {
            AISettingsInfoBar.Severity = Microsoft.UI.Xaml.Controls.InfoBarSeverity.Error;
            AISettingsInfoBar.Title = "输入为空";
            AISettingsInfoBar.Message = "请输入主对话 API Key";
            AISettingsInfoBar.IsOpen = true;
            return;
        }

        SavePrimaryKeyBtn.IsEnabled = false;
        try
        {
            ViewModel.PrimaryApiKeyInput = key;
            ViewModel.ThinkingApiKeyInput = ThinkingApiKeyBox.Password?.Trim() ?? "";
            await ViewModel.SaveAISettingsAsync();
            PrimaryApiKeyBox.Password = "";
            RefreshAIStatusDisplay();
        }
        finally
        {
            SavePrimaryKeyBtn.IsEnabled = true;
        }
    }

    private async void OnClearPrimaryKeyClicked(object sender, RoutedEventArgs e)
    {
        ClearPrimaryKeyBtn.IsEnabled = false;
        try
        {
            await ViewModel.ClearApiKeyAsync("primary");
            RefreshAIStatusDisplay();
        }
        finally
        {
            ClearPrimaryKeyBtn.IsEnabled = true;
        }
    }

    private async void OnSaveThinkingKeyClicked(object sender, RoutedEventArgs e)
    {
        var key = ThinkingApiKeyBox.Password?.Trim();
        if (string.IsNullOrEmpty(key))
        {
            AISettingsInfoBar.Severity = Microsoft.UI.Xaml.Controls.InfoBarSeverity.Error;
            AISettingsInfoBar.Title = "输入为空";
            AISettingsInfoBar.Message = "请输入 Thinker API Key";
            AISettingsInfoBar.IsOpen = true;
            return;
        }

        SaveThinkingKeyBtn.IsEnabled = false;
        try
        {
            ViewModel.ThinkingApiKeyInput = key;
            ViewModel.PrimaryApiKeyInput = PrimaryApiKeyBox.Password?.Trim() ?? "";
            await ViewModel.SaveAISettingsAsync();
            ThinkingApiKeyBox.Password = "";
            RefreshAIStatusDisplay();
        }
        finally
        {
            SaveThinkingKeyBtn.IsEnabled = true;
        }
    }

    private async void OnClearThinkingKeyClicked(object sender, RoutedEventArgs e)
    {
        ClearThinkingKeyBtn.IsEnabled = false;
        try
        {
            await ViewModel.ClearApiKeyAsync("thinking");
            RefreshAIStatusDisplay();
        }
        finally
        {
            ClearThinkingKeyBtn.IsEnabled = true;
        }
    }

    private void ProviderComboBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (ProviderComboBox.SelectedItem is ComboBoxItem item && item.Tag != null)
            ViewModel.SelectedProvider = item.Tag.ToString()!;
    }
}
