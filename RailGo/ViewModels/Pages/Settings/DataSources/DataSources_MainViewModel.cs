using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using RailGo.Contracts.Services;

namespace RailGo.ViewModels.Pages.Settings.DataSources;

public partial class DataSources_MainViewModel : ObservableObject
{
    private readonly IDataSourceService _dataSourceService;

    [ObservableProperty]
    private string? _queryMode;

    [ObservableProperty]
    private bool isLoading;

    [ObservableProperty]
    private bool isOnlineMode = true;

    [ObservableProperty]
    private bool isOfflineMode;

    [ObservableProperty]
    private string querySource = "在线（RailGo默认）";

    [ObservableProperty]
    private string offlineDatabasePath;

    public DataSources_MainViewModel(IDataSourceService dataSourceService)
    {
        _dataSourceService = dataSourceService;
        _ = LoadSettingsAsync();
    }

    private async Task LoadSettingsAsync()
    {
        try
        {
            IsLoading = true;
            QueryMode = await _dataSourceService.GetQueryModeAsync();

            if (QueryMode == "Offline")
            {
                IsOfflineMode = true;
                IsOnlineMode = false;
            }
            else
            {
                IsOnlineMode = true;
                IsOfflineMode = false;
            }

            // 读取已保存的离线数据库路径
            OfflineDatabasePath = await _dataSourceService.GetCustomDataSourceAddressAsync() ?? string.Empty;
            UpdateQuerySourceDisplay();
        }
        finally
        {
            IsLoading = false;
        }
    }

    private void UpdateQuerySourceDisplay()
    {
        if (IsOfflineMode)
        {
            QuerySource = "离线（RailGo默认）";
        }
        else
        {
            QuerySource = "在线（RailGo默认）";
        }
    }

    /// <summary>
    /// 切换到在线模式
    /// </summary>
    public async Task SwitchToOnlineModeAsync()
    {
        IsOnlineMode = true;
        IsOfflineMode = false;
        QueryMode = "Online";
        await _dataSourceService.SetQueryModeAsync("Online");
        UpdateQuerySourceDisplay();
    }

    /// <summary>
    /// 切换到离线模式（调用前应先确认）
    /// </summary>
    public async Task SwitchToOfflineModeAsync()
    {
        IsOfflineMode = true;
        IsOnlineMode = false;
        QueryMode = "Offline";
        await _dataSourceService.SetQueryModeAsync("Offline");
        UpdateQuerySourceDisplay();
    }

    /// <summary>
    /// 保存离线数据库路径
    /// </summary>
    public async Task SetOfflineDatabasePathAsync(string path)
    {
        OfflineDatabasePath = path;
        await _dataSourceService.SetCustomDataSourceAddressAsync(path);
    }
}
