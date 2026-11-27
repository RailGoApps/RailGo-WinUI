using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Threading.Tasks;
using RailGo.Core.Models;
using RailGo.Core.Models.QueryDatas;
using RailGo.Core.Query.Online;

namespace RailGo.Contracts.Services;

public interface IQueryService
{
    // 车次查询接口
    Task<ObservableCollection<TrainPreselectResult>> QueryTrainPreselectAsync(string keyword);
    Task<Train> QueryTrainQueryAsync(string trainNumber, string date = null);
    Task<ObservableCollection<TrainRunInfo>> QueryStationToStationQueryAsync(string from, string to, string date, bool city);

    // 车站查询接口
    Task<ObservableCollection<StationPreselectResult>> QueryStationPreselectAsync(string keyword);
    Task<StationQueryResponse> QueryStationQueryAsync(string telecode);
    Task<BigScreenData> QueryGetBigScreenDataAsync(string stationName);

    // 动车组查询接口
    Task<ObservableCollection<EmuOperation>> QueryEmuQueryAsync(string type, string keyword);
    Task<EmuCarInfo> QueryEmuCarInfoAsync(string emuId);

    // 实时数据接口
    Task<ObservableCollection<DelayInfo>> QueryTrainDelayAsync(string trainNumber, string date);
    Task<StationExitInfo> QueryPlatformInfoAsync(string trainNumber, string stationTelecode, string date, string kind);
    Task<CoachPicInfo> QueryCoachPicAsync(string trainNumber);

    // 其他接口
    Task<byte[]> QueryDownloadEmuImageAsync(string trainModel);
    Task<byte[]> QueryDownloadImageAsync(string url);
    Task<TrainAtlasResponse> QueryTrainAtlasAsync(string keyword, int page, int perPage);
}