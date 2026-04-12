using System.Reflection;
using System.Windows.Input;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.UI.Xaml;
using RailGo.Contracts.Services;
using RailGo.Core.Models.Settings;
using RailGo.Core.Query.Online;
using RailGo.Helpers;
using RailGo.Services;
using Windows.ApplicationModel;

namespace RailGo.ViewModels.Pages.Settings.DataSources;

public partial class DataSources_ShellViewModel : ObservableRecipient
{
    private readonly IDataSourceService _dataSourceService;

    [ObservableProperty]
    private string querySourceCode;

    [ObservableProperty]
    private bool allowCustomSource;

    public DataSources_ShellViewModel(IDataSourceService dataSourceService)
    {
        _dataSourceService = dataSourceService;
        _ = LoadSettingsAsync();
    }

    private async Task LoadSettingsAsync()
    {
        AllowCustomSource = await _dataSourceService.GetIfAllowCustomSourceAsync();
    }

    public async void CheckAvailableModes()
    {
        var MainViewModel = App.GetService<DataSources_MainViewModel>();
        MainViewModel.IfOfflineAvailable = DBGetService.LocalDatabaseExists();
        var QueryModeSelcted = await _dataSourceService.GetQueryModeAsync();
        if (QueryModeSelcted == "Offline")
        {
            if (DBGetService.LocalDatabaseExists())
            {
                MainViewModel.QuerySource = "离线（RailGo默认）";
                QuerySourceCode = "OfflineRailGO";
            }
            else
            {
                MainViewModel.QuerySource = "在线（RailGo默认）";
                QuerySourceCode = "OnlineRailGO";
            }
        }
    }
}