using System.Collections.Generic;
using Newtonsoft.Json;

namespace RailGo.Core.Models.Settings;

// 版本信息模型
public class VersionInfo
{
    [JsonProperty("latest_db")]
    public int LatestDb
    {
        get; set;
    }

    [JsonProperty("db")]
    public string Db
    {
        get; set;
    }

    [JsonProperty("latest_pack")]
    public int LatestPack
    {
        get; set;
    }

    [JsonProperty("pack")]
    public string Pack
    {
        get; set;
    }
}

// 下载URL响应
public class DownloadUrlResponse
{
    [JsonProperty("url")]
    public string Url
    {
        get; set;
    }
}

// 动车组图片贡献者响应
public class EmuContributorResponse
{
    [JsonProperty("code")]
    public int Code
    {
        get; set;
    }

    [JsonProperty("data")]
    public List<string> Data
    {
        get; set;
    }
}