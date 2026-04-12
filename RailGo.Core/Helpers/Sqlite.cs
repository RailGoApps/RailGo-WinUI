using Microsoft.Data.Sqlite;

namespace RailGo.Core.Helpers;

public static class Sqlite
{
    public static bool FieldExists(this SqliteDataReader reader, string fieldName)
    {
        for (int i = 0; i < reader.FieldCount; i++)
        {
            if (reader.GetName(i).Equals(fieldName, StringComparison.OrdinalIgnoreCase))
                return true;
        }
        return false;
    }
}