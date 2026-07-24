using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Threading.Tasks;
using RailGo.Core.Helpers;
using RailGo.Core.Models;
using RailGo.Core.Models.QueryDatas;
using RailGo.Core.Models.Settings;

namespace RailGo.Core.Query.Online;

public class OnlineApiService
{
    #region V2 API 通用方法

    private static async Task<T> GetV2Async<T>(string url)
    {
        var response = await HttpService.GetAsync<V2ApiResponse<T>>(url);
        return response.Data;
    }

    #endregion

    #region 车次查询接口

    public static async Task<ObservableCollection<string>> TrainPreselectAsync(string keyword, string url)
    {
        return await HttpService.GetAsync<ObservableCollection<string>>($"{url}?keyword={System.Net.WebUtility.UrlEncode(keyword)}");
    }

    public static async Task<Train> TrainQueryAsync(string trainNumber, string url)
    {
        return await HttpService.GetAsync<Train>($"{url}?train={System.Net.WebUtility.UrlEncode(trainNumber)}");
    }

    public static async Task<ObservableCollection<TrainRunInfo>> StationToStationQueryAsync(string from, string to, string date, string url, bool city)
    {
        return await HttpService.GetAsync<ObservableCollection<TrainRunInfo>>($"{url}?from={from}&to={to}&date={date}&city={city}");
    }

    #endregion

    #region 车站查询接口

    public static async Task<ObservableCollection<StationPreselectResult>> StationPreselectAsync(string keyword, string url)
    {
        return await HttpService.GetAsync<ObservableCollection<StationPreselectResult>>($"{url}?keyword={System.Net.WebUtility.UrlEncode(keyword)}");
    }

    /// <summary>
    /// 车站详情查询 (参数: telecode=车站电报码)
    /// </summary>
    public static async Task<StationQueryResponse> StationQueryAsync(string telecode, string url)
    {
        return await HttpService.GetAsync<StationQueryResponse>($"{url}?telecode={telecode}");
    }

    /// <summary>
    /// 车站大屏数据 (V2 API: stationTelecode=电报码, kind=departure/arrival)
    /// </summary>
    public static async Task<BigScreenData> GetBigScreenDataAsync(string stationTelecode, string url, string kind = "departure")
    {
        return await HttpService.GetAsync<BigScreenData>($"{url}?stationTelecode={stationTelecode}&kind={kind}");
    }

    #endregion

    #region 动车组查询接口

    public static async Task<ObservableCollection<EmuOperation>> EmuQueryAsync(string type, string keyword, string url)
    {
        return await HttpService.GetAsync<ObservableCollection<EmuOperation>>($"{url}/{System.Net.WebUtility.UrlEncode(keyword)}");
    }

    public static async Task<ObservableCollection<EmuAssignment>> EmuAssignmentQueryAsync(string type, string keyword, int cursor, int count, string url)
    {
        var formData = new List<KeyValuePair<string, string>>
        {
            new("type", type),
            new("keyword", keyword),
            new("trainCategory", "0"),
            new("cursor", cursor.ToString()),
            new("count", count.ToString())
        };

        var onlineResponse = await HttpService.PostFormAsync<EmuAssignmentResponse>(url, formData);
        return onlineResponse?.Data?.Data;
    }

    #endregion

    #region 实时数据接口

    /// <summary>
    /// 正晚点查询 (V2 API: GET /api/v2/getTrainDelay)
    /// </summary>
    public static async Task<ObservableCollection<DelayInfo>> QueryTrainDelayAsync(string date, string trainNumber, string fromStation, string toStation, string url)
    {
        var result = new ObservableCollection<DelayInfo>();
        try
        {
            var delayData = await GetV2Async<List<DelayInfo>>($"{url}?trainNum={System.Net.WebUtility.UrlEncode(trainNumber)}&stationTelecode={System.Net.WebUtility.UrlEncode(fromStation)}&date={date}");
            if (delayData != null)
            {
                foreach (var item in delayData)
                {
                    result.Add(item);
                }
            }
        }
        catch
        {
        }
        return result;
    }

    public static async Task<PlatformInfo> QueryPlatformInfoAsync(string stationCode, string trainDate, string type, string stationTrainCode, string url)
    {
        var data = new
        {
            stationCode,
            trainDate,
            type,
            stationTrainCode
        };
        return await HttpService.PostAsync<PlatformInfo>(url, data);
    }

    #endregion

    #region 其他接口

    public static async Task<byte[]> DownloadEmuImageAsync(string trainModel, string url)
    {
        return await HttpService.DownloadFileAsync($"{url}/{System.Net.WebUtility.UrlEncode(trainModel)}.png");
    }

    #endregion
}
