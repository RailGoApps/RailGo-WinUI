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

    // 查询日期（yyyyMMdd），用于 V2 车次接口
    [JsonIgnore]
    public string Date
    {
        get; set;
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

    // V2 接口返回的路局简称
    [JsonProperty("bureauShortName")]
    public string BureauShortName
    {
        get; set;
    }

    [JsonIgnore]
    public string DisplayBureau => string.IsNullOrEmpty(BureauShortName) ? BureauName : BureauShortName;

    [JsonProperty("spend")]
    public int? Spend
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

    [JsonProperty("runTime")]
    public int RunTime
    {
        get; set;
    }

    [JsonIgnore]
    public DelayInfo DelayInfo
    {
        get; set;
    }

    // 检票口、站台及出站口信息
    [JsonIgnore]
    public StationExitInfo ExitInfo
    {
        get; set;
    }

    [JsonIgnore]
    public string PlatformDisplay => string.IsNullOrEmpty(ExitInfo?.Platform) ? "--" : ExitInfo.Platform;

    [JsonIgnore]
    public string TicketGateDisplay => ExitInfo?.Entrance != null && ExitInfo.Entrance.Count > 0
        ? string.Join(" / ", ExitInfo.Entrance)
        : "--";

    [JsonIgnore]
    public string StationExitDisplay => ExitInfo?.Exit != null && ExitInfo.Exit.Count > 0
        ? string.Join(" / ", ExitInfo.Exit)
        : "--";

    [JsonIgnore]
    public string DelayDisplay => DelayInfo == null ? "--" : DelayInfo.Display;

    [JsonIgnore]
    public string DelayColor => DelayInfo?.DisplayColor ?? "#000000";

    [JsonIgnore]
    public string? ActualArrivalTime
    {
        get
        {
            if (DelayInfo == null)
                return null;

            if (string.IsNullOrEmpty(DelayInfo.ArrivalTime) || !DelayInfo.DelayMinutes.HasValue)
                return null;

            try
            {
                if (TimeSpan.TryParse(DelayInfo.ArrivalTime, out var arrivalTime))
                {
                    var actualTime = arrivalTime.Add(TimeSpan.FromMinutes(DelayInfo.DelayMinutes.Value));

                    // 处理跨天情况（如果超过24小时）
                    var days = (int)actualTime.TotalDays;
                    var timePart = actualTime.ToString(@"hh\:mm");

                    return days > 0 ? $"+{days} {timePart}" : timePart;
                }
            }
            catch
            {

            }

            return null;
        }
    }

    [JsonIgnore]
    public string? ActualDepartureTime
    {
        get
        {
            if (DelayInfo == null)
                return null;

            if (string.IsNullOrEmpty(DelayInfo.DepartureTime) || !DelayInfo.DelayMinutes.HasValue)
                return null;

            try
            {
                if (TimeSpan.TryParse(DelayInfo.DepartureTime, out var departureTime))
                {
                    var actualTime = departureTime.Add(TimeSpan.FromMinutes(DelayInfo.DelayMinutes.Value));

                    // 处理跨天情况（如果超过24小时）
                    var days = (int)actualTime.TotalDays;
                    var timePart = actualTime.ToString(@"hh\:mm");

                    return days > 0 ? $"+{days} {timePart}" : timePart;
                }
            }
            catch
            {
                
            }

            return null;
        }
    }
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
// 正晚点信息（V2）
public class DelayInfo
{
    [JsonProperty("stationName")]
    public string StationName
    {
        get; set;
    }

    [JsonProperty("stationTelecode")]
    public string StationTelecode
    {
        get; set;
    }

    [JsonProperty("planArriveTime")]
    public string PlanArriveTime
    {
        get; set;
    }

    [JsonProperty("planDepartTime")]
    public string PlanDepartTime
    {
        get; set;
    }

    [JsonProperty("delayStatus")]
    public string DelayStatus
    {
        get; set;
    }

    [JsonProperty("delayStatusCode")]
    public string DelayStatusCode
    {
        get; set;
    }

    // V2 接口返回正晚点的分钟数（绝对值，方向由 delayStatusCode 决定）
    [JsonProperty("delayTime")]
    public int? DelayTime
    {
        get; set;
    }

    [JsonIgnore]
    public string ArrivalTime => PlanArriveTime;

    [JsonIgnore]
    public string DepartureTime => PlanDepartTime;

    [JsonIgnore]
    public bool IsMissing => string.IsNullOrEmpty(DelayStatusCode)
        || DelayStatusCode.Contains("MISSING", System.StringComparison.OrdinalIgnoreCase);

    [JsonIgnore]
    public bool IsPrediction => DelayStatusCode?.EndsWith("_PREDICTION", System.StringComparison.OrdinalIgnoreCase) == true;

    [JsonIgnore]
    public int? DelayMinutes
    {
        get
        {
            if (IsMissing)
            {
                return null;
            }

            var code = DelayStatusCode?.ToUpperInvariant();
            var minutes = DelayTime ?? 0;

            if (code != null && code.StartsWith("DELAY"))
            {
                return minutes;
            }

            if (code != null && code.StartsWith("EARLY"))
            {
                return -minutes;
            }

            if (code != null && code.StartsWith("ON_TIME"))
            {
                return 0;
            }

            return minutes;
        }
    }

    [JsonIgnore]
    public string Display
    {
        get
        {
            if (IsMissing)
            {
                return "未维护";
            }

            var minutes = DelayMinutes ?? 0;
            var prefix = IsPrediction ? "预计" : string.Empty;

            if (minutes > 0)
            {
                return $"{prefix}晚点{minutes}分钟";
            }

            if (minutes < 0)
            {
                return $"{prefix}早点{-minutes}分钟";
            }

            return IsPrediction ? "预计正点" : "正点";
        }
    }

    [JsonIgnore]
    public string DisplayColor
    {
        get
        {
            if (IsMissing)
            {
                return "#8a8a8e";
            }

            var minutes = DelayMinutes ?? 0;

            if (minutes > 0)
            {
                return "#c0392b";
            }

            if (minutes < 0)
            {
                return "#27ae60";
            }

            return "#107c10";
        }
    }
}
#endregion

#region 检票口、站台及出站口信息
// 检票口、站台及出站口信息（V2）
public class StationExitInfo
{
    [JsonProperty("entrance")]
    public List<string> Entrance
    {
        get; set;
    }

    [JsonProperty("platform")]
    public string Platform
    {
        get; set;
    }

    [JsonProperty("exit")]
    public List<string> Exit
    {
        get; set;
    }
}
#endregion

#region 列车车厢图信息
// 列车车厢图（V2）
public class CoachPicInfo
{
    [JsonProperty("carType")]
    public string CarType
    {
        get; set;
    }

    [JsonProperty("carCode")]
    public string CarCode
    {
        get; set;
    }

    [JsonProperty("coachPicList")]
    public List<CoachPicItem> CoachPicList
    {
        get; set;
    }

    [JsonProperty("coachDetailPicList")]
    public List<CoachPicItem> CoachDetailPicList
    {
        get; set;
    }

    [JsonProperty("trainStyle")]
    public string TrainStyle
    {
        get; set;
    }

    [JsonProperty("carInfo")]
    public List<CoachCarInfoItem> CarInfo
    {
        get; set;
    }

    [JsonProperty("carPic")]
    public string CarPic
    {
        get; set;
    }
}

public class CoachCarInfoItem
{
    [JsonProperty("pictureValue")]
    public string PictureValue
    {
        get; set;
    }

    [JsonProperty("picOrder")]
    public int PicOrder
    {
        get; set;
    }

    [JsonProperty("pictureUrl")]
    public string PictureUrl
    {
        get; set;
    }

    [JsonProperty("pictureName")]
    public string PictureName
    {
        get; set;
    }
}

public class CoachPicItem
{
    [JsonProperty("picOrder")]
    public int PicOrder
    {
        get; set;
    }

    [JsonProperty("pictureUrl")]
    public string PictureUrl
    {
        get; set;
    }

    [JsonProperty("pictureName")]
    public string PictureName
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