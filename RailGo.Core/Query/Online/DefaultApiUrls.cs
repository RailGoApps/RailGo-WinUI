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
        { "QueryTrainDelay", "https://delay.data.railgo.zenglingkun.cn/api/trainDetails/queryTrainDelayDetails" },
        { "QueryPlatformInfo", "https://mobile.12306.cn/wxxcx/wechat/bigScreen/getExit" },
        { "QueryGetBigScreenData", "https://screen.data.railgo.zenglingkun.cn" },
        { "QueryEmuQuery", "https://emu.data.railgo.zenglingkun.cn" },
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
