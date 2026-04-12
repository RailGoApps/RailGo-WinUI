using System.Collections.ObjectModel;
using System.Diagnostics;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using RailGo.Core.Models;
using RailGo.Core.Models.QueryDatas;
using RailGo.Services;
using Windows.Storage;
using Windows.Storage.Pickers;
using RailGo.ViewModels.Pages.Shell;

namespace RailGo.ViewModels.Pages.TrainEmus;

public partial class EMU_RoutingDetailsViewModel : ObservableRecipient
{
    public MainWindowViewModel progressBarVM = App.GetService<MainWindowViewModel>();

    [ObservableProperty]
    public ObservableCollection<EmuOperation> trainEmuInfos = new();

    [ObservableProperty]
    private bool isLoading;

    [ObservableProperty]
    private bool ifCanDownload = false;

    [ObservableProperty]
    private string trainEmuModel;

    [ObservableProperty]
    private string trainEmuCode;

    [ObservableProperty]
    private string trainBelong;

    [ObservableProperty]
    private string trainMaker;

    [ObservableProperty]
    private ImageSource emuImageSource;

    private byte[] imageBytes;

    public EMU_RoutingDetailsViewModel()
    {
    }

    [RelayCommand]
    private async Task SearchEmuDetailsAsync(EmuOperation DataFromLast)
    {
        try
        {
            IsLoading = true;
            progressBarVM.TaskIsInProgress = "Visible";

            TrainEmuModel = DataFromLast.EmuNoModel;
            TrainEmuCode = DataFromLast.EmuNoCode;

            // 调用 API 进行搜索
            var queryService = App.GetService<QueryService>();
            var TrainEmuInfosTask = queryService.QueryEmuQueryAsync("emu", DataFromLast.EmuNo);
            var TrainEmuFromWhereAllTask = queryService.QueryEmuAssignmentQueryAsync("trainSerialNumber", DataFromLast.EmuNoCode);
            var imageBytesTask = queryService.QueryDownloadEmuImageAsync(DataFromLast.EmuNoModel);
            await Task.WhenAll(TrainEmuInfosTask, TrainEmuFromWhereAllTask, imageBytesTask);
            TrainEmuInfos = TrainEmuInfosTask.Result;
            var TrainEmuFromWhereAll = TrainEmuFromWhereAllTask.Result;
            imageBytes = imageBytesTask.Result;

            var targetEmu = FilterByTrainModel(TrainEmuFromWhereAll, DataFromLast.EmuNoModel);
            TrainBelong = $"{targetEmu.Bureau ?? "未知"} {targetEmu.Department ?? "未知"}段";
            TrainMaker = $"{targetEmu.Manufacturer ?? "未知"} 制造";

            if (imageBytes != null && imageBytes.Length > 0)
            {
                using var stream = new MemoryStream(imageBytes);
                var bitmapImage = new BitmapImage();
                IfCanDownload = true;
                await bitmapImage.SetSourceAsync(stream.AsRandomAccessStream());
                EmuImageSource = bitmapImage;
            }
        }
        catch (Exception ex)
        {
            progressBarVM.IfShowErrorInfoBarOpen = true;
            Trace.WriteLine(ex.Message);
            Trace.WriteLine(ex);
            progressBarVM.ShowErrorInfoBarContent = ex.Message;
            progressBarVM.ShowErrorInfoBarTitle = "Error";
            WaitCloseInfoBar();
        }
        finally
        {
            IsLoading = false;
            progressBarVM.TaskIsInProgress = "Collapsed";
        }
    }
    private async void WaitCloseInfoBar()
    {
        await Task.Delay(3000);
        progressBarVM.IfShowErrorInfoBarOpen = false;
    }
    public EmuAssignment FilterByTrainModel(ObservableCollection<EmuAssignment> assignments, string targetTrainModel)
    {
        if (assignments == null || string.IsNullOrEmpty(targetTrainModel))
            return null;

        // 精确匹配车型
        return assignments.FirstOrDefault(a => a.TrainModel == targetTrainModel);
    }

    [RelayCommand]
    private async Task SaveEmuImageAsync()
    {
        if (string.IsNullOrEmpty(TrainEmuModel) || imageBytes == null)
            return;

        try
        {
            var savePicker = new FileSavePicker();
            var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(MainWindow.Instance);
            WinRT.Interop.InitializeWithWindow.Initialize(savePicker, hwnd);

            savePicker.SuggestedFileName = $"{TrainEmuModel}";
            savePicker.FileTypeChoices.Add("PNG 图片", new List<string> { ".png" });
            savePicker.DefaultFileExtension = ".png";

            var file = await savePicker.PickSaveFileAsync();
            if (file != null)
            {
                // 直接使用缓存的图片数据保存
                await FileIO.WriteBytesAsync(file, imageBytes);

                progressBarVM.IfShowErrorInfoBarOpen = true;
                progressBarVM.ShowErrorInfoBarContent = $"图片已保存到: {file.Path}";
                progressBarVM.ShowErrorInfoBarTitle = "保存成功";
                WaitCloseInfoBar();
            }
        }
        catch (Exception ex)
        {
            progressBarVM.IfShowErrorInfoBarOpen = true;
            progressBarVM.ShowErrorInfoBarContent = $"保存失败: {ex.Message}";
            progressBarVM.ShowErrorInfoBarTitle = "错误";
            WaitCloseInfoBar();
        }
    }
}