using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;

namespace RailGo.Core.Query.Online;
public static class DefaultApiUrls
{
    private static readonly Dictionary<string, string> _urlMappings = new Dictionary<string, string>
    {
        { "QueryTrainPreselect", "https://data.railgo.zenglingkun.cn/api/train/preselect" },
        // 车次主数据 V2
        { "QueryTrainQuery", "https://rg-api.zenglingkun.cn/api/v2/getTrainMain" },
        { "QueryStationToStationQuery", "https://data.railgo.zenglingkun.cn/api/train/sts_query" },
        { "QueryStationPreselect", "https://data.railgo.zenglingkun.cn/api/station/preselect" },
        { "QueryStationQuery", "https://data.railgo.zenglingkun.cn/api/station/query" },
        { "QueryEmuAssignmentQuery", "https://emu.railgo.zenglingkun.cn/api/car/info" },
        // 正晚点查询 V2
        { "QueryTrainDelay", "https://rg-api.zenglingkun.cn/api/v2/getTrainDelayAll" },
        // 检票口、站台及出站口 V2
        { "QueryPlatformInfo", "https://rg-api.zenglingkun.cn/api/v2/getExit" },
        // 车站大屏 V2
        { "QueryGetBigScreenData", "https://rg-api.zenglingkun.cn/api/v2/getStationBigScreen" },
        // 动车组运行 V2
        { "QueryEmuQuery", "https://emu.railgo.zenglingkun.cn/api/query" },
        // 列车车厢图 V2
        { "QueryCoachPic", "https://rg-api.zenglingkun.cn/api/v2/getCoachPic" },
        // 列车图鉴
        { "QueryTrainAtlas", "https://train.idcmoss.cn/api/model_search.php" },
        { "QueryDownloadEmuImage", "https://tp.railgo.zenglingkun.cn/api" }
    };

    public static string GetDefaultUrl(string methodName)
    {
        return _urlMappings.TryGetValue(methodName, out var url) ? url : null;
    }

    public static bool TryGetDefaultUrl(string methodName, out string url)
    {
        return _urlMappings.TryGetValue(methodName, out url);
    }
}
