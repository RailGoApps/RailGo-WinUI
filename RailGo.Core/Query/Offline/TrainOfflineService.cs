using Microsoft.Data.Sqlite;
using System.Collections.ObjectModel;
using RailGo.Core.Helpers;
using RailGo.Core.Models.QueryDatas;
using Newtonsoft.Json;
using System.Diagnostics;


namespace RailGo.Core.Query.Offline;

public class TrainOfflineService : BaseOfflineService
{
    public TrainOfflineService(string databasePath) : base(databasePath) { }

    /// <summary>
    /// 车次预选搜索（离线版）
    /// </summary>
    public async Task<string> TrainPreselectAsync(string keyword)
    {
        string sql = @"
        SELECT DISTINCT numberFull 
        FROM trains 
        WHERE number LIKE @keyword 
           OR numberFull LIKE @keyword";

        var parameters = new[]
        {
            new SqliteParameter("@keyword", $"%{keyword}%")
        };

        var results = await QueryAsync(sql, reader =>
        {
            var numberFullJson = reader["numberFull"].ToString();
            if (!string.IsNullOrEmpty(numberFullJson) && numberFullJson.StartsWith("["))
            {
                var numberList = JsonConvert.DeserializeObject<List<string>>(numberFullJson);
                return string.Join("/", numberList);
            }
            return string.Empty;
        }, parameters);

        var stringArray = results.Where(r => !string.IsNullOrEmpty(r)).ToList();
        return SerializeToJson(stringArray);
    }

    /// <summary>
    /// 车次详情查询（离线版）
    /// </summary>
    public async Task<string> TrainQueryAsync(string trainNumber)
    {
        try
        {
            string sql = @"
            SELECT * FROM trains 
            WHERE number = @trainNumber 
               OR json_extract(numberFull, '$[0]') = @trainNumber
               OR json_extract(numberFull, '$[1]') = @trainNumber
               OR json_extract(numberFull, '$[2]') = @trainNumber";

            var parameters = new[]
            {
            new SqliteParameter("@trainNumber", trainNumber),
            };

            var trains = await QueryAsync(sql, reader =>
            {
                return new Train
                {
                    NumberFull = reader.FieldExists("numberFull") ?
                        JsonConvert.DeserializeObject<List<string>>(reader["numberFull"].ToString() ?? "[]") :
                        new List<string>(),

                    NumberKind = reader.FieldExists("numberKind") ? reader["numberKind"]?.ToString() : null,
                    Type = reader.FieldExists("type") ? reader["type"]?.ToString() : null,
                    BureauName = reader.FieldExists("bureauName") ? reader["bureauName"]?.ToString() : null,
                    Runner = reader.FieldExists("runner") ? reader["runner"]?.ToString() : null,
                    CarOwner = reader.FieldExists("carOwner") ? reader["carOwner"]?.ToString() : null,
                    Car = reader.FieldExists("car") ? reader["car"]?.ToString() : null,

                    Rundays = reader.FieldExists("randays") ?
                        JsonConvert.DeserializeObject<List<string>>(reader["randays"]?.ToString() ?? "[]") :
                        new List<string>(),

                    Timetable = reader.FieldExists("timetable") ?
                        JsonConvert.DeserializeObject<ObservableCollection<TimetableItem>>(reader["timetable"]?.ToString() ?? "[]") :
                        new ObservableCollection<TimetableItem>(),

                    Diagram = reader.FieldExists("diagram") ?
                        JsonConvert.DeserializeObject<ObservableCollection<TrainDiagram>>(reader["diagram"]?.ToString() ?? "[]") :
                        new ObservableCollection<TrainDiagram>()
                };
            }, parameters);

            var validTrains = trains.Where(t => t != null).ToList();
            var train = validTrains.FirstOrDefault();
            return SerializeToJson(train);
        }
        catch (Exception ex)
        {
            Trace.WriteLine($"TrainQueryAsync 错误: {ex.Message}");
            return "null";
        }
    }

    /// <summary>
    /// 站站查询（离线版）
    /// </summary>
    public async Task<string> StationToStationQueryAsync(string from, string to, string date)
    {
        string sql = @"
            SELECT t.* 
            FROM trains t
            WHERE EXISTS (
                SELECT 1 FROM json_each(t.timetable) AS stop1
                WHERE json_extract(stop1.value, '$.station') = @fromStation
            )
            AND EXISTS (
                SELECT 1 FROM json_each(t.timetable) AS stop2
                WHERE json_extract(stop2.value, '$.station') = @toStation
            )";

        var parameters = new[]
        {
            new SqliteParameter("@fromStation", from),
            new SqliteParameter("@toStation", to)
        };

        var trains = await QueryAsync(sql, reader => new TrainRunInfo
        {
            NumberFull = JsonConvert.DeserializeObject<ObservableCollection<string>>(reader["numberFull"].ToString() ?? "[]"),
            NumberKind = reader["numberKind"].ToString(),
            Type = reader["type"].ToString(),
            BureauName = reader["bureauName"].ToString(),
            Timetable = JsonConvert.DeserializeObject<ObservableCollection<TimetableItem>>(reader["timetable"].ToString() ?? "[]")
        }, parameters);

        return SerializeToJson(trains);
    }
}