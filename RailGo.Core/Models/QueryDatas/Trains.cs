using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json;

namespace RailGo.Core.Models.QueryDatas;

#region 车次预搜索结果
// 车次预选搜索结果
public class TrainPreselectResult
{
    [JsonProperty("fullNumber")]
    public string FullNumber
    {
        get; set;
    }

    [JsonIgnore]
    public string Number
    {
        get
        {
            if (string.IsNullOrEmpty(FullNumber))
                return string.Empty;

            var parts = FullNumber.Split('/');
            return parts.Length > 0 ? parts[0] : FullNumber;
        }
    }

    [JsonIgnore]
    public string Prefix
    {
        get
        {
            if (string.IsNullOrEmpty(FullNumber))
                return "";

            // 提取前缀字母
            if (FullNumber.Length > 0 && char.IsLetter(FullNumber[0]))
            {
                return FullNumber[0].ToString();
            }
            return "";
        }
    }

    [JsonIgnore]
    public string NumberPart
    {
        get
        {
            if (string.IsNullOrEmpty(FullNumber))
                return "";

            // 处理 G1202/G1203 这种情况
            if (FullNumber.Contains('/'))
            {
                var parts = FullNumber.Split('/');
                if (parts.Length == 2 && parts[0].Length > 0 && parts[1].Length > 0)
                {
                    var firstChar1 = parts[0][0];
                    var firstChar2 = parts[1][0];

                    if (char.IsLetter(firstChar1) && char.IsLetter(firstChar2) && firstChar1 == firstChar2)
                    {
                        return $"{parts[0].Substring(1)}/{parts[1].Substring(1)}";
                    }
                }
            }

            // 普通情况：去掉前缀
            if (FullNumber.Length > 0 && char.IsLetter(FullNumber[0]))
            {
                return FullNumber.Substring(1);
            }
            return FullNumber;
        }
    }

    [JsonIgnore]
    public string PrefixColor
    {
        get
        {
            if (string.IsNullOrEmpty(Prefix))
                return "#000000";

            return Prefix.ToUpper() switch
            {
                "G" => "#DC2626", // 红色-高铁
                "D" => "#2563EB", // 蓝色-动车
                "C" => "#16A34A", // 绿色-城际
                "Z" => "#7C3AED", // 紫色-直达
                "T" => "#EA580C", // 橙色-特快
                "K" => "#854D0E", // 棕色-快速
                "S" => "#0891B2", // 青色-市域
                "L" => "#CA8A04", // 黄色-临客
                _ => "#000000"    // 黑色-其他
            };
        }
    }
}
#endregion

#region 车次基本信息
// 车次基本信息
public class Train
{
    [JsonProperty("number")]
    public string Number => NumberFull != null && NumberFull.Count > 0
        ? string.Join("/", NumberFull)
        : null;

    [JsonProperty("numberFull")]
    public List<string> NumberFull
    {
        get; set;
    }

    [JsonProperty("numberKind")]
    public string NumberKind
    {
        get; set;
    }

    [JsonProperty("type")]
    public string Type
    {
        get; set;
    }

    [JsonProperty("timetable")]
    public ObservableCollection<TimetableItem> Timetable
    {
        get; set;
    }

    [JsonProperty("bureauName")]
    public string BureauName
    {
        get; set;
    }

    [JsonProperty("runner")]
    public string Runner
    {
        get; set;
    }

    [JsonProperty("carOwner")]
    public string CarOwner
    {
        get; set;
    }

    [JsonProperty("car")]
    public string Car
    {
        get; set;
    }

    [JsonProperty("rundays")]
    public List<string> Rundays
    {
        get; set;
    }

    [JsonProperty("diagram")]
    public ObservableCollection<TrainDiagram> Diagram
    {
        get; set;
    }
}
#endregion

#region 车次时刻表
// 时刻表项
public class TimetableItem
{
    [JsonProperty("station")]
    public string Station
    {
        get; set;
    }

    [JsonProperty("stationTelecode")]
    public string StationTelecode
    {
        get; set;
    }

    [JsonProperty("trainCode")]
    public string TrainCode
    {
        get; set;
    }

    [JsonProperty("arrive")]
    public string Arrive
    {
        get; set;
    }

    [JsonProperty("depart")]
    public string Depart
    {
        get; set;
    }

    [JsonProperty("distance")]
    public string Distance
    {
        get; set;
    }

    [JsonProperty("speed")]
    public double? Speed
    {
        get; set;
    }

    [JsonProperty("day")]
    public int Day
    {
        get; set;
    }

    [JsonProperty("stopTime")]
    public int StopTime
    {
        get; set;
    }

    [JsonIgnore]
    public DelayInfo DelayInfo
    {
        get; set;
    }

    [JsonIgnore]
    public string DelayDisplay => DelayInfo?.DelayMinutes switch
    {
        null => "--",         
        0 => "正点",    
        > 0 => $"晚点{DelayInfo.DelayMinutes}分钟",  
        < 0 => $"提前{-DelayInfo.DelayMinutes}分钟"  
    };

    [JsonIgnore]
    public string DelayColor => DelayInfo?.DelayMinutes switch
    {
        null => "#000000",   
        0 => "#000000",     
        > 0 => "#c0392b",   
        < 0 => "#27ae60"
    };
}
#endregion

#region 车次交路信息
// 交路信息
public class TrainDiagram
{
    [JsonProperty("number")]
    public string TrainNum
    {
        get; set;
    }

    [JsonProperty("from")]
    public List<string> From
    {
        get; set;
    }

    [JsonProperty("to")]
    public List<string> To
    {
        get; set;
    }

    [JsonIgnore]
    public string FromStation => From?.Count > 0 ? From[0] : null;

    [JsonIgnore]
    public string FromTime => From?.Count > 1 ? From[1] : null;

    [JsonIgnore]
    public string ToStation => To?.Count > 0 ? To[0] : null;

    [JsonIgnore]
    public string ToTime => To?.Count > 1 ? To[1] : null;
}
#endregion

#region 正晚点信息
// 正晚点信息
public class DelayInfo
{
    [JsonProperty("stationName")]
    public string StationName
    {
        get; set;
    }

    [JsonProperty("stationTelegramCode")]
    public string StationTelecode
    {
        get; set;
    }

    [JsonProperty("delayMinutes")]
    public int? DelayMinutes
    {
        get; set;
    }

    [JsonProperty("status")]
    public int? Status
    {
        get; set;
    }

    [JsonProperty("arrivalTime")]
    public string ArrivalTime
    {
        get; set;
    }

    [JsonProperty("departureTime")]
    public string DepartureTime
    {
        get; set;
    }

    [JsonProperty("stopMinutes")]
    public int? StopMinutes
    {
        get; set;
    }

    [JsonProperty("arrivalDate")]
    public string ArrivalDate
    {
        get; set;
    }
}
#endregion

#region 停台检票口信息
// 停台检票口信息
public class PlatformInfo
{
    [JsonProperty("status")]
    public bool Status
    {
        get; set;
    }

    [JsonProperty("data")]
    public PlatformData Data
    {
        get; set;
    }

    [JsonProperty("errorMsg")]
    public string ErrorMsg
    {
        get; set;
    }
}

public class PlatformData
{
    [JsonProperty("platform")]
    public string Platform
    {
        get; set;
    }

    [JsonProperty("wicket")]
    public string Wicket
    {
        get; set;
    }
}
#endregion

#region 正晚点信息
// 正晚点响应
public class DelayResponse
{
    [JsonProperty("data")]
    public List<DelayInfo> Data
    {
        get; set;
    }
}
#endregion