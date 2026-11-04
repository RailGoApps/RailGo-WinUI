using System.Reflection;
using System.Windows.Input;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using RailGo.AI.Models;
using RailGo.AI.Services;
using RailGo.Contracts.Services;
using RailGo.Helpers;
using Microsoft.UI.Xaml;
using Windows.ApplicationModel;
using Windows.System;

namespace RailGo.ViewModels.Pages.Settings;

public partial class SettingsViewModel : ObservableRecipient
{
    private readonly IThemeSelectorService _themeSelectorService;
    private readonly AISettingsService? _aiSettingsService;

    // ---- Theme ----
    [ObservableProperty]
    private ElementTheme _elementTheme;

    [ObservableProperty]
    private string _versionDescription;

    public ICommand SwitchThemeCommand { get; }

    // ---- AI / RailGPT API Settings ----
    [ObservableProperty]
    private bool _isAISettingsLoaded;

    [ObservableProperty]
    private bool _isAISettingsLoading;

    [ObservableProperty]
    private string _aiStatusMessage = "";

    [ObservableProperty]
    private string _selectedProvider = "deepseek";

    [ObservableProperty]
    private string _primaryApiKeyInput = "";

    [ObservableProperty]
    private string _thinkingApiKeyInput = "";

    [ObservableProperty]
    private string _maskedPrimaryKeyDisplay = "";

    [ObservableProperty]
    private string _maskedThinkingKeyDisplay = "";

    [ObservableProperty]
    private bool _hasApiKeyConfigured;

    [ObservableProperty]
    private bool _isAISaving;

    [ObservableProperty]
    private string _aiConfigUpdatedAt = "";

    public ICommand LoadAISettingsCommand { get; }
    public ICommand SaveAISettingsCommand { get; }
    public ICommand ClearPrimaryApiKeyCommand { get; }
    public ICommand ClearThinkingApiKeyCommand { get; }

    public SettingsViewModel(IThemeSelectorService themeSelectorService)
    {
        _themeSelectorService = themeSelectorService;
        _elementTheme = _themeSelectorService.Theme;
        _versionDescription = GetVersionDescription();

        SwitchThemeCommand = new RelayCommand<ElementTheme>(
            async (param) =>
            {
                if (ElementTheme != param)
                {
                    ElementTheme = param;
                    await _themeSelectorService.SetThemeAsync(param);
                }
            });

        // AI settings service is optional — may be null if not registered
        _aiSettingsService = App.GetService<AISettingsService>();

        LoadAISettingsCommand = new RelayCommand(async () => await LoadAISettingsAsync());
        SaveAISettingsCommand = new RelayCommand(async () => await SaveAISettingsAsync());
        ClearPrimaryApiKeyCommand = new RelayCommand(async () => await ClearApiKeyAsync("primary"));
        ClearThinkingApiKeyCommand = new RelayCommand(async () => await ClearApiKeyAsync("thinking"));
    }

    // ---- AI Settings logic ----

    /// <summary>
    /// Fetch current API settings from the RailGPT Python backend.
    /// </summary>
    public async Task LoadAISettingsAsync()
    {
        if (_aiSettingsService == null) return;

        IsAISettingsLoading = true;
        AiStatusMessage = "正在读取 RailGPT 设置…";

        try
        {
            var payload = await _aiSettingsService.GetSettingsAsync();
            if (payload == null)
            {
                AiStatusMessage = "RailGPT 后端未连接，请稍后重试";
                IsAISettingsLoaded = false;
                return;
            }

            SelectedProvider = payload.Provider;
            HasApiKeyConfigured = payload.HasApiKey;
            MaskedPrimaryKeyDisplay = payload.MaskedPrimaryApiKey;
            MaskedThinkingKeyDisplay = payload.MaskedThinkingApiKey;
            AiConfigUpdatedAt = payload.UpdatedAt;
            IsAISettingsLoaded = true;
            AiStatusMessage = payload.HasApiKey
                ? $"已配置 ({payload.Provider})"
                : "未配置 API Key";
        }
        catch (Exception ex)
        {
            AiStatusMessage = $"读取失败: {ex.Message}";
            IsAISettingsLoaded = false;
        }
        finally
        {
            IsAISettingsLoading = false;
            PrimaryApiKeyInput = "";
            ThinkingApiKeyInput = "";
        }
    }

    /// <summary>
    /// Save API key settings to the RailGPT Python backend.
    /// </summary>
    public async Task SaveAISettingsAsync()
    {
        if (_aiSettingsService == null || IsAISaving) return;

        var primary = PrimaryApiKeyInput?.Trim();
        var thinking = ThinkingApiKeyInput?.Trim();
        if (string.IsNullOrEmpty(primary) && string.IsNullOrEmpty(thinking))
        {
            AiStatusMessage = "请至少输入一个 API Key";
            return;
        }

        IsAISaving = true;
        AiStatusMessage = "正在保存…";

        try
        {
            var payload = await _aiSettingsService.SaveApiSettingsAsync(
                SelectedProvider,
                string.IsNullOrEmpty(primary) ? null : primary,
                string.IsNullOrEmpty(thinking) ? null : thinking);

            if (payload != null)
            {
                HasApiKeyConfigured = payload.HasApiKey;
                MaskedPrimaryKeyDisplay = payload.MaskedPrimaryApiKey;
                MaskedThinkingKeyDisplay = payload.MaskedThinkingApiKey;
                AiConfigUpdatedAt = payload.UpdatedAt;
                AiStatusMessage = "API 设置已保存 ✓";
            }
        }
        catch (Exception ex)
        {
            AiStatusMessage = $"保存失败: {ex.Message}";
        }
        finally
        {
            IsAISaving = false;
            PrimaryApiKeyInput = "";
            ThinkingApiKeyInput = "";
        }
    }

    /// <summary>
    /// Delete a specific API key slot.
    /// </summary>
    public async Task ClearApiKeyAsync(string slot)
    {
        if (_aiSettingsService == null) return;

        IsAISaving = true;
        AiStatusMessage = $"正在删除 {slot} API Key…";

        try
        {
            var payload = await _aiSettingsService.DeleteApiKeyAsync(slot);
            if (payload != null)
            {
                HasApiKeyConfigured = payload.HasApiKey;
                MaskedPrimaryKeyDisplay = payload.MaskedPrimaryApiKey;
                MaskedThinkingKeyDisplay = payload.MaskedThinkingApiKey;
                AiConfigUpdatedAt = payload.UpdatedAt;
                AiStatusMessage = $"{slot} API Key 已删除";
            }
        }
        catch (Exception ex)
        {
            AiStatusMessage = $"删除失败: {ex.Message}";
        }
        finally
        {
            IsAISaving = false;
        }
    }

    private static string GetVersionDescription()
    {
        Version version;
        if (RuntimeHelper.IsMSIX)
        {
            var packageVersion = Package.Current.Id.Version;
            version = new(packageVersion.Major, packageVersion.Minor, packageVersion.Build, packageVersion.Revision);
        }
        else
        {
            version = Assembly.GetExecutingAssembly().GetName().Version!;
        }

        return $"{"AppDisplayName".GetLocalized()} - {version.Major}.{version.Minor}.{version.Build}.{version.Revision}";
    }

    [RelayCommand]
    private async Task OpenWebsiteAsync()
    {
        await Launcher.LaunchUriAsync(new Uri("https://railgo.dev/"));
    }

    [RelayCommand]
    private async Task OpenRailGoCenterAsync()
    {
        await Launcher.LaunchUriAsync(new Uri("https://center.zenglingkun.cn/"));
    }
}
