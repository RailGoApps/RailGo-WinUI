using System;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Linq;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using RailGo.Core.Models.QueryDatas;
using RailGo.Services;

namespace RailGo.ViewModels.Pages.StationToStation;

public partial class StationToStationViewModel : ObservableRecipient
{
    [ObservableProperty]
    private StationPreselectResult fromStation;

    [ObservableProperty]
    private string fromStationKeyword;

    [ObservableProperty]
    private ObservableCollection<StationPreselectResult> fromStationSuggestions = new();

    [ObservableProperty]
    private bool fromSuggestionOpen;

    [ObservableProperty]
    private StationPreselectResult selectedFromSuggestion;

    [ObservableProperty]
    private StationPreselectResult toStation;

    [ObservableProperty]
    private string toStationKeyword;

    [ObservableProperty]
    private ObservableCollection<StationPreselectResult> toStationSuggestions = new();

    [ObservableProperty]
    private bool toSuggestionOpen;

    [ObservableProperty]
    private StationPreselectResult selectedToSuggestion;

    [ObservableProperty]
    private bool allowCitysIsOpen = true;

    [ObservableProperty]
    private DateTimeOffset startDate = new(DateTime.Now);

    [ObservableProperty]
    public ObservableCollection<TrainRunInfo> trainResults;

    partial void OnSelectedFromSuggestionChanged(StationPreselectResult value)
    {
        if (value != null)
        {
            FromStation = value;
            FromSuggestionOpen = false;
        }
    }

    partial void OnSelectedToSuggestionChanged(StationPreselectResult value)
    {
        if (value != null)
        {
            ToStation = value;
            ToSuggestionOpen = false;
        }
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

    public async Task QueryFromStationSuggestionsAsync()
    {
        if (string.IsNullOrWhiteSpace(FromStationKeyword))
        {
            FromSuggestionOpen = false;
            return;
        }

        try
        {
            var service = App.GetService<QueryService>();
            var list = await service.QueryStationPreselectAsync(FromStationKeyword.Trim());
            FromStationSuggestions = new ObservableCollection<StationPreselectResult>(SortByKeyword(list ?? Enumerable.Empty<StationPreselectResult>(), FromStationKeyword.Trim()));
            FromSuggestionOpen = FromStationSuggestions.Count > 0;
        }
        catch (Exception ex)
        {
            Trace.WriteLine(ex);
            FromSuggestionOpen = false;
        }
    }

    public async Task QueryToStationSuggestionsAsync()
    {
        if (string.IsNullOrWhiteSpace(ToStationKeyword))
        {
            ToSuggestionOpen = false;
            return;
        }

        try
        {
            var service = App.GetService<QueryService>();
            var list = await service.QueryStationPreselectAsync(ToStationKeyword.Trim());
            ToStationSuggestions = new ObservableCollection<StationPreselectResult>(SortByKeyword(list ?? Enumerable.Empty<StationPreselectResult>(), ToStationKeyword.Trim()));
            ToSuggestionOpen = ToStationSuggestions.Count > 0;
        }
        catch (Exception ex)
        {
            Trace.WriteLine(ex);
            ToSuggestionOpen = false;
        }
    }

    public async Task QueryTrainListAsync()
    {
        FromSuggestionOpen = false;
        ToSuggestionOpen = false;

        var queryService = App.GetService<QueryService>();
        var resolvedFromStation = await ResolveStationAsync(queryService, FromStation, FromStationKeyword);
        var resolvedToStation = await ResolveStationAsync(queryService, ToStation, ToStationKeyword);

        if (resolvedFromStation == null || resolvedToStation == null)
        {
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

    /// <summary>
    /// 按与 keyword 的贴近程度排序候选列表：
    /// 1. 完全等于 keyword
    /// 2. keyword 是站名的子串（用户输入"上海南" → "上海南站"）
    /// 3. 站名/拼音首字母以 keyword 开头
    /// 4. 拼音首字母以 keyword 开头
    /// 5. 长度最接近 keyword 的
    /// </summary>
    private static IReadOnlyList<StationPreselectResult> SortByKeyword(IEnumerable<StationPreselectResult> stations, string keyword)
    {
        var normalized = keyword.Trim();
        return stations
            .Select(s => new { Item = s, Score = MatchScore(s, normalized) })
            .OrderByDescending(x => x.Score)
            .Select(x => x.Item)
            .ToList();
    }

    private static int MatchScore(StationPreselectResult s, string keyword)
    {
        var name = (s.Name ?? string.Empty).Trim();
        var tele = (s.TeleCode ?? string.Empty).Trim();
        var py = (s.PinyinTriple ?? string.Empty).Trim();

        if (string.Equals(name, keyword, StringComparison.OrdinalIgnoreCase)) return 10000;
        if (string.Equals(tele, keyword, StringComparison.OrdinalIgnoreCase)) return 9999;
        if (string.Equals(py, keyword, StringComparison.OrdinalIgnoreCase)) return 9998;

        if (name.IndexOf(keyword, StringComparison.OrdinalIgnoreCase) >= 0) return 8000 - Math.Abs(name.Length - keyword.Length);
        if (tele.IndexOf(keyword, StringComparison.OrdinalIgnoreCase) >= 0) return 7000;

        if (name.StartsWith(keyword, StringComparison.OrdinalIgnoreCase)) return 6000;
        if (tele.StartsWith(keyword, StringComparison.OrdinalIgnoreCase)) return 5000;
        if (py.StartsWith(keyword, StringComparison.OrdinalIgnoreCase)) return 4000;

        return -Math.Abs(name.Length - keyword.Length);
    }

    /// <summary>
    /// 最终查询时兜底解析：用户可以没选候选，这里按"最贴近 keyword"的站点来查。
    /// </summary>
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

        var ordered = SortByKeyword(stations, normalizedInput);
        return ordered.FirstOrDefault();
    }
}
