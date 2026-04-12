using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using RailGo.Services;
using RailGo.Core.Models;
using RailGo.Core.Models.QueryDatas;
using System.Threading.Tasks;
using System.Linq;
using System.Diagnostics;
using RailGo.ViewModels.Pages.Shell;

namespace RailGo.ViewModels.Pages.Stations;

public partial class StationDetailsViewModel : ObservableRecipient
{
    public StationDetailsViewModel()
    {
    }

    public MainWindowViewModel progressBarVM = App.GetService<MainWindowViewModel>();

    [ObservableProperty]
    private string stationNameLook;

    [ObservableProperty]
    private string stationPinyin;

    [ObservableProperty]
    private string stationBureau;

    [ObservableProperty]
    private string stationBelong;

    [ObservableProperty]
    private string stationType;

    [ObservableProperty]
    private string stationCodes;

    [ObservableProperty]
    private ObservableCollection<StationTrain> stationTrains;

    [ObservableProperty]
    private ObservableCollection<StationScreenItem> stationBigScreen;

    [ObservableProperty]
    public string ifHighspeed = "Collapsed";

    [ObservableProperty]
    public string ifPassenger = "Collapsed";

    [ObservableProperty]
    public string ifCargo = "Collapsed";

    [ObservableProperty]
    private bool ifBigscreen = false;

    [ObservableProperty]
    private bool isLoading;

    // 存储当前车站的电报码，用于查找停靠信息
    private string currentStationTelecode;

    [RelayCommand]
    public async Task GetInformationAsync(StationPreselectResult stationInfo)
    {
        var queryService = App.GetService<QueryService>();
        string teleCode = stationInfo.TeleCode;
        string stationName = stationInfo.Name;
        List<string> type = stationInfo.Type;
        bool FromAPage = false;
        if (string.IsNullOrEmpty(teleCode) || string.IsNullOrEmpty(stationName))
            return;

        try
        {
            IsLoading = true;
            progressBarVM.TaskIsInProgress = "Visible";

            StationNameLook = stationName;
            StationCodes = $"{stationInfo.PinyinTriple}/-{teleCode}";
            StationPinyin = stationInfo.Pinyin;

            // 设置车站类型标签
            if (type != null)
            {
                if (type.Contains("高"))
                {
                    IfHighspeed = "Visible";
                }

                if (type.Contains("客"))
                {
                    IfPassenger = "Visible";
                    IfBigscreen = true;
                }

                if (type.Contains("货"))
                {
                    IfCargo = "Visible";
                }

                if (type.Contains("SearchingST"))
                {
                    FromAPage = true;
                    IfBigscreen = true;
                }

            }
            var stationResponse = new StationQueryResponse();
            var screenResponse = new BigScreenData();
            // 调用车站详情API
            if (IfBigscreen)
            {
                var stationTask = queryService.QueryStationQueryAsync(teleCode);
                var screenTask = queryService.QueryGetBigScreenDataAsync(stationName);
                await Task.WhenAll(stationTask, screenTask);
                stationResponse = stationTask.Result;
                screenResponse = screenTask.Result;
            }

            if (stationResponse?.Data != null)
            {
                // 设置车站基本信息
                var stationData = stationResponse.Data;
                StationBelong = $"{stationData.Bureau ?? "未知"} {stationData.Belong} 辖";

                // 来自其他页面时，重新设置车站Type

                if (FromAPage)
                {
                    type = stationData.Type;
                    if (type != null)
                    {
                        if (type.Contains("高"))
                        {
                            IfHighspeed = "Visible";
                        }

                        if (type.Contains("客"))
                        {
                            IfPassenger = "Visible";
                            IfBigscreen = true;
                        }

                        if (type.Contains("货"))
                        {
                            IfCargo = "Visible";
                        }
                    }
                }

                // 保存当前车站电报码
                currentStationTelecode = stationData.Telecode;

                // 设置车次信息
                if (stationResponse.Trains != null && stationResponse.Trains.Any())
                {
                    StationTrains = new ObservableCollection<StationTrain>(stationResponse.Trains);
                }
                else
                {
                    StationTrains = new ObservableCollection<StationTrain>();
                }
            }

            if (screenResponse?.Data != null)
            {
                StationBigScreen = new ObservableCollection<StationScreenItem>(screenResponse.Data);
            }
        }
        catch (Exception ex)
        {
            progressBarVM.IfShowErrorInfoBarOpen = true;
            progressBarVM.ShowErrorInfoBarContent = ex.Message;
            progressBarVM.ShowErrorInfoBarTitle = "Error";
            WaitCloseInfoBar();
            StationTrains = new ObservableCollection<StationTrain>();
        }
        finally
        {
            IsLoading = false;
            progressBarVM.TaskIsInProgress = "Collapsed";
        }
    }

    public async Task<ObservableCollection<StationPreselectResult>> SearchStationDetails(string stationName)
    {
        if (string.IsNullOrWhiteSpace(stationName))
        {
            return new ObservableCollection<StationPreselectResult>();
        }

        try
        {
            IsLoading = true;
            progressBarVM.TaskIsInProgress = "Visible";

            // 调用 API 进行搜索
            var queryService = App.GetService<QueryService>();
            return await queryService.QueryStationPreselectAsync(stationName);
        }
        catch (Exception ex)
        {
            progressBarVM.IfShowErrorInfoBarOpen = true;
            progressBarVM.ShowErrorInfoBarContent = ex.Message;
            progressBarVM.ShowErrorInfoBarTitle = "Error";
            WaitCloseInfoBar();
            return new ObservableCollection<StationPreselectResult>();
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
    public StationTrain FindstationTrainsByTrainNumber(string trainNumber)
    {
        return stationTrains.FirstOrDefault(item => item.Number == trainNumber);
    }

}