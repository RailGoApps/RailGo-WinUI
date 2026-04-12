using Microsoft.Data.Sqlite;
using System.Data;
using Newtonsoft.Json;
using RailGo.Core.Models;

namespace RailGo.Core.Query.Offline;

public abstract class BaseOfflineService
{
    protected readonly string _databasePath;

    protected BaseOfflineService(string databasePath)
    {
        _databasePath = databasePath;
    }

    protected async Task<List<T>> QueryAsync<T>(string sql, Func<SqliteDataReader, T> mapper, params SqliteParameter[] parameters)
    {
        var results = new List<T>();

        using (var connection = new SqliteConnection($"Data Source={_databasePath}"))
        using (var command = new SqliteCommand(sql, connection))
        {
            await connection.OpenAsync();

            if (parameters != null)
            {
                command.Parameters.AddRange(parameters);
            }

            using (var reader = await command.ExecuteReaderAsync())
            {
                while (await reader.ReadAsync())
                {
                    results.Add(mapper(reader));
                }
            }
        }

        return results;
    }

    protected string SerializeToJson(object obj)
    {
        return JsonConvert.SerializeObject(obj, Formatting.Indented);
    }
}