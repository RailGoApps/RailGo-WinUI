namespace RailGo.Core.Models.Settings;

// 下载进度
public class DownloadProgress
{
    public string Status { get; set; }
    public int Percentage { get; set; }
}

// 数据库信息
public class DatabaseInfo
{
    public string Path { get; set; }
    public long FileSize { get; set; }
    public System.DateTime LastModified { get; set; }
    public bool Exists { get; set; }
}

// API V2 通用响应包装
public class V2ApiResponse<T>
{
    [Newtonsoft.Json.JsonProperty("data")]
    public T Data { get; set; }

    [Newtonsoft.Json.JsonProperty("msg")]
    public string Msg { get; set; }

    [Newtonsoft.Json.JsonProperty("success")]
    public bool Success { get; set; }
}
