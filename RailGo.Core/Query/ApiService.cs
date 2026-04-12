using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Threading.Tasks;
using RailGo.Core.Models;
using RailGo.Core.Models.QueryDatas;
using RailGo.Core.Query.Offline;
using RailGo.Core.Query.Online;
using Newtonsoft.Json;

namespace RailGo.Core.Query;

public class ApiService
{
    #region 离线模式判断

    /// <summary>
    /// 获取离线服务实例
    /// </summary>
    public static T GetOfflineService<T>(string databasePath) where T : BaseOfflineService
    {
        return (T)Activator.CreateInstance(typeof(T), databasePath);
    }

    #endregion

    #region 车次查询接口

    /// <summary>
    /// 车次预选搜索
    /// </summary>
    public static async Task<ObservableCollection<TrainPreselectResult>> TrainPreselectAsync(bool isOfflineMode, string urlOrDbPath, string keyword)
    {
        if (isOfflineMode)
        {
            var offlineService = GetOfflineService<TrainOfflineService>(urlOrDbPath);
            var json = await offlineService.TrainPreselectAsync(keyword);
            var stringArray = JsonConvert.DeserializeObject<ObservableCollection<string>>(json);

            var result = new ObservableCollection<TrainPreselectResult>();
            if (stringArray != null)
            {
                foreach (var fullNumber in stringArray)
                {
                    result.Add(new TrainPreselectResult { FullNumber = fullNumber });
                }
            }
            return result;
        }
        else
        {
            var stringArray = await OnlineApiService.TrainPreselectAsync(keyword, urlOrDbPath);
            var result = new ObservableCollection<TrainPreselectResult>();
            if (stringArray != null)
            {
                foreach (var fullNumber in stringArray)
                {
                    result.Add(new TrainPreselectResult { FullNumber = fullNumber });
                }
            }
            return result;
        }
    }

    /// <summary>
    /// 车次详情查询
    /// </summary>
    public static async Task<Train> TrainQueryAsync(bool isOfflineMode, string urlOrDbPath, string trainNumber)
    {
        if (isOfflineMode)
        {
            var offlineService = GetOfflineService<TrainOfflineService>(urlOrDbPath);
            var json = await offlineService.TrainQueryAsync(trainNumber);
            return JsonConvert.DeserializeObject<Train>(json);
        }
        else
        {
            return await OnlineApiService.TrainQueryAsync(trainNumber, urlOrDbPath);
        }
    }

    /// <summary>
    /// 站站查询
    /// </summary>
    public static async Task<ObservableCollection<TrainRunInfo>> StationToStationQueryAsync(bool isOfflineMode, string urlOrDbPath, string from, string to, string date, bool city)
    {
        if (isOfflineMode)
        {
            var offlineService = GetOfflineService<TrainOfflineService>(urlOrDbPath);
            var json = await offlineService.StationToStationQueryAsync(from, to, date);
            return JsonConvert.DeserializeObject<ObservableCollection<TrainRunInfo>>(json);
        }
        else
        {
            return await OnlineApiService.StationToStationQueryAsync(from, to, date, urlOrDbPath, city);
        }
    }

    #endregion

    #region 车站查询接口

    /// <summary>
    /// 车站预选搜索
    /// </summary>
    public static async Task<ObservableCollection<StationPreselectResult>> StationPreselectAsync(bool isOfflineMode, string urlOrDbPath, string keyword)
    {
        if (isOfflineMode)
        {
            var offlineService = GetOfflineService<StationOfflineService>(urlOrDbPath);
            var json = await offlineService.StationPreselectAsync(keyword);
            return JsonConvert.DeserializeObject<ObservableCollection<StationPreselectResult>>(json);
        }
        else
        {
            return await OnlineApiService.StationPreselectAsync(keyword, urlOrDbPath);
        }
    }

    /// <summary>
    /// 车站详情查询
    /// </summary>
    public static async Task<StationQueryResponse> StationQueryAsync(bool isOfflineMode, string urlOrDbPath, string telecode)
    {
        if (isOfflineMode)
        {
            var offlineService = GetOfflineService<StationOfflineService>(urlOrDbPath);
            var json = await offlineService.StationQueryAsync(telecode);
            return JsonConvert.DeserializeObject<StationQueryResponse>(json);
        }
        else
        {
            return await OnlineApiService.StationQueryAsync(telecode, urlOrDbPath);
        }
    }

    /// <summary>
    /// 车站大屏数据
    /// </summary>
    public static async Task<BigScreenData> GetBigScreenDataAsync(bool isOfflineMode, string urlOrDbPath, string stationName)
    {
        if (!isOfflineMode)
        {
            return await OnlineApiService.GetBigScreenDataAsync(stationName, urlOrDbPath);
        }
        return null;
    }

    #endregion

    #region 动车组查询接口

    /// <summary>
    /// 动车组运行交路查询
    /// </summary>
    public static async Task<ObservableCollection<EmuOperation>> EmuQueryAsync(bool isOfflineMode, string urlOrDbPath, string type, string keyword)
    {
        if (!isOfflineMode)
        {
            return await OnlineApiService.EmuQueryAsync(type, keyword, urlOrDbPath);
        }
        return null;
    }

    /// <summary>
    /// 动车组配属查询
    /// </summary>
    public static async Task<ObservableCollection<EmuAssignment>> EmuAssignmentQueryAsync(
        bool isOfflineMode, string urlOrDbPath, string type, string keyword, int cursor = 0, int count = 15)
    {
        if (!isOfflineMode)
        {
            return await OnlineApiService.EmuAssignmentQueryAsync(type, keyword, cursor, count, urlOrDbPath);
        }
        else
        {
            return null;
        }
    }

    #endregion

    #region 实时数据接口

    /// <summary>
    /// 正晚点查询
    /// </summary>
    public static async Task<ObservableCollection<DelayInfo>> QueryTrainDelayAsync(bool isOfflineMode, string urlOrDbPath, string date, string trainNumber,
        string fromStation, string toStation)
    {
        if (!isOfflineMode)
        {
            return await OnlineApiService.QueryTrainDelayAsync(date, trainNumber, fromStation, toStation, urlOrDbPath);
        }
        return null;
    }

    /// <summary>
    /// 停台检票口查询
    /// </summary>
    public static async Task<PlatformInfo> QueryPlatformInfoAsync(bool isOfflineMode, string urlOrDbPath, string stationCode, string trainDate,
        string type, string stationTrainCode)
    {
        if (!isOfflineMode)
        {
            return await OnlineApiService.QueryPlatformInfoAsync(stationCode, trainDate, type, stationTrainCode, urlOrDbPath);
        }
        return null;
    }

    #endregion

    #region 其他接口

    /// <summary>
    /// 下载动车组图片
    /// </summary>
    public static async Task<byte[]> DownloadEmuImageAsync(bool isOfflineMode, string urlOrDbPath, string trainModel)
    {
        if (!isOfflineMode)
        {
            return await OnlineApiService.DownloadEmuImageAsync(trainModel, urlOrDbPath);
        }
        return null;
    }

    #endregion
}