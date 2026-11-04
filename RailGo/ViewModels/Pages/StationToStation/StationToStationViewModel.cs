using System;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Linq;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Messaging;
using RailGo.Core.Models.Messages;
using RailGo.Core.Models.QueryDatas;
using RailGo.Services;
using RailGo.Contracts.ViewModels;

namespace RailGo.ViewModels.Pages.StationToStation;

public partial class StationToStationViewModel : ObservableRecipient, INavigationAware
{
    [ObservableProperty]
    private string contentText = "最近查询";

    [ObservableProperty]
    private StationPreselectResult fromStation;

    [ObservableProperty]
    private string fromStationKeyword;

    [ObservableProperty]
    private StationPreselectResult toStation;

    [ObservableProperty]
    private string toStationKeyword;

    [ObservableProperty]
    private bool selectTeachingTipIsOpen;

    [ObservableProperty]
    private bool allowCitysIsOpen = true;

    [ObservableProperty]
    private DateTimeOffset startDate = new(DateTime.Now);

    [ObservableProperty]
    public ObservableCollection<TrainRunInfo> trainResults;

    public StationToStationViewModel()
    {
        WeakReferenceMessenger.Default.Register<StationSelectedInStationToStationMessagerModel>(this, (recipient, message) =>
        {
            if (message != null && message.MessagerName == "StationToStation_SearchFromStation")
            {
                FromStation = message.Data;
                SelectTeachingTipIsOpen = false;
            }
            else if (message != null && message.MessagerName == "StationToStation_SearchToStation")
            {
                ToStation = message.Data;
                SelectTeachingTipIsOpen = false;
            }
        });
    }

    partial void OnFromStationChanged(StationPreselectResult value)
    {
        if (value != null)
        {
            FromStationKeyword = value.Name;
        }
    }

    partial void OnToStationChanged(StationPreselectResult value)
    {
        if (value != null)
        {
            ToStationKeyword = value.Name;
        }
    }

    public async Task QueryTrainListAsync()
    {
        var queryService = App.GetService<QueryService>();
        var resolvedFromStation = await ResolveStationAsync(queryService, FromStation, FromStationKeyword);
        var resolvedToStation = await ResolveStationAsync(queryService, ToStation, ToStationKeyword);

        if (resolvedFromStation == null || resolvedToStation == null)
        {
            ContentText = "请正确填写始发站和终到站（可输入站名/电报码，或点击右侧按钮选择）";
            return;
        }

        FromStation = resolvedFromStation;
        ToStation = resolvedToStation;

        try
        {
            TrainResults = await queryService.QueryStationToStationQueryAsync(
                resolvedFromStation.TeleCode,
                resolvedToStation.TeleCode,
                StartDate.ToString("yyyyMMdd"),
                AllowCitysIsOpen);
            Trace.WriteLine($"查询到 {TrainResults.Count} 条结果");
        }
        catch (Exception ex)
        {
            Trace.WriteLine($"查询失败：{ex.Message}");
        }
    }

    private static async Task<StationPreselectResult?> ResolveStationAsync(
        QueryService queryService,
        StationPreselectResult? selectedStation,
        string? inputKeyword)
    {
        if (selectedStation != null && !string.IsNullOrWhiteSpace(selectedStation.TeleCode))
        {
            return selectedStation;
        }

        if (string.IsNullOrWhiteSpace(inputKeyword))
        {
            return null;
        }

        var normalizedInput = inputKeyword.Trim();
        var stations = await queryService.QueryStationPreselectAsync(normalizedInput);
        if (stations == null || stations.Count == 0)
        {
            return null;
        }

        var exactMatch = stations.FirstOrDefault(station =>
            string.Equals(station.Name, normalizedInput, StringComparison.OrdinalIgnoreCase)
            || string.Equals(station.TeleCode, normalizedInput, StringComparison.OrdinalIgnoreCase)
            || string.Equals(station.PinyinTriple, normalizedInput, StringComparison.OrdinalIgnoreCase));

        return exactMatch ?? stations.FirstOrDefault();
    }

    public void OnNavigatedTo(object parameter)
    {
        if (parameter is not string[] { Length: >= 2 } route)
            return;

        FromStationKeyword = route[0];
        ToStationKeyword = route[1];
        _ = QueryFromDeepLinkObservedAsync();
    }

    private async Task QueryFromDeepLinkObservedAsync()
    {
        try
        {
            await QueryTrainListAsync();
        }
        catch (Exception ex)
        {
            ContentText = $"查询失败：{ex.Message}";
        }
    }

    public void OnNavigatedFrom()
    {
    }
}
