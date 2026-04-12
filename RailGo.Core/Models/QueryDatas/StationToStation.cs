using Newtonsoft.Json;
using System.Collections.ObjectModel;

namespace RailGo.Core.Models.QueryDatas
{
    public class TrainRunInfo
    {
        [JsonProperty("bureauName")]
        public string BureauName
        {
            get; set;
        }

        [JsonProperty("car")]
        public string Car
        {
            get; set;
        }

        [JsonProperty("carOwner")]
        public string CarOwner
        {
            get; set;
        }

        [JsonProperty("dayDiff")]
        public int DayDiff
        {
            get; set;
        }

        [JsonProperty("diagram")]
        public ObservableCollection<object> Diagram
        {
            get; set;
        }

        [JsonProperty("fromDepart")]
        public string FromDepart
        {
            get; set;
        }

        [JsonProperty("fromPos")]
        public int FromPos
        {
            get; set;
        }

        [JsonProperty("fromStationTelecode")]
        public string FromStationTelecode
        {
            get; set;
        }

        [JsonProperty("number")]
        public string Number
        {
            get; set;
        }

        [JsonProperty("numberFull")]
        public ObservableCollection<string> NumberFull
        {
            get; set;
        }

        [JsonProperty("numberKind")]
        public string NumberKind
        {
            get; set;
        }

        [JsonProperty("passTime")]
        public string PassTime
        {
            get; set;
        }

        [JsonProperty("rundays")]
        public ObservableCollection<string> RunDays
        {
            get; set;
        }

        [JsonProperty("runner")]
        public string Runner
        {
            get; set;
        }

        [JsonProperty("timetable")]
        public ObservableCollection<TimetableItem> Timetable
        {
            get; set;
        }

        [JsonProperty("toArrive")]
        public string ToArrive
        {
            get; set;
        }

        [JsonProperty("toPos")]
        public int ToPos
        {
            get; set;
        }

        [JsonProperty("toStationTelecode")]
        public string ToStationTelecode
        {
            get; set;
        }

        [JsonProperty("type")]
        public string Type
        {
            get; set;
        }

        [JsonIgnore]
        public string FromStationName
        {
            get
            {
                if (string.IsNullOrEmpty(FromStationTelecode) || Timetable == null)
                    return string.Empty;

                var station = Timetable.FirstOrDefault(t =>
                    t.StationTelecode == FromStationTelecode);
                return station?.Station ?? string.Empty;
            }
        }

        [JsonIgnore]
        public string ToStationName
        {
            get
            {
                if (string.IsNullOrEmpty(ToStationTelecode) || Timetable == null)
                    return string.Empty;

                var station = Timetable.FirstOrDefault(t =>
                    t.StationTelecode == ToStationTelecode);
                return station?.Station ?? string.Empty;
            }
        }

        [JsonIgnore]
        public string ToArriveTime
        {
            get
            {
                if (string.IsNullOrEmpty(FromDepart) || string.IsNullOrEmpty(PassTime))
                    return string.Empty;
                try
                {
                    var fromTime = TimeSpan.Parse(FromDepart);
                    int hours = 0;
                    int minutes = 0;
                    var match = System.Text.RegularExpressions.Regex.Match(PassTime, @"(\d+)时(\d+)分");
                    if (match.Success)
                    {
                        hours = int.Parse(match.Groups[1].Value);
                        minutes = int.Parse(match.Groups[2].Value);
                    }
                    else
                    {
                        throw new FormatException("正则表达式匹配失败");
                    }
                    var arriveTime = fromTime.Add(new TimeSpan(hours, minutes, 0));
                    if (arriveTime.Days > 0)
                    {
                        return $"{arriveTime:hh\\:mm}(+{arriveTime.Days})";
                    }
                    return arriveTime.ToString("hh\\:mm");
                }
                catch
                {
                    return string.Empty;
                    throw;
                }
            }
        }
    }
}
