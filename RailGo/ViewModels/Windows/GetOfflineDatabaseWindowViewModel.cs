using System.Reflection;
using System.Windows.Input;

using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;

using RailGo.Contracts.Services;
using RailGo.Helpers;

using Microsoft.UI.Xaml;
using Microsoft.UI.Dispatching;

using Windows.ApplicationModel;
using RailGo.Core.Query.Online;
using Microsoft.UI.Xaml.Controls;
using RailGo.Core.Models.Settings;
using RailGo.Services;
using RailGo.ViewModels.Pages.Settings.DataSources;
using RailGo.ViewModels.Pages.Stations;
using RailGo.ViewModels.Pages.TrainEmus;
using RailGo.ViewModels.Pages.Trains;
using System;
using System.Diagnostics;

namespace RailGo.ViewModels.Windows;

public partial class GetOfflineDatabaseWindowViewModel : ObservableRecipient
{
    private DispatcherQueue _dispatcherQueue;
    private CancellationTokenSource _downloadCancellationTokenSource;
    private readonly IDataSourceService _dataSourceService;

    public GetOfflineDatabaseWindowViewModel(IDataSourceService dataSourceService, DispatcherQueue dispatcherQueue = null)
    {
        _dataSourceService = dataSourceService;
        _dispatcherQueue = dispatcherQueue ?? DispatcherQueue.GetForCurrentThread();
    }

    public event Action RequestCloseWindow;

    [ObservableProperty]
    private VersionInfo gotVersion = new VersionInfo { Db = "未知" };

    [ObservableProperty]
    private string infoBarButtonMode = "Waiting";

    [ObservableProperty]
    private bool ifGetRemoteVersion = false;

    [ObservableProperty]
    private string infoBarButtonContent = "开始下载";

    [ObservableProperty]
    private string infoBarContent = "未获取最新数据库版本";

    [ObservableProperty]
    private string infoBarTitle = "下载任务还没有开始";

    [ObservableProperty]
    private string progressBarVisibility = "Collapsed";

    [ObservableProperty]
    private bool progressBarShowError = false;

    [ObservableProperty]
    private bool progressBarIsIndeterminate = false;

    [ObservableProperty]
    private InfoBarSeverity infoBarSerityName = InfoBarSeverity.Informational;

    [ObservableProperty]
    public bool windowCloseConfirm;

    public void SetRemoteDatabaaseVersion(VersionInfo version)
    {
        GotVersion = version;
        InfoBarContent = "准备下载的版本：" + (version.Db ?? "未获取最新数据库版本");
        IfGetRemoteVersion = (version.Db != null);
    }

    [RelayCommand]
    private async Task InfoBarButtonClickAsync()
    {
        switch (InfoBarButtonMode)
        {
            case "Waiting":
                InfoBarSerityName = InfoBarSeverity.Informational;
                InfoBarButtonMode = "Canceled";
                InfoBarButtonContent = "终止并取消下载";
                InfoBarTitle = "下载中";
                InfoBarContent = "正在下载中，请耐心等待。。。";
                ProgressBarShowError = false;
                ProgressBarIsIndeterminate = true;
                ProgressBarVisibility = "Visible";
                StartDownload();
                break;
            case "Success":
                InfoBarButtonMode = "Waiting";
                InfoBarButtonContent = "开始下载";
                InfoBarTitle = "下载任务还没有开始";
                InfoBarContent = "准备下载的版本：" + (GotVersion.Db ?? "未获取最新数据库版本");
                InfoBarSerityName = InfoBarSeverity.Informational;
                ProgressBarShowError = false;
                ProgressBarIsIndeterminate = false;
                ProgressBarVisibility = "Collapsed";
                RequestCloseWindow?.Invoke();
                break;
            case "Canceled":
                CancelDownload();
                InfoBarButtonMode = "Waiting";
                InfoBarButtonContent = "开始下载";
                InfoBarTitle = "下载已经被取消";
                InfoBarContent = "下载已经被取消，准备重新下载\n准备下载的版本：" + (GotVersion.Db ?? "未获取最新数据库版本");
                InfoBarSerityName = InfoBarSeverity.Warning;
                ProgressBarShowError = false;
                ProgressBarIsIndeterminate = false;
                ProgressBarVisibility = "Collapsed";
                break;
        }
    }

    private void StartDownload()
    {
        _downloadCancellationTokenSource = new CancellationTokenSource();
        _ = Task.Run(async () =>
        {
            try
            {
                await DownloadDatabaseAsync(_downloadCancellationTokenSource.Token);
                if (!_downloadCancellationTokenSource.Token.IsCancellationRequested)
                {
                    _dispatcherQueue.TryEnqueue(() =>
                    {
                        InfoBarButtonMode = "Success";
                        InfoBarButtonContent = "确定关闭";
                        InfoBarContent = "离线数据库下载完成！";
                        InfoBarTitle = "下载成功";
                        InfoBarSerityName = InfoBarSeverity.Success;
                        ProgressBarShowError = false;
                        ProgressBarIsIndeterminate = false;
                        ProgressBarVisibility = "Collapsed";
                        WindowCloseConfirm = true;

                        var DataSources_OnlineDatabasesViewModel_Service = App.GetService<DataSources_OnlineDatabasesViewModel>();
                        _ = DataSources_OnlineDatabasesViewModel_Service.RefeshDBAllAsync();
                        DataSources_OnlineDatabasesViewModel_Service.LocalDBInfoBarSeverity = InfoBarSeverity.Success;
                    });
                    _ = _dataSourceService.UpdateOfflineDatabaseVersionAsync(GotVersion.Db, GotVersion.LatestDb);
                }
            }
            catch (OperationCanceledException)
            {
            }
            catch (Exception ex)
            {
                if (!_downloadCancellationTokenSource.Token.IsCancellationRequested)
                {
                    _dispatcherQueue.TryEnqueue(() =>
                    {
                        InfoBarContent = ex.Message;
                        InfoBarButtonMode = "Canceled";
                        InfoBarButtonContent = "取消下载";
                        InfoBarTitle = "下载失败";
                        InfoBarSerityName = InfoBarSeverity.Error;
                        ProgressBarShowError = true;
                        ProgressBarIsIndeterminate = false;
                        ProgressBarVisibility = "Collapsed";
                    });
                }
            }
        });
    }

    public void CancelDownload()
    {
        _downloadCancellationTokenSource?.Cancel();
    }

    private async Task DownloadDatabaseAsync(CancellationToken cancellationToken = default)
    {
        try
        {
            await DBGetService.DownloadAndSaveDatabaseAsync();
            cancellationToken.ThrowIfCancellationRequested();
        }
        catch (Exception ex)
        {
            Trace.WriteLine($"数据库下载失败: {ex.Message}");
            Trace.WriteLine(ex);
            throw;
        }
    }
}