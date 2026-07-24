using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace RailGo.Core.Models.QueryDatas;

#region 车站预搜索结果
// 车站预选搜索结果
public class StationPreselectResult
{
    [JsonProperty("name")]
    public string Name
    {
        get; set;
    }

    [JsonProperty("telecode")]
    public string TeleCode
    {
        get; set;
    }

    [JsonProperty("pinyin")]
    public string Pinyin
    {
        get; set;
    }

    [JsonProperty("pinyinTriple")]
    public string PinyinTriple
    {
        get; set;
    }

    [JsonProperty("type")]
    public List<string> Type
    {
        get; set;
    }

    [JsonProperty("bureau")]
    public string Bureau
    {
        get; set;
    }

    [JsonProperty("belong")]
    public string Belong
    {
        get; set;
    }

    [JsonProperty("city")]
    public string City
    {
        get; set;
    }

    [JsonProperty("level")]
    public string Level
    {
        get; set;
    }

    [JsonProperty("lines")]
    public List<string> Lines
    {
        get; set;
    }

    [JsonProperty("province")]
    public string Province
    {
        get; set;
    }

    [JsonProperty("tmism")]
    public string Tmism
    {
        get; set;
    }

    [JsonProperty("trainList")]
    public List<string> TrainList
    {
        get; set;
    }
}
#endregion

#region 车站基本信息查询及请求响应
// 车站查询响应
public class StationQueryResponse
{
    [JsonProperty("data")]
    public Station Data
    {
        get; set;
    }

    [JsonProperty("trains")]
    public ObservableCollection<StationTrain> Trains
    {
        get; set;
    }
}

// 车站基本信息
public class Station
{
    [JsonProperty("name")]
    public string Name
    {
        get; set;
    }

    [JsonProperty("telecode")]
    public string Telecode
    {
        get; set;
    }

    [JsonProperty("pinyin")]
    public string Pinyin
    {
        get; set;
    }

    [JsonProperty("pinyinTriple")]
    public string PinyinTriple
    {
        get; set;
    }

    [JsonProperty("type")]
    public List<string> Type
    {
        get; set;
    }

    [JsonProperty("bureau")]
    public string Bureau
    {
        get; set;
    }

    [JsonProperty("belong")]
    public string Belong
    {
        get; set;
    }

    [JsonProperty("trainList")]
    public List<string> TrainList
    {
        get; set;
    }

    [JsonProperty("city")]
    public string City
    {
        get; set;
    }

    [JsonProperty("level")]
    public string Level
    {
        get; set;
    }

    [JsonProperty("province")]
    public string Province
    {
        get; set;
    }

    [JsonProperty("lines")]
    public List<string> Lines
    {
        get; set;
    }

    [JsonProperty("tmism")]
    public string Tmism
    {
        get; set;
    }
}
#endregion

#region 车站大屏数据查询
public class BigScreenData
{
    [JsonProperty("data")]
    public ObservableCollection<StationScreenItem> Data
    {
        get; set;
    }

    [JsonProperty("msg")]
    public string Msg
    {
        get; set;
    }

    [JsonProperty("success")]
    public bool Success
    {
        get; set;
    }
}

public class StationScreenItem
{
    [JsonProperty("bigScreenPort")]
    public List<string> BigScreenPort
    {
        get; set;
    }

    [JsonProperty("bigScreenStatusCode")]
    public string BigScreenStatusCode
    {
        get; set;
    }

    [JsonProperty("bigScreenStatus")]
    public string BigScreenStatus
    {
        get; set;
    }

    [JsonProperty("trainNum")]
    public string TrainNumber
    {
        get; set;
    }

    [JsonProperty("time")]
    public string ScheduleTime
    {
        get; set;
    }

    [JsonProperty("timeDelay")]
    public int TimeDelay
    {
        get; set;
    }

    [JsonProperty("trainStartStation")]
    public string FromStation
    {
        get; set;
    }

    [JsonProperty("trainEndStation")]
    public string ToStation
    {
        get; set;
    }

    [JsonIgnore]
    public string DisplayTime
    {
        get
        {
            if (DateTime.TryParse(ScheduleTime, out var dateTime))
                return dateTime.ToString("HH:mm");
            return ScheduleTime;
        }
    }

    [JsonIgnore]
    public string DisplayWaitingRoom
    {
        get
        {
            if (BigScreenPort == null || BigScreenPort.Count == 0)
                return string.Empty;
            return BigScreenPort[0];
        }
    }

    [JsonIgnore]
    public string DisplayTicketGate
    {
        get
        {
            if (BigScreenPort == null || BigScreenPort.Count <= 1)
                return string.Empty;
            return BigScreenPort[1];
        }
    }

    [JsonIgnore]
    public string Status
    {
        get
        {
            return BigScreenStatus ?? BigScreenStatusCode ?? string.Empty;
        }
    }

    [JsonIgnore]
    public string WaitingArea
    {
        get
        {
            if (BigScreenPort == null || BigScreenPort.Count == 0)
                return string.Empty;
            return string.Join("/", BigScreenPort);
        }
    }
}
#endregion

#region 途径车次
// 途径车次
public class StationTrain
{
    [JsonProperty("arrive")]
    public string ArriveTime
    {
        get; set;
    }

    [JsonProperty("depart")]
    public string DepartTime
    {
        get; set;
    }

    [JsonProperty("fromStation")]
    public StationTrainStationInfo FromStation
    {
        get; set;
    }

    [JsonProperty("indexStopThere")]
    public int IndexStopThere
    {
        get; set;
    }

    [JsonProperty("number")]
    public string Number
    {
        get; set;
    }

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

    [JsonProperty("stopTime")]
    public int StopTime
    {
        get; set;
    }

    [JsonProperty("toStation")]
    public StationTrainStationInfo ToStation
    {
        get; set;
    }

    [JsonProperty("type")]
    public string Type
    {
        get; set;
    }

    [JsonIgnore]
    public string DisplayFullNumber => NumberFull != null && NumberFull.Any()
        ? string.Join("/", NumberFull)
        : Number ?? string.Empty;
}

// 车次中的车站信息
public class StationTrainStationInfo
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
}
#endregion
