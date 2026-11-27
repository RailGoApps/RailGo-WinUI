namespace RailGo.Core.Models.Settings;

/// <summary>
/// 鸣谢名单中的成员（开发、特别鸣谢、贡献者）
/// </summary>
public class CreditPerson
{
    public string Name { get; set; }

    public string Avatar { get; set; }
}

/// <summary>
/// 鸣谢页中的数据源信息
/// </summary>
public class DataSourceCredit
{
    public string Name { get; set; }

    public string Website { get; set; }

    public string Features { get; set; }
}
