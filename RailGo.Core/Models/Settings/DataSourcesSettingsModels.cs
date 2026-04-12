using System.Collections.ObjectModel;
using Newtonsoft.Json;

namespace RailGo.Core.Models.Settings;

public class DataSourceMethod
{
    [JsonProperty("name")]
    public string Name
    {
        get; set;
    }

    [JsonProperty("mode")]
    public string Mode
    {
        get; set;
    }

    [JsonProperty("sourceName")]
    public string SourceName
    {
        get; set;
    }

    [JsonIgnore]
    public bool IsOnlineMode
    {
        get => Mode?.ToLower() == "online";
        set
        {
            if (value) Mode = "online";
        }
    }

    [JsonIgnore]
    public bool IsOfflineMode
    {
        get => Mode?.ToLower() == "offline";
        set
        {
            if (value) Mode = "offline";
        }
    }

    [JsonIgnore]
    public string ModeDisplayText => GetModeDisplayText(Mode);

    public string GetModeDisplayText(string mode)
    {
        return mode?.ToLower() switch
        {
            "online" => "在线",
            "offline" => "离线",
            _ => mode ?? "未知"
        };
    }
}

public class DataSourceGroup
{
    [JsonProperty("name")]
    public string Name { get; set; }

    [JsonProperty("data")]
    public ObservableCollection<DataSourceMethod> Data { get; set; } = new ObservableCollection<DataSourceMethod>();
}

public class LocalDatabaseSource
{
    [JsonProperty("name")]
    public string Name { get; set; }

    [JsonProperty("address")]
    public string Address { get; set; } 
}

public class OnlineApiSource
{
    [JsonProperty("name")]
    public string Name { get; set; }

    [JsonProperty("address")]
    public string Address { get; set; }
}

public class OfflineDatabaseVersion
{
    [JsonProperty("installDate")]
    public DateTime InstallDate { get; set; } = DateTime.Now;

    [JsonProperty("version")]
    public string Version { get; set; } = string.Empty;

    [JsonProperty("sequence")]
    public int Sequence { get; set; } = 0;
}

public class GetPathModel
{
    public bool IsOfflineMode { get; set; }
    public string Path { get; set; }
}