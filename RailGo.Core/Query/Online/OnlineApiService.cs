using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Threading.Tasks;
using RailGo.Core.Helpers;
using RailGo.Core.Models;
using RailGo.Core.Models.QueryDatas;

namespace RailGo.Core.Query.Online;

public class OnlineApiService
{
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

    public static async Task<StationQueryResponse> StationQueryAsync(string telecode, string url)
    {
        return await HttpService.GetAsync<StationQueryResponse>($"{url}?telecode={telecode}");
    }

    public static async Task<BigScreenData> GetBigScreenDataAsync(string stationName, string url)
    {
        var nameWithoutSuffix = stationName.Replace("站", "");
        return await HttpService.GetAsync<BigScreenData>($"{url}/station/{System.Net.WebUtility.UrlEncode(nameWithoutSuffix)}");
    }

    #endregion

    #region 动车组查询接口

    public static async Task<ObservableCollection<EmuOperation>> EmuQueryAsync(string type, string keyword, string url)
    {
        return await HttpService.GetAsync<ObservableCollection<EmuOperation>>($"{url}/{type}/{System.Net.WebUtility.UrlEncode(keyword)}");
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

    public static async Task<ObservableCollection<DelayInfo>> QueryTrainDelayAsync(string date, string trainNumber, string fromStation, string toStation, string url)
    {
        var data = new
        {
            date,
            trainNumber,
            fromStationName = fromStation,
            toStationName = toStation
        };

        var delayResponse = await HttpService.PostAsync<DelayResponse>(url, data);
        var result = new ObservableCollection<DelayInfo>();

        if (delayResponse?.Data != null)
        {
            // 将数据添加到 ObservableCollection
            foreach (var item in delayResponse.Data)
            {
                result.Add(item);
            }
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