using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Reflection;
using System.Threading.Tasks;
using RailGo.Contracts.Services;
using RailGo.Core.Helpers;
using RailGo.Core.Models;
using RailGo.Core.Models.QueryDatas;
using RailGo.Core.Models.Settings;
using RailGo.Core.Query;
using RailGo.Core.Query.Online;
using static Microsoft.Extensions.Logging.EventSource.LoggingEventSource;

namespace RailGo.Services;

public class QueryService : IQueryService
{
    private readonly IDataSourceService _dataSourceService;

    public QueryService(IDataSourceService dataSourceService)
    {
        _dataSourceService = dataSourceService;
    }

    private async Task<GetPathModel> GetPath(string MethodName)
    {
        var AllowCustomSource = await _dataSourceService.GetIfAllowCustomSourceAsync();
        var QueryModeSelcted = await _dataSourceService.GetQueryModeAsync();
        DataSourceMethod method = new();

        if (QueryModeSelcted == "Custom"&& AllowCustomSource)
        {
            var GroupName = await _dataSourceService.GetSelectedDataSourceAsync();
            method = await _dataSourceService.GetDataSourceMethodAsync(GroupName, MethodName);
        }

        // 旧的自定义数据源没有配置新增接口时，回退到默认在线接口
        if (method == null || string.IsNullOrEmpty(method.SourceName))
        {
            method = new DataSourceMethod { Mode = "Online", SourceName = "RailGoDefalt", Name = MethodName };
        }
        else if (QueryModeSelcted == "Offline")
        {
            method = new DataSourceMethod { Mode = "Offline", SourceName = "RailGoDefalt", Name = MethodName };
        }
        else if (QueryModeSelcted == "Online")
        {
            method = new DataSourceMethod { Mode = "Online", SourceName = "RailGoDefalt", Name = MethodName };
        }
        else
        {
            method = new DataSourceMethod { Mode = "Online", SourceName = "RailGoDefalt", Name = MethodName };
        }

        bool isOfflineMode = method.IsOfflineMode;
        string urlOrDbPath;
        if (method.SourceName == "RailGoDefalt")
        {
            if(isOfflineMode)
            {
                urlOrDbPath = GetDatabasePath();
            }
            else
            {
                urlOrDbPath = DefaultApiUrls.GetDefaultUrl(MethodName);
            }
        }
        else
        {
            if (isOfflineMode)
            {
                urlOrDbPath = await _dataSourceService.GetLocalDatabaseSourceAddressAsync(method.SourceName);
            }
            else
            {
                urlOrDbPath = await _dataSourceService.GetOnlineApiSourceAddressAsync(method.SourceName);
            }
        }
        return new GetPathModel
        {
            IsOfflineMode = isOfflineMode,
            Path = urlOrDbPath
        };
    }

    private async Task<GetPathModel> GetPathWithCompletement(string MethodName)
    {
        var GotPath = await GetPath(MethodName);
        if (GotPath.IsOfflineMode)
        {
            if (await _dataSourceService.GetOfflineComplementOnlineAsync())
            {
                GotPath.IsOfflineMode = false;
                GotPath.Path = DefaultApiUrls.GetDefaultUrl(MethodName);
            }
        }
        return GotPath;
    }

    /// <summary>
    /// 获取数据库路径
    /// </summary>
    private string GetDatabasePath()
    {
        return DBGetService.GetLocalDatabasePath();
    }

    #region 车次查询接口

    /// <summary>
    /// 车次预选搜索
    /// </summary>
    public async Task<ObservableCollection<TrainPreselectResult>> QueryTrainPreselectAsync(string keyword)
    {
        var GotPath = await GetPath("QueryTrainPreselect");
        var CanRetryWithOffline = await _dataSourceService.GetOnlineFallbackToOfflineAsync();
        if (!GotPath.IsOfflineMode && CanRetryWithOffline)
        {
            try
            {
                return await ApiService.TrainPreselectAsync(GotPath.IsOfflineMode, GotPath.Path, keyword);
            }
            catch (Exception ex)
            {
                try
                {
                    var offlineDbPath = GetDatabasePath();
                    return await ApiService.TrainPreselectAsync(true, offlineDbPath, keyword);
                }
                catch
                {
                    throw ex;
                }
            }
        }
        return await ApiService.TrainPreselectAsync(GotPath.IsOfflineMode, GotPath.Path, keyword);
    }

    /// <summary>
    /// 车次详情查询
    /// </summary>
    public async Task<Train> QueryTrainQueryAsync(string trainNumber, string date = null)
    {
        date ??= DateTime.Now.ToString("yyyyMMdd");
        var GotPath = await GetPath("QueryTrainQuery");
        var CanRetryWithOffline = await _dataSourceService.GetOnlineFallbackToOfflineAsync();
        if (!GotPath.IsOfflineMode && CanRetryWithOffline)
        {
            try
            {
                return await ApiService.TrainQueryAsync(GotPath.IsOfflineMode, GotPath.Path, trainNumber, date);
            }
            catch (Exception ex)
            {
                try
                {
                    var offlineDbPath = GetDatabasePath();
                    return await ApiService.TrainQueryAsync(true, offlineDbPath, trainNumber, date);
                }
                catch
                {
                    throw ex;
                }
            }
        }
        return await ApiService.TrainQueryAsync(GotPath.IsOfflineMode, GotPath.Path, trainNumber, date);
    }

    /// <summary>
    /// 站站查询
    /// </summary>
    public async Task<ObservableCollection<TrainRunInfo>> QueryStationToStationQueryAsync(string from, string to, string date, bool city)
    {
        var GotPath = await GetPath("QueryStationToStationQuery");
        var CanRetryWithOffline = await _dataSourceService.GetOnlineFallbackToOfflineAsync();
        if (!GotPath.IsOfflineMode && CanRetryWithOffline)
        {
            try
            {
                return await ApiService.StationToStationQueryAsync(GotPath.IsOfflineMode, GotPath.Path, from, to, date, city);
            }
            catch (Exception ex)
            {
                try
                {
                    var offlineDbPath = GetDatabasePath();
                    return await ApiService.StationToStationQueryAsync(true, offlineDbPath, from, to, date, city);
                }
                catch
                {
                    throw ex;
                }
            }
        }
        return await ApiService.StationToStationQueryAsync(GotPath.IsOfflineMode, GotPath.Path, from, to, date, city);
    }

    #endregion

    #region 车站查询接口

    /// <summary>
    /// 车站预选搜索
    /// </summary>
    public async Task<ObservableCollection<StationPreselectResult>> QueryStationPreselectAsync(string keyword)
    {
        var GotPath = await GetPath("QueryStationPreselect");
        var CanRetryWithOffline = await _dataSourceService.GetOnlineFallbackToOfflineAsync();
        if (!GotPath.IsOfflineMode && CanRetryWithOffline)
        {
            try
            {
                return await ApiService.StationPreselectAsync(GotPath.IsOfflineMode, GotPath.Path, keyword);
            }
            catch (Exception ex)
            {
                try
                {
                    var offlineDbPath = GetDatabasePath();
                    return await ApiService.StationPreselectAsync(true, offlineDbPath, keyword);
                }
                catch
                {
                    throw ex;
                }
            }
        }
        return await ApiService.StationPreselectAsync(GotPath.IsOfflineMode, GotPath.Path, keyword);
    }

    /// <summary>
    /// 车站详情查询
    /// </summary>
    public async Task<StationQueryResponse> QueryStationQueryAsync(string telecode)
    {
        var GotPath = await GetPath("QueryStationQuery");
        var CanRetryWithOffline = await _dataSourceService.GetOnlineFallbackToOfflineAsync();
        if (!GotPath.IsOfflineMode && CanRetryWithOffline)
        {
            try
            {
                return await ApiService.StationQueryAsync(GotPath.IsOfflineMode, GotPath.Path, telecode);
            }
            catch (Exception ex)
            {
                try
                {
                    var offlineDbPath = GetDatabasePath();
                    return await ApiService.StationQueryAsync(true, offlineDbPath, telecode);
                }
                catch (Exception ex2)
                {
                    Trace.WriteLine(ex);
                    Trace.WriteLine(ex2);
                    throw ex;
                }
            }
        }
        return await ApiService.StationQueryAsync(GotPath.IsOfflineMode, GotPath.Path, telecode);
    }

    /// <summary>
    /// 车站大屏数据
    /// </summary>
    public async Task<BigScreenData> QueryGetBigScreenDataAsync(string stationTelecode)
    {
        var GotPath = await GetPathWithCompletement("QueryGetBigScreenData");
        if (!GotPath.IsOfflineMode)
        {
            return await ApiService.GetBigScreenDataAsync(GotPath.IsOfflineMode, GotPath.Path, stationTelecode);
        }
        return new();
    }

    #endregion

    #region 动车组查询接口

    /// <summary>
    /// 动车组运行交路查询
    /// </summary>
    public async Task<ObservableCollection<EmuOperation>> QueryEmuQueryAsync(string type, string keyword)
    {
        var GotPath = await GetPathWithCompletement("QueryEmuQuery");
        if (!GotPath.IsOfflineMode)
        {
            return await ApiService.EmuQueryAsync(GotPath.IsOfflineMode, GotPath.Path, type, keyword);
        }
        return new();
    }

    /// <summary>
    /// 动车组配属信息查询
    /// </summary>
    public async Task<EmuCarInfo> QueryEmuCarInfoAsync(string emuId)
    {
        var GotPath = await GetPathWithCompletement("QueryEmuAssignmentQuery");
        if (!GotPath.IsOfflineMode)
        {
            return await ApiService.EmuCarInfoAsync(GotPath.IsOfflineMode, GotPath.Path, emuId);
        }
        return null;
    }

    #endregion

    #region 实时数据接口

    /// <summary>
    /// 正晚点查询
    /// </summary>
    public async Task<ObservableCollection<DelayInfo>> QueryTrainDelayAsync(string trainNumber, string date)
    {
        var GotPath = await GetPathWithCompletement("QueryTrainDelay");
        if (!GotPath.IsOfflineMode)
        {
            return await ApiService.QueryTrainDelayAsync(GotPath.IsOfflineMode, GotPath.Path, trainNumber, date);
        }
        return new();
    }

    /// <summary>
    /// 检票口、站台及出站口查询
    /// </summary>
    public async Task<StationExitInfo> QueryPlatformInfoAsync(string trainNumber, string stationTelecode, string date, string kind)
    {
        var GotPath = await GetPathWithCompletement("QueryPlatformInfo");
        if (!GotPath.IsOfflineMode)
        {
            return await ApiService.QueryPlatformInfoAsync(GotPath.IsOfflineMode, GotPath.Path, trainNumber, stationTelecode, date, kind);
        }
        return null;
    }

    /// <summary>
    /// 列车车厢图查询
    /// </summary>
    public async Task<CoachPicInfo> QueryCoachPicAsync(string trainNumber)
    {
        var GotPath = await GetPathWithCompletement("QueryCoachPic");
        if (!GotPath.IsOfflineMode)
        {
            return await ApiService.QueryCoachPicAsync(GotPath.IsOfflineMode, GotPath.Path, trainNumber);
        }
        return null;
    }

    /// <summary>
    /// 下载图片
    /// </summary>
    public async Task<byte[]> QueryDownloadImageAsync(string url)
    {
        return await HttpService.DownloadFileAsync(url);
    }

    #endregion

    #region 其他接口

    /// <summary>
    /// 下载动车组图片
    /// </summary>
    public async Task<byte[]> QueryDownloadEmuImageAsync(string trainModel)
    {
        var GotPath = await GetPathWithCompletement("QueryDownloadEmuImage");
        return await ApiService.DownloadEmuImageAsync(GotPath.IsOfflineMode, GotPath.Path, trainModel);
    }

    /// <summary>
    /// 列车图鉴搜索
    /// </summary>
    public async Task<TrainAtlasResponse> QueryTrainAtlasAsync(string keyword, int page, int perPage)
    {
        var GotPath = await GetPathWithCompletement("QueryTrainAtlas");
        if (!GotPath.IsOfflineMode)
        {
            return await ApiService.QueryTrainAtlasAsync(GotPath.IsOfflineMode, GotPath.Path, keyword, page, perPage);
        }
        return null;
    }

    #endregion
}

