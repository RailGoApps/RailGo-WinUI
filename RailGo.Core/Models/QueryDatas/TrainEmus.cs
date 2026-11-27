using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json;

namespace RailGo.Core.Models.QueryDatas;

#region 动车组运行交路
// 动车组运行交路（V2）
public class EmuOperation
{
    [JsonProperty("runDate")]
    public string Date
    {
        get; set;
    }

    [JsonProperty("trainNum")]
    public string TrainNo
    {
        get; set;
    }

    // V2 接口返回的车组号数组，重联时会有多个
    [JsonProperty("trainCode")]
    public List<string> TrainCodes
    {
        get; set;
    }

    [JsonProperty("coupling")]
    public bool Coupling
    {
        get; set;
    }

    [JsonIgnore]
    public string EmuNo => TrainCodes != null && TrainCodes.Count > 0
        ? string.Join("/", TrainCodes)
        : null;

    // 动车组型号（从EmuNo中提取）
    [JsonIgnore]
    public string EmuNoModel
    {
        get
        {
            if (string.IsNullOrEmpty(EmuNo))
                return null;

            var first = EmuNo.Split('/')[0];

            // 提取型号部分（去掉最后4位数字）
            if (first.Length > 4)
            {
                return first.Substring(0, first.Length - 4).TrimEnd('-');
            }
            return first;
        }
    }

    // 动车组车号（从EmuNo中提取最后4位数字，重联时用 / 连接）
    [JsonIgnore]
    public string EmuNoCode
    {
        get
        {
            if (string.IsNullOrEmpty(EmuNo))
                return null;

            return string.Join("/", EmuNo.Split('/').Select(code => code.Length >= 4 ? code.Substring(code.Length - 4) : code));
        }
    }

    // 完整格式：车型-车号
    [JsonIgnore]
    public string EmuNoFull
    {
        get
        {
            if (string.IsNullOrEmpty(EmuNo))
                return null;

            return $"{EmuNoModel}-{EmuNoCode}";
        }
    }
}

// 动车组运行查询响应（V2）
public class EmuQueryResponse
{
    [JsonProperty("success")]
    public bool Success
    {
        get; set;
    }

    [JsonProperty("data")]
    public List<EmuOperation> Data
    {
        get; set;
    }
}
#endregion

#region 动车组配属查询请求响应及信息
// 动车组配属信息响应 (GET /api/car/info?id=车组号)
public class EmuCarInfoResponse
{
    [JsonProperty("success")]
    public bool Success
    {
        get; set;
    }

    [JsonProperty("data")]
    public EmuCarInfo Data
    {
        get; set;
    }
}

// 动车组配属信息
public class EmuCarInfo
{
    [JsonProperty("id")]
    public string Id
    {
        get; set;
    }

    [JsonProperty("model")]
    public string Model
    {
        get; set;
    }

    [JsonProperty("bureau")]
    public string Bureau
    {
        get; set;
    }

    [JsonProperty("depot")]
    public string Depot
    {
        get; set;
    }

    [JsonProperty("train_depot")]
    public string TrainDepot
    {
        get; set;
    }

    [JsonProperty("trainset_manufacturer")]
    public string TrainsetManufacturer
    {
        get; set;
    }

    [JsonProperty("sub_model")]
    public string SubModel
    {
        get; set;
    }
}
#endregion
