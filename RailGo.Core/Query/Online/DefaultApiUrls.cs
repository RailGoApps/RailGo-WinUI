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
        { "QueryTrainQuery", "https://data.railgo.zenglingkun.cn/api/train/query" },
        { "QueryStationToStationQuery", "https://data.railgo.zenglingkun.cn/api/train/sts_query" },
        { "QueryStationPreselect", "https://data.railgo.zenglingkun.cn/api/station/preselect" },
        { "QueryStationQuery", "https://data.railgo.zenglingkun.cn/api/station/query" },
        { "QueryEmuAssignmentQuery", "https://delay.data.railgo.zenglingkun.cn/api/trainAssignment/queryEmu" },
        // 正晚点查询改用 V2 API（原 delay 端点已不可用）
        { "QueryTrainDelay", "https://rg-api.zenglingkun.cn/api/v2/getTrainDelay" },
        // 检票口查询（保持原 12306 端点）
        { "QueryPlatformInfo", "https://mobile.12306.cn/wxxcx/wechat/bigScreen/getExit" },
        // 车站大屏改用 V2 API（原 screen 端点已不可用）
        { "QueryGetBigScreenData", "https://rg-api.zenglingkun.cn/api/v2/getStationBigScreen" },
        { "QueryEmuQuery", "https://api.rail.re/emu" },
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
