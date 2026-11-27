﻿﻿﻿using System.Collections.ObjectModel;
using System.Diagnostics;
using System.IO;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Newtonsoft.Json;
using RailGo.Core.Models;
using RailGo.Core.Models.QueryDatas;
using RailGo.Services;
using RailGo.Views;
using Windows.Media.Protection.PlayReady;
using RailGo.ViewModels.Pages.Shell;

namespace RailGo.ViewModels.Pages.Trains;

public partial class TrainNumberTripDetailsViewModel : ObservableRecipient
{
    public TrainNumberTripDetailsViewModel()
    {
    }

    public MainWindowViewModel progressBarVM = App.GetService<MainWindowViewModel>();

    [ObservableProperty]
    public Train realdata;

    [ObservableProperty]
    public string trainName;

    [ObservableProperty]
    public string alongTime;

    [ObservableProperty]
    public ObservableCollection<TimetableItem> viaStations;

    [ObservableProperty]
    public ObservableCollection<TrainDiagram> routing;

    [ObservableProperty]
    public string trainModel;

    [ObservableProperty]
    public ObservableCollection<EmuOperation> trainEmuInfos = new();

    [ObservableProperty]
    public string crType = " -- ";

    [ObservableProperty]
    public string ifEmuRouting = "Collapsed";

    [ObservableProperty]
    public string crTypeLabelBorderBrush = "#ffffff";

    [ObservableProperty]
    public string crTypeLabelBackground = "#ffffff";

    [ObservableProperty]
    public string bureauName;

    [ObservableProperty]
    public string arrivalTime;

    [ObservableProperty]
    public string endStationName;

    [ObservableProperty]
    public string fromTime;

    [ObservableProperty]
    public string beginStationName;

    [ObservableProperty]
    public ObservableCollection<DelayInfo> trainDelayInfo = new();

    [ObservableProperty]
    public string ifRouting = "Collapsed";

    // 列车车厢图摘要
    [ObservableProperty]
    public string coachSummary;

    [ObservableProperty]
    public string coachCarType;

    [ObservableProperty]
    public string coachCarCode;

    [ObservableProperty]
    public string coachTrainNumber;

    [ObservableProperty]
    public bool isCoachPicVisible;

    [RelayCommand]
    public async Task GetInformationAsync((string train_no, string date) parameters)
    {
        var train_no = parameters.train_no;
        var queryNumber = train_no?.Split('/')[0];
        var date = string.IsNullOrWhiteSpace(parameters.date) ? DateTime.Now.ToString("yyyyMMdd") : parameters.date;
        progressBarVM.TaskIsInProgress = "Visible";

        var queryService = App.GetService<QueryService>();

        try
        {
            // 车次主数据与动车组信息并行查询
            var trainTask = queryService.QueryTrainQueryAsync(queryNumber, date);
            var emuTask = queryService.QueryEmuQueryAsync("train", queryNumber);

            Realdata = await trainTask;

            if (Realdata?.Timetable == null || Realdata.Timetable.Count == 0)
            {
                throw new InvalidOperationException($"未查询到 {train_no} 在 {date} 的车次信息");
            }

            try
            {
                var emuInfos = await emuTask;
                // 车次详情只展示当前开行日的动车组信息
                var sameDateEmuInfos = emuInfos?
                    .Where(item => string.Equals(item.Date?.Replace("-", ""), date, StringComparison.OrdinalIgnoreCase))
                    .ToList();

                TrainEmuInfos = sameDateEmuInfos != null && sameDateEmuInfos.Count > 0
                    ? new ObservableCollection<EmuOperation>(sameDateEmuInfos)
                    : emuInfos ?? new ObservableCollection<EmuOperation>();
                IfEmuRouting = TrainEmuInfos.Count > 0 ? "Visible" : "Collapsed";
            }
            catch (Exception ex)
            {
                Trace.WriteLine(ex);
                IfEmuRouting = "Collapsed";
            }

            // 获取第一个和最后一个时刻表项
            var firstItem = Realdata.Timetable.First();
            var lastItem = Realdata.Timetable.Last();

            BeginStationName = firstItem.Station;
            FromTime = firstItem.Depart;
            EndStationName = lastItem.Station;
            ArrivalTime = lastItem.Arrive;

            // 计算运行时间，优先使用接口返回的总历时
            if (Realdata.Spend is int spend && spend > 0)
            {
                AlongTime = $"约{spend / 60}时{spend % 60}分";
            }
            else if (TimeSpan.TryParse(FromTime, out var startTime) && TimeSpan.TryParse(ArrivalTime, out var endTime))
            {
                // 考虑天数差异
                int dayDifference = lastItem.Day - firstItem.Day;
                if (dayDifference > 0)
                {
                    endTime = endTime.Add(TimeSpan.FromDays(dayDifference));
                }

                TimeSpan duration = endTime - startTime;
                AlongTime = $"约{(int)duration.TotalHours}时{duration.Minutes}分";
            }
            else
            {
                AlongTime = " -- ";
            }

            BureauName = $"{Realdata.DisplayBureau}{Realdata.CarOwner}";
            TrainModel = Realdata.Car;
            TrainName = Realdata.Number;
            CrType = ResolveTrainType(Realdata);

            switch (CrType)
            {
                case "高速":
                    CrTypeLabelBorderBrush = "#f09b7d";
                    CrTypeLabelBackground = "#fdefeb";
                    break;
                case "动车":
                    CrTypeLabelBorderBrush = "#718bdc";
                    CrTypeLabelBackground = "#e9edfa";
                    break;
                case "城际":
                    CrTypeLabelBorderBrush = "#86c5a8";
                    CrTypeLabelBackground = "#eaf6f0";
                    break;
                case "直达":
                    CrTypeLabelBorderBrush = "#b39ddb";
                    CrTypeLabelBackground = "#f3eefc";
                    break;
                case "特快":
                    CrTypeLabelBorderBrush = "#f0b27a";
                    CrTypeLabelBackground = "#fdf1e7";
                    break;
                case "快速":
                case "新空调快速":
                    CrTypeLabelBorderBrush = "#a8d9e9";
                    CrTypeLabelBackground = "#e9f5fa";
                    break;
                default:
                    CrTypeLabelBorderBrush = "#c8c8c8";
                    CrTypeLabelBackground = "#f5f5f5";
                    break;
            }

            Routing = Realdata.Diagram;
            IfRouting = Routing != null && Routing.Count > 0 ? "Visible" : "Collapsed";

            // 正晚点、检票口/站台/出站口、车厢图并行查询
            var delayTask = queryService.QueryTrainDelayAsync(queryNumber, date);
            var coachTask = queryService.QueryCoachPicAsync(Realdata.NumberFull?.FirstOrDefault() ?? queryNumber);
            var exitTask = LoadStationExitInfosAsync(queryService, Realdata.Timetable, queryNumber, date);

            try
            {
                await Task.WhenAll(delayTask, coachTask, exitTask);
            }
            catch (Exception ex)
            {
                Trace.WriteLine(ex);
            }

            TrainDelayInfo = delayTask.Status == TaskStatus.RanToCompletion ? delayTask.Result : new();
            if (TrainDelayInfo.Count > 0)
            {
                var delayDict = TrainDelayInfo
                    .Where(item => !string.IsNullOrEmpty(item.StationTelecode))
                    .GroupBy(item => NormalizeTelecode(item.StationTelecode))
                    .ToDictionary(group => group.Key, group => group.First());

                foreach (var station in Realdata.Timetable)
                {
                    if (delayDict.TryGetValue(NormalizeTelecode(station.StationTelecode), out var delayInfo))
                    {
                        station.DelayInfo = delayInfo;
                    }
                }
            }

            var coachInfo = coachTask.Status == TaskStatus.RanToCompletion ? coachTask.Result : null;
            if (coachInfo?.CoachPicList != null && coachInfo.CoachPicList.Count > 0)
            {
                CoachTrainNumber = Realdata.NumberFull?.FirstOrDefault() ?? queryNumber;
                CoachCarType = coachInfo.CarType;
                CoachCarCode = coachInfo.CarCode;
                CoachSummary = BuildCoachSummary(coachInfo);
                IsCoachPicVisible = true;
            }
            else
            {
                IsCoachPicVisible = false;
            }

            ViaStations = Realdata.Timetable;
        }
        catch (Exception ex)
        {
            Trace.WriteLine(ex);
            progressBarVM.IfShowErrorInfoBarOpen = true;
            progressBarVM.ShowErrorInfoBarTitle = "Error";
            progressBarVM.ShowErrorInfoBarContent = ex.Message;
        }
        finally
        {
            progressBarVM.TaskIsInProgress = "Collapsed";
        }
    }

    private static string ResolveTrainType(Train train)
    {
        if (!string.IsNullOrWhiteSpace(train.Type))
        {
            return train.Type;
        }

        var kind = train.NumberKind?.Trim().ToUpperInvariant();
        if (string.IsNullOrEmpty(kind))
        {
            return " -- ";
        }

        return kind switch
        {
            "G" => "高速",
            "D" => "动车",
            "C" => "城际",
            "Z" => "直达",
            "T" => "特快",
            "K" => "快速",
            "S" => "市域",
            _ => train.NumberKind
        };
    }

    /// <summary>
    /// 根据车厢图接口返回的 carInfo 组装摘要：编组、定员、最高时速、餐车、全长。
    /// </summary>
    private static string BuildCoachSummary(CoachPicInfo info)
    {
        string GetValue(string name)
        {
            return info.CarInfo?
                .FirstOrDefault(item => string.Equals(item.PictureName, name, StringComparison.OrdinalIgnoreCase))
                ?.PictureValue;
        }

        var parts = new List<string>();
        void Add(string label, string value)
        {
            if (!string.IsNullOrWhiteSpace(value))
            {
                parts.Add($"{label} {value}");
            }
        }

        Add("编组", GetValue("列车编组"));
        Add("定员", GetValue("列车定员"));
        Add("最高时速", GetValue("最高时速"));
        Add("餐车", GetValue("餐车"));
        Add("全长", GetValue("列车长度"));

        return string.Join(" · ", parts);
    }

    private static string NormalizeTelecode(string telecode)
    {
        return string.IsNullOrEmpty(telecode) ? string.Empty : telecode.Replace("/", "").Trim();
    }

    private static async Task LoadStationExitInfosAsync(QueryService queryService, ObservableCollection<TimetableItem> timetable,
        string fallbackTrainNumber, string date)
    {
        using var semaphore = new SemaphoreSlim(5);
        var tasks = new List<Task>();

        for (int i = 0; i < timetable.Count; i++)
        {
            var index = i;
            var item = timetable[index];
            if (string.IsNullOrWhiteSpace(item.StationTelecode))
            {
                continue;
            }

            // 始发站只有发车信息，终到站只有到达信息
            var kind = index == 0 ? "departure" : index == timetable.Count - 1 ? "arrival" : "departure";
            var trainNumber = string.IsNullOrWhiteSpace(item.TrainCode) ? fallbackTrainNumber : item.TrainCode;
            var telecode = item.StationTelecode.Replace("/", "");

            tasks.Add(Task.Run(async () =>
            {
                await semaphore.WaitAsync();
                try
                {
                    item.ExitInfo = await queryService.QueryPlatformInfoAsync(trainNumber, telecode, date, kind);
                }
                catch (Exception ex)
                {
                    Trace.WriteLine(ex);
                }
                finally
                {
                    semaphore.Release();
                }
            }));
        }

        await Task.WhenAll(tasks);
    }

    public async Task<ObservableCollection<StationPreselectResult>> SearchStationDetails(string stationName)
    {
        if (string.IsNullOrWhiteSpace(stationName))
        {
            return new ObservableCollection<StationPreselectResult>();
        }

        try
        {
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
            return new ObservableCollection<StationPreselectResult>();
        }
        finally
        {
            progressBarVM.TaskIsInProgress = "Collapsed";
        }
    }
    public TimetableItem FindstationTrainsByStationName(string stationName)
    {
        return viaStations.FirstOrDefault(item => item.Station == stationName);
    }
}
