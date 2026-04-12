using System.Collections.ObjectModel;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using RailGo.Contracts.Services;
using RailGo.Core.Query.Online;

namespace RailGo.ViewModels.Pages.Settings.DataSources;

public partial class DataSources_MainViewModel : ObservableObject
{
    private readonly IDataSourceService _dataSourceService;

    [ObservableProperty]
    private string? _queryMode;

    [ObservableProperty]
    private bool _isLoading;

    [ObservableProperty]
    private bool ifOfflineAvailable;

    [ObservableProperty]
    private string querySource;

    [ObservableProperty]
    private bool allowCustomSource;

    [ObservableProperty]
    private ObservableCollection<string>? dataSourceGroups;

    [ObservableProperty]
    private string? selectedDataSourceGroup;

    [ObservableProperty]
    private bool ifCompleteOnOfflineUnsupport;

    [ObservableProperty]
    private bool ifCompleteOnOnlineFailed;

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
            AllowCustomSource = await _dataSourceService.GetIfAllowCustomSourceAsync();
            DataSourceGroups = await _dataSourceService.GetAllGroupNamesAsync();
            SelectedDataSourceGroup = await _dataSourceService.GetSelectedDataSourceAsync();
            IfCompleteOnOfflineUnsupport = await _dataSourceService.GetOfflineComplementOnlineAsync();
            IfCompleteOnOnlineFailed = await _dataSourceService.GetOnlineFallbackToOfflineAsync();
            UpdateQuerySourceDisplay();
        }
        finally
        {
            IsLoading = false;
        }
    }

    private void UpdateQuerySourceDisplay()
    {
        if (QueryMode == "Offline")
        {
            QuerySource = "离线（RailGo默认）";
        }
        else if (QueryMode == "Online")
        {
            QuerySource = "在线（RailGo默认）";
        }
        else if (QueryMode == "Custom")
        {
            if (AllowCustomSource)
            {
                if (!string.IsNullOrEmpty(SelectedDataSourceGroup))
                {
                    QuerySource = $"自定义数据源：{SelectedDataSourceGroup}";
                }
                else
                {
                    QuerySource = "自定义数据源：未选择";
                }
            }
            else
            {
                QuerySource = "在线（RailGo默认）(选择的查询源被禁用，使用默认）";
            }
        }
        else
        {
            QuerySource = "Unkown";
        }
    }

    partial void OnQueryModeChanged(string? value)
    {
        if (!IsLoading && !string.IsNullOrEmpty(value))
        {
            _ = _dataSourceService.SetQueryModeAsync(value);
            UpdateQuerySourceDisplay();
        }
    }

    partial void OnAllowCustomSourceChanged(bool value)
    {
        if (!IsLoading)
        {
            _ = _dataSourceService.SetIfAllowCustomSourceAsync(value);
            if (QueryMode == "Custom")
            {
                _ = _dataSourceService.SetQueryModeAsync("Online");
                QueryMode = "Online";
            }
            UpdateQuerySourceDisplay();
        }
    }

    partial void OnSelectedDataSourceGroupChanged(string? value)
    {
        if (!IsLoading && !string.IsNullOrEmpty(value))
        {
            _ = _dataSourceService.SetSelectedDataSourceAsync(value);
            UpdateQuerySourceDisplay();
        }
    }

    partial void OnIfCompleteOnOfflineUnsupportChanged(bool value)
    {
        if (!IsLoading && value != null)
        {
            _ = _dataSourceService.SetOfflineComplementOnlineAsync(value);
        }
    }

    partial void OnIfCompleteOnOnlineFailedChanged(bool value)
    {
        if (!IsLoading && value != null)
        {
            _ = _dataSourceService.SetOnlineFallbackToOfflineAsync(value);
        }
    }
}