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

    public static async Task<Train> TrainQueryAsync(string trainNumber, string date, string url)
    {
        return await GetV2Async<Train>($"{url}?trainNum={System.Net.WebUtility.UrlEncode(trainNumber)}&date={date}");
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
        return await HttpService.GetAsync<BigScreenData>($"{url}?stationTelecode={stationTelecode}&kind={kind}&ver=3");
    }

    #endregion

    #region 动车组查询接口

    public static async Task<ObservableCollection<EmuOperation>> EmuQueryAsync(string type, string keyword, string url)
    {
        var result = new ObservableCollection<EmuOperation>();
        var response = await HttpService.GetAsync<EmuQueryResponse>($"{url}?keyword={System.Net.WebUtility.UrlEncode(keyword)}");

        if (response?.Data != null)
        {
            foreach (var item in response.Data)
            {
                result.Add(item);
            }
        }

        return result;
    }

    /// <summary>
    /// 动车组配属信息 (GET /api/car/info?id=车组号)
    /// </summary>
    public static async Task<EmuCarInfo> EmuCarInfoAsync(string emuId, string url)
    {
        var response = await HttpService.GetAsync<EmuCarInfoResponse>($"{url}?id={System.Net.WebUtility.UrlEncode(emuId)}");
        return response?.Data;
    }

    #endregion

    #region 实时数据接口

    /// <summary>
    /// 正晚点查询 (V2 API: GET /api/v2/getTrainDelayAll)
    /// </summary>
    public static async Task<ObservableCollection<DelayInfo>> QueryTrainDelayAsync(string trainNumber, string date, string url)
    {
        var result = new ObservableCollection<DelayInfo>();
        try
        {
            var delayData = await GetV2Async<List<DelayInfo>>($"{url}?trainNum={System.Net.WebUtility.UrlEncode(trainNumber)}&date={date}");
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

    /// <summary>
    /// 检票口、站台及出站口查询 (V2 API: GET /api/v2/getExit)
    /// </summary>
    public static async Task<StationExitInfo> QueryPlatformInfoAsync(string trainNumber, string stationTelecode, string date, string kind, string url)
    {
        try
        {
            return await GetV2Async<StationExitInfo>($"{url}?trainNum={System.Net.WebUtility.UrlEncode(trainNumber)}&stationTelecode={System.Net.WebUtility.UrlEncode(stationTelecode)}&date={date}&kind={kind}");
        }
        catch
        {
            return null;
        }
    }

    /// <summary>
    /// 列车车厢图查询 (V2 API: GET /api/v2/getCoachPic)
    /// </summary>
    public static async Task<CoachPicInfo> QueryCoachPicAsync(string trainNumber, string url)
    {
        try
        {
            return await GetV2Async<CoachPicInfo>($"{url}?train={System.Net.WebUtility.UrlEncode(trainNumber)}");
        }
        catch
        {
            return null;
        }
    }

    #endregion

    #region 其他接口

    /// <summary>
    /// 列车图鉴搜索
    /// </summary>
    public static async Task<TrainAtlasResponse> QueryTrainAtlasAsync(string keyword, int page, int perPage, string url)
    {
        return await HttpService.GetAsync<TrainAtlasResponse>($"{url}?q={System.Net.WebUtility.UrlEncode(keyword)}&page={page}&per_page={perPage}");
    }

    public static async Task<byte[]> DownloadEmuImageAsync(string trainModel, string url)
    {
        return await HttpService.DownloadFileAsync($"{url}/{System.Net.WebUtility.UrlEncode(trainModel)}.png");
    }

    #endregion
}
