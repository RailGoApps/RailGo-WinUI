using System.Windows.Input;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using RailGo.Contracts.Services;
using RailGo.Helpers;
using Microsoft.UI.Xaml;
using Windows.System;

namespace RailGo.ViewModels.Pages.Settings;

public partial class SettingsViewModel : ObservableRecipient
{
    private readonly IThemeSelectorService _themeSelectorService;

    [ObservableProperty]
    private ElementTheme _elementTheme;

    [ObservableProperty]
    private string _versionDescription;

    public ICommand SwitchThemeCommand { get; }

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
    }

    private static string GetVersionDescription()
    {
        return $"{"AppDisplayName".GetLocalized()} - 0.1.4.0";
    }

    [RelayCommand]
    private async Task OpenWebsiteAsync()
    {
        await Launcher.LaunchUriAsync(new Uri("https://docs.railgo.dev/"));
    }

    [RelayCommand]
    private async Task OpenRailGoCenterAsync()
    {
        await Launcher.LaunchUriAsync(new Uri("https://center.zenglingkun.cn/"));
    }
}
