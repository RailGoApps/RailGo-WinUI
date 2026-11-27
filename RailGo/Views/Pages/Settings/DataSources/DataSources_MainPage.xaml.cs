using System.Diagnostics;
using System.IO;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using RailGo.Core.Query.Online;
using RailGo.ViewModels.Pages.Settings.DataSources;
using Windows.Storage;
using Windows.Storage.Pickers;

namespace RailGo.Views.Pages.Settings.DataSources;

public sealed partial class DataSources_MainPage : Page
{
    public DataSources_MainViewModel ViewModel
    {
        get;
    }

    public DataSources_MainPage()
    {
        ViewModel = App.GetService<DataSources_MainViewModel>();
        InitializeComponent();
    }

    private async void OnlineModeRadio_Checked(object sender, RoutedEventArgs e)
    {
        if (ViewModel.IsLoading)
        {
            return;
        }
        await ViewModel.SwitchToOnlineModeAsync();
    }

    private async void OfflineModeRadio_Checked(object sender, RoutedEventArgs e)
    {
        if (ViewModel.IsLoading)
        {
            return;
        }

        // 切换到离线模式前弹出确认对话框
        var result = await SwitchToOfflineDialog.ShowAsync();
        if (result == ContentDialogResult.Primary)
        {
            await ViewModel.SwitchToOfflineModeAsync();
        }
        else
        {
            // 用户取消，恢复在线模式选中状态
            ViewModel.IsOfflineMode = false;
            ViewModel.IsOnlineMode = true;
        }
    }

    private async void SelectDatabaseFile_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var picker = new FileOpenPicker();
            picker.FileTypeFilter.Add(".sqlite");
            picker.FileTypeFilter.Add(".db");

            var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(App.MainWindow);
            WinRT.Interop.InitializeWithWindow.Initialize(picker, hwnd);

            var pickedFile = await picker.PickSingleFileAsync();
            if (pickedFile is null)
            {
                return;
            }

            // 将选中的数据库文件复制到应用默认位置 ApplicationData/railgo.sqlite
            var destinationPath = DBGetService.GetLocalDatabasePath();
            var destinationDir = Path.GetDirectoryName(destinationPath);
            if (!string.IsNullOrEmpty(destinationDir) && !Directory.Exists(destinationDir))
            {
                Directory.CreateDirectory(destinationDir);
            }

            File.Copy(pickedFile.Path, destinationPath, overwrite: true);

            // 保存用户选择的路径用于显示
            await ViewModel.SetOfflineDatabasePathAsync(pickedFile.Path);
        }
        catch (Exception ex)
        {
            Debug.WriteLine(ex);
        }
    }

    private void DownloadDatabase_Click(object sender, RoutedEventArgs e)
    {
        // 跳转到浏览器下载离线数据库
        var url = "https://update.railgo.zenglingkun.cn/update/db";
        _ = global::Windows.System.Launcher.LaunchUriAsync(new global::System.Uri(url));
    }
}
