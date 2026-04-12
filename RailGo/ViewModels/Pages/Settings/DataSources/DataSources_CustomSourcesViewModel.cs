using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Linq;
using System.Threading.Tasks;
using System.Windows.Input;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using CommunityToolkit.WinUI.UI.Controls;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using RailGo.Contracts.Services;
using RailGo.Core.Models.Settings;
using RailGo.Views.ContentDialogs;

namespace RailGo.ViewModels.Pages.Settings.DataSources;

public partial class DataSources_CustomSourcesViewModel : ObservableRecipient
{
    private readonly IDataSourceService _dataSourceService;

    [ObservableProperty]
    private DataSourceGroup? selectedItem; // ListView选中的数据源组

    [ObservableProperty]
    private ObservableCollection<DataSourceGroup> customSources = new(); // 所有自定义数据源组

    [ObservableProperty]
    private ObservableCollection<DataSourceMethod> selectingMethodItem; // ListView选中的方法加载出来的DataGrid信息

    [ObservableProperty]
    private DataSourceMethod selectedMethodItem; // ListView选中的方法加载出来的DataGrid中选中的项

    [ObservableProperty]
    private string currentItemName = "数据源组"; // 当前的方法名称

    [ObservableProperty]
    private bool isTitleEditing = false; // 是否正在编辑标题

    private string TitleEditingBackup;

    // 编辑时的ContentDialog
    [ObservableProperty]
    private bool methodModeSelectIsOnline; // ContentDialog中的选择的是否在线

    [ObservableProperty]
    private bool methodModeSelectIsOffline; // ContentDialog中的选择的是否离线

    [ObservableProperty]
    private object currentSelectSources_Editing = new(); // ContentDialog中的选择的数据源

    [ObservableProperty]
    private ObservableCollection<object> currentSources_Editing = new(); // ContentDialog中的所有的数据源

    // 可见性
    [ObservableProperty]
    private Visibility isContentOpen = Visibility.Collapsed;

    [ObservableProperty]
    private Visibility isContentClose = Visibility.Visible;

    [ObservableProperty]
    private Visibility isTitleyesEditing = Visibility.Collapsed; // 标题编辑确定取消可见性

    [ObservableProperty]
    private Visibility isTitlenotEditing = Visibility.Visible; // 标题编辑开始可见性

    // 新建数据源
    [ObservableProperty]
    private string newDataSourceGroupName;

    [ObservableProperty]
    private ObservableCollection<string> _availableModes = new() { "online", "offline" };

    private readonly List<DataSourceMethod> _predefinedMethods = new()
    {
        new DataSourceMethod { Name = "QueryTrainPreselect", Mode = "online", SourceName = "RailGoDefalt" },
        new DataSourceMethod { Name = "QueryTrainQuery", Mode = "online", SourceName = "RailGoDefalt" },
        new DataSourceMethod { Name = "QueryStationToStationQuery", Mode = "online", SourceName = "RailGoDefalt" },
        
        new DataSourceMethod { Name = "QueryStationPreselect", Mode = "online", SourceName = "RailGoDefalt" },
        new DataSourceMethod { Name = "QueryStationQuery", Mode = "online", SourceName = "RailGoDefalt" },
        new DataSourceMethod { Name = "QueryGetBigScreenData", Mode = "online", SourceName = "RailGoDefalt" },
        
        new DataSourceMethod { Name = "QueryEmuQuery", Mode = "online", SourceName = "RailGoDefalt" },
        new DataSourceMethod { Name = "QueryEmuAssignmentQuery", Mode = "online", SourceName = "RailGoDefalt" },
        
        new DataSourceMethod { Name = "QueryTrainDelay", Mode = "online", SourceName = "RailGoDefalt" },
        new DataSourceMethod { Name = "QueryPlatformInfo", Mode = "online", SourceName = "RailGoDefalt" },
        
        new DataSourceMethod { Name = "QueryDownloadEmuImage", Mode = "online", SourceName = "RailGoDefalt" }
    };

    public DataSources_CustomSourcesViewModel(IDataSourceService dataSourceService)
    {
        _dataSourceService = dataSourceService;
        _ = InitializeAsync();
    }

    private async Task InitializeAsync()
    {
        await LoadDataSourcesAsync();
    }

    [RelayCommand]
    private async Task LoadDataSourcesAsync()
    {
        CustomSources = await _dataSourceService.GetAllDataSourcesAsync();
    }

    [RelayCommand]
    private async void StartCreateNew()
    {
        var NewItem = new DataSourceGroup
        {
            Name = NewDataSourceGroupName,
            Data = new ObservableCollection<DataSourceMethod>(_predefinedMethods.Select(m => new DataSourceMethod
            {
                Name = m.Name,
                Mode = m.Mode,
                SourceName = m.SourceName
            }))
        };
        await _dataSourceService.SetDataSourceGroupAsync(NewItem);
        await LoadDataSourcesAsync();
        NewDataSourceGroupName = null;
    }

    [RelayCommand]
    private async Task SelectionItemAsync()
    {
        if (SelectedItem == null) return;

        SelectingMethodItem = null;
        CurrentItemName = null;

        SelectingMethodItem = SelectedItem.Data;
        CurrentItemName = SelectedItem.Name;
        IsContentOpen = Visibility.Visible;
        IsContentClose = Visibility.Collapsed;
    }

    [RelayCommand]
    private async Task SaveSelectedMethodAsync()
    {
        if (SelectingMethodItem == null) return;

        var SourceName = string.Empty;
        if (CurrentSelectSources_Editing is OnlineApiSource)
        {
            SourceName = (CurrentSelectSources_Editing as OnlineApiSource)?.Name;
        }
        if (CurrentSelectSources_Editing is LocalDatabaseSource)
        {
            SourceName = (CurrentSelectSources_Editing as LocalDatabaseSource)?.Name;
        }

        var Mode = string.Empty;
        if (MethodModeSelectIsOnline == true)
        {
            Mode = "online";
        }
        else if (MethodModeSelectIsOffline == true)
        {
            Mode = "offline";
        }
        else
        {
            Mode = SelectedMethodItem.Mode;
        }

        DataSourceMethod EditedMethod = new DataSourceMethod
        {
            Name = SelectedMethodItem.Name,
            Mode = Mode,
            SourceName = SourceName
        };
        await _dataSourceService.SetDataSourceMethodAsync(SelectedItem.Name, EditedMethod);
        await LoadDataSourcesAsync();

        SelectedItem = null;
        IsTitleEditing = false;
        CurrentItemName = "数据源组";
        SelectingMethodItem = null;
        IsContentOpen = Visibility.Collapsed;
        IsContentClose = Visibility.Visible;
    }

    [RelayCommand]
    private async Task SaveSelectedMethodDefaltAsync()
    {
        if (SelectingMethodItem == null) return;

        var SourceName = "RailGoDefalt";

        var Mode = string.Empty;
        if (MethodModeSelectIsOnline == true)
        {
            Mode = "online";
        }
        else if (MethodModeSelectIsOffline == true)
        {
            Mode = "offline";
        }
        else
        {
            Mode = SelectedMethodItem.Mode;
        }

        DataSourceMethod EditedMethod = new DataSourceMethod
        {
            Name = SelectedMethodItem.Name,
            Mode = Mode,
            SourceName = SourceName
        };
        await _dataSourceService.SetDataSourceMethodAsync(SelectedItem.Name, EditedMethod);
        await LoadDataSourcesAsync();

        SelectedItem = null;
        IsTitleEditing = false;
        CurrentItemName = "数据源组";
        SelectingMethodItem = null;
        IsContentOpen = Visibility.Collapsed;
        IsContentClose = Visibility.Visible;
    }

    [RelayCommand]
    private async Task DeleteSelectedAsync()
    {
        if (SelectedItem == null) return;

        CustomSources.Remove(SelectedItem);
        SelectedItem = null;
        IsTitleEditing = false;
        CurrentItemName = "数据源组";
        SelectingMethodItem = null;
        IsContentOpen = Visibility.Collapsed;
        IsContentClose = Visibility.Visible;

        await _dataSourceService.SaveDataSourcesToSettingsAsync(CustomSources);
        await LoadDataSourcesAsync();
    }

    [RelayCommand]
    private async Task EditSelectedItemAsync()
    {
        if (SelectedItem == null) return;
        TitleEditingBackup = CurrentItemName;
        IsTitlenotEditing = Visibility.Collapsed;
        IsTitleyesEditing = Visibility.Visible;
        IsTitleEditing = true;
    }

    [RelayCommand]
    private async Task EditSelectedItemOKAsync()
    {
        if (SelectedItem == null) return;

        IsTitleEditing = false;
        IsTitlenotEditing = Visibility.Visible;
        IsTitleyesEditing = Visibility.Collapsed;
        TitleEditingBackup = null;

        CustomSources.Remove(SelectedItem);
        var EditedItem = new DataSourceGroup
        {
            Name = CurrentItemName,
            Data = SelectingMethodItem
        };
        CustomSources.Add(EditedItem);

        SelectedItem = null;
        IsTitleEditing = false;
        CurrentItemName = "数据源组";
        SelectingMethodItem = null;
        IsContentOpen = Visibility.Collapsed;
        IsContentClose = Visibility.Visible;

        await _dataSourceService.SaveDataSourcesToSettingsAsync(CustomSources);
        await LoadDataSourcesAsync();
    }

    [RelayCommand]
    private async Task EditSelectedItemNOAsync()
    {
        if (SelectedItem == null) return;

        CurrentItemName = TitleEditingBackup;
        IsTitleEditing = false;
        IsTitlenotEditing = Visibility.Visible;
        IsTitleyesEditing = Visibility.Collapsed;
    }

    [RelayCommand]
    private async void SourcesLoading(string mode)
    {
        if (MethodModeSelectIsOnline || mode == "online")
        {
            var onlineApiSources = await _dataSourceService.GetOnlineApiSourcesAsync(); 
            CurrentSources_Editing = new ObservableCollection<object>(onlineApiSources.Cast<object>());
        }
        else if (MethodModeSelectIsOffline || mode == "offline")
        {
            var offlineDatabaseVersion = await _dataSourceService.GetLocalDatabaseSourcesAsync();
            CurrentSources_Editing = new ObservableCollection<object>(offlineDatabaseVersion.Cast<object>());
        }
    }
}