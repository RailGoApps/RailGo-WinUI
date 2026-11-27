using System.Collections.ObjectModel;
using System.Diagnostics;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.UI.Xaml.Controls;
using RailGo.Contracts.Services;
using RailGo.Core.Models.QueryDatas;
using RailGo.Services;
using RailGo.ViewModels.Pages.Shell;
using RailGo.ViewModels.Pages.TrainAtlas;

namespace RailGo.ViewModels.Pages.TrainEmus;

public partial class EMU_RoutingDetailsViewModel : ObservableRecipient
{
    public MainWindowViewModel progressBarVM = App.GetService<MainWindowViewModel>();

    [ObservableProperty]
    public ObservableCollection<EmuOperation> trainEmuInfos = new();

    [ObservableProperty]
    private bool isLoading;

    [ObservableProperty]
    private string trainEmuModel;

    [ObservableProperty]
    private string trainEmuCode;

    [ObservableProperty]
    private string currentEmuNo;

    [ObservableProperty]
    private string trainBelong = "未知";

    [ObservableProperty]
    private string trainMaker = "未知";

    public EMU_RoutingDetailsViewModel()
    {
    }

    [RelayCommand]
    private async Task SearchEmuDetailsAsync(EmuOperation DataFromLast)
    {
        if (DataFromLast == null)
        {
            return;
        }

        try
        {
            IsLoading = true;
            progressBarVM.TaskIsInProgress = "Visible";

            TrainEmuModel = DataFromLast.EmuNoModel;
            TrainEmuCode = DataFromLast.EmuNoCode;
            CurrentEmuNo = DataFromLast.EmuNo;

            var queryService = App.GetService<QueryService>();
            var trainCodes = GetTrainCodes(DataFromLast);

            // 重联车组（如 CR400BF-5076/5033）不能直接把 "A/B" 作为关键字查询，
            // 需要对每个车组号分别查询后合并去重。
            var merged = new List<EmuOperation>();
            var keys = new HashSet<string>();
            foreach (var trainCode in trainCodes)
            {
                ObservableCollection<EmuOperation> result = null;
                try
                {
                    result = await queryService.QueryEmuQueryAsync("emu", trainCode);
                }
                catch (Exception ex)
                {
                    Trace.WriteLine($"查询车组 {trainCode} 交路失败: {ex.Message}");
                }

                if (result == null)
                {
                    continue;
                }

                foreach (var operation in result)
                {
                    var key = $"{operation.TrainNo}|{operation.Date}|{operation.EmuNo}";
                    if (keys.Add(key))
                    {
                        merged.Add(operation);
                    }
                }
            }

            TrainEmuInfos = new ObservableCollection<EmuOperation>(
                merged.OrderByDescending(x => x.Date).ThenByDescending(x => x.TrainNo));

            try
            {
                // 配属接口同样只识别单个车组号，重联时取第一个车组号查询
                var carInfo = trainCodes.Count > 0
                    ? await queryService.QueryEmuCarInfoAsync(trainCodes[0])
                    : null;
                if (carInfo != null)
                {
                    TrainBelong = $"{carInfo.Bureau ?? "未知"} {carInfo.Depot ?? "未知"}";
                    TrainMaker = $"{carInfo.TrainsetManufacturer ?? "未知"} 制造";
                }
            }
            catch (Exception ex)
            {
                // 配属信息属于附加信息，失败时不影响交路表展示
                Trace.WriteLine($"动车组配属信息获取失败: {ex.Message}");
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

    /// <summary>
    /// 获取需要查询的车组号列表，重联时包含全部车组号
    /// </summary>
    private static List<string> GetTrainCodes(EmuOperation data)
    {
        if (data.TrainCodes is { Count: > 0 })
        {
            return data.TrainCodes
                .Where(code => !string.IsNullOrWhiteSpace(code))
                .Select(code => code.Trim())
                .Distinct()
                .ToList();
        }

        if (!string.IsNullOrWhiteSpace(data.EmuNo))
        {
            return data.EmuNo
                .Split('/', StringSplitOptions.RemoveEmptyEntries)
                .Select(code => code.Trim())
                .Distinct()
                .ToList();
        }

        return new List<string>();
    }

    /// <summary>
    /// 跳转到列车图鉴并自动填充当前车组号
    /// </summary>
    [RelayCommand]
    private void ViewAtlasImage()
    {
        // 重联车组的 CurrentEmuNo 形如 "CR400BF-5033/CR400BF-5076"，
        // 列车图鉴只支持单个车组号，这里取第一个。
        var emuNo = CurrentEmuNo?
            .Split('/', StringSplitOptions.RemoveEmptyEntries)
            .Select(code => code.Trim())
            .FirstOrDefault(code => !string.IsNullOrEmpty(code));

        if (string.IsNullOrEmpty(emuNo))
            return;

        // 切换到 Home 标签页，导航栏会同步选中“列车图鉴”
        if (MainWindow.Instance?.MainTabView is TabView tabView)
        {
            tabView.SelectedIndex = 0;
        }

        App.GetService<INavigationService>().NavigateTo(typeof(TrainAtlasViewModel).FullName!, emuNo);
    }

    private async void WaitCloseInfoBar()
    {
        await Task.Delay(3000);
        progressBarVM.IfShowErrorInfoBarOpen = false;
    }
}
