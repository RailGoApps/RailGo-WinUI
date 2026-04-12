using System.Reflection;
using System.Windows.Input;

using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;

using RailGo.Contracts.Services;
using RailGo.Helpers;

using Microsoft.UI.Xaml;

using Windows.ApplicationModel;
using Microsoft.UI.Xaml.Controls;
using RailGo.Services;
using RailGo.Core.Models.QueryDatas;
using RailGo.ViewModels.Pages.Shell;
using System.Collections.ObjectModel;
using RailGo.Core.Query.Online;
using RailGo.Core.Models.Settings;
using RailGo.Views.Windows;
using RailGo.ViewModels.Windows;
using Windows.Storage;
using RailGo.Views.Pages.Settings.DataSources;
using System.Diagnostics;
using Windows.Storage.Pickers;

namespace RailGo.ViewModels.Pages.Settings.DataSources;

public partial class DataSources_OnlineDatabasesViewModel : ObservableRecipient
{
    private readonly IDataSourceService _dataSourceService;
    public DataSources_OnlineDatabasesViewModel(IDataSourceService dataSourceService)
    {
        _dataSourceService = dataSourceService;
        _ = RefeshDBAllAsync();
    }
    public MainWindowViewModel progressBarVM = App.GetService<MainWindowViewModel>();

    [ObservableProperty]
    private VersionInfo remoteDBInfo = new VersionInfo() { Db = "未获取" };

    [ObservableProperty]
    private OfflineDatabaseVersion localDBInfo = new OfflineDatabaseVersion() { Version = "未下载" };

    [ObservableProperty]
    private string remoteDBRefreshedDate;

    [ObservableProperty]
    public InfoBarSeverity remoteDBInfoBarSeverity = InfoBarSeverity.Informational;

    [ObservableProperty]
    public InfoBarSeverity localDBInfoBarSeverity = InfoBarSeverity.Informational;

    [ObservableProperty]
    private string deleteDbResult = "操作未执行";

    [ObservableProperty]
    private string extractDbResult = "操作未执行";

    [ObservableProperty]
    private bool ifDeleteDBSettingsCardTeachingTipOpen;

    [ObservableProperty]
    private bool ifExtractDBSettingsCardTeachingTipOpen;

    [ObservableProperty]
    private bool ifDeleteExtractDBSettingsCardCan;

    [RelayCommand]
    public async Task GetRemoteDBInfoAsync()
    {
        try
        {
            progressBarVM.TaskIsInProgress = "Visible";
            RemoteDBInfo = await DBGetService.GetVersionInfoAsync();
            var DownloadDBWindowViewModel = App.GetService<GetOfflineDatabaseWindowViewModel>();
            DownloadDBWindowViewModel.SetRemoteDatabaaseVersion(RemoteDBInfo);
            RemoteDBRefreshedDate = DateTime.Now.ToString("yy.MM.dd HH:mm:ss");

            if (LocalDBInfo != null)
            {
                if (RemoteDBInfo.Db != LocalDBInfo.Version)
                {
                    RemoteDBInfoBarSeverity = InfoBarSeverity.Warning;
                }
                else
                {
                    RemoteDBInfoBarSeverity = InfoBarSeverity.Success;
                }
            }
        }
        catch (Exception ex)
        {
            RemoteDBInfoBarSeverity = InfoBarSeverity.Error;
            RemoteDBInfo = new VersionInfo() { Db = "获取失败" };
        }
        finally
        {
            progressBarVM.TaskIsInProgress = "Collapsed";
        }
    }

    [RelayCommand]
    public async Task GetLocalDBInfoAsync()
    {
        IfDeleteExtractDBSettingsCardCan = DBGetService.LocalDatabaseExists();
        try
        {
            progressBarVM.TaskIsInProgress = "Visible";
            LocalDBInfo = await _dataSourceService.GetOfflineDatabaseVersionAsync();

            if (LocalDBInfo == null || !DBGetService.LocalDatabaseExists())
            {
                LocalDBInfoBarSeverity = InfoBarSeverity.Warning;
                LocalDBInfo = new OfflineDatabaseVersion() { Version = "未下载", InstallDate = DateTime.MinValue };
            }
            else
            {
                if(LocalDBInfo.InstallDate != null && LocalDBInfo.Version != null && LocalDBInfo.Sequence != null)
                {
                    LocalDBInfoBarSeverity = InfoBarSeverity.Informational;
                }
            }
        }
        catch (Exception ex)
        {
            LocalDBInfoBarSeverity = InfoBarSeverity.Warning;
            LocalDBInfo = new OfflineDatabaseVersion() { Version = "获取失败或未下载", InstallDate = DateTime.MinValue };
        }
        finally
        {
            progressBarVM.TaskIsInProgress = "Collapsed";
        }
    }

    [RelayCommand]
    private async Task DownloadRemoteDBAsync()
    {
        var downloadDBWindow = App.GetService<GetOfflineDatabaseWindow>();
        downloadDBWindow.Activate();
    }

    [RelayCommand]
    public async Task RefeshDBAllAsync()
    {
        _ = GetRemoteDBInfoAsync();
        _ = GetLocalDBInfoAsync();
        var DataSourcesShell = App.GetService<DataSources_ShellViewModel>();
        DataSourcesShell.CheckAvailableModes();
    }

    [RelayCommand]
    public async Task DeleteDBAsync()
    {
        try
        {
            progressBarVM.TaskIsInProgress = "Visible";
            _ = DBGetService.DeleteLocalDatabase();
            _ = _dataSourceService.UpdateOfflineDatabaseVersionAsync(null, -1);
            _ = RefeshDBAllAsync();

            DeleteDbResult = "删除成功";
            IfDeleteExtractDBSettingsCardCan = false;
        }
        catch (Exception ex)
        {
            DeleteDbResult = "删除失败：" + ex.Message;
        }
        finally
        {
            IfDeleteDBSettingsCardTeachingTipOpen = true;
            progressBarVM.TaskIsInProgress = "Collapsed";
        }
    }

    [RelayCommand]
    public async Task CloseDeleteDBSettingsCardTeachingTipAsync()
    {
        DeleteDbResult = "操作未执行";
        IfDeleteDBSettingsCardTeachingTipOpen = false;
    }

    [RelayCommand]
    public async Task CloseExtractDBSettingsCardTeachingTipAsync()
    {
        ExtractDbResult = "操作未执行";
        IfExtractDBSettingsCardTeachingTipOpen = false;
    }

    [RelayCommand]
    public async Task ExtractDBAsync()
    {
        try
        {
            progressBarVM.TaskIsInProgress = "Visible";
            var savePicker = new FileSavePicker();
            var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(MainWindow.Instance);
            WinRT.Interop.InitializeWithWindow.Initialize(savePicker, hwnd);

            savePicker.SuggestedFileName = "railgo";
            savePicker.FileTypeChoices.Add("SQLite数据库", new List<string> { ".sqlite" });
            savePicker.DefaultFileExtension = ".sqlite";
            var destinationFile = await savePicker.PickSaveFileAsync();

            StorageFile sourceFile = await StorageFile.GetFileFromPathAsync(DBGetService.GetLocalDatabasePath());
            StorageFolder destinationFolder = await destinationFile.GetParentAsync();
            string destinationFileName = destinationFile.Name;
            await sourceFile.CopyAsync(destinationFolder, destinationFileName, NameCollisionOption.ReplaceExisting);

            ExtractDbResult = "导出成功";
            _ = RefeshDBAllAsync();
        }
        catch (Exception ex)
        {
            ExtractDbResult = "导出失败：" + ex.Message;
        }
        finally
        {
            IfExtractDBSettingsCardTeachingTipOpen = true;
            progressBarVM.TaskIsInProgress = "Collapsed";
        }
    }
}