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
    Task<Train> QueryTrainQueryAsync(string trainNumber);
    Task<ObservableCollection<TrainRunInfo>> QueryStationToStationQueryAsync(string from, string to, string date, bool city);

    // 车站查询接口
    Task<ObservableCollection<StationPreselectResult>> QueryStationPreselectAsync(string keyword);
    Task<StationQueryResponse> QueryStationQueryAsync(string telecode);
    Task<BigScreenData> QueryGetBigScreenDataAsync(string stationName);

    // 动车组查询接口
    Task<ObservableCollection<EmuOperation>> QueryEmuQueryAsync(string type, string keyword);
    Task<ObservableCollection<EmuAssignment>> QueryEmuAssignmentQueryAsync(string type, string keyword, int cursor = 0, int count = 15);

    // 实时数据接口
    Task<ObservableCollection<DelayInfo>> QueryTrainDelayAsync(string date, string trainNumber, string fromStation, string toStation);
    Task<PlatformInfo> QueryPlatformInfoAsync(string stationCode, string trainDate, string type, string stationTrainCode);

    // 其他接口
    Task<byte[]> QueryDownloadEmuImageAsync(string trainModel);
}