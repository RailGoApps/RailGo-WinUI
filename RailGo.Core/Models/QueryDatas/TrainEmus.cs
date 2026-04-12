using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json;

namespace RailGo.Core.Models.QueryDatas;

#region 动车组运行交路
// 动车组运行交路
public class EmuOperation
{
    [JsonProperty("date")]
    public string Date
    {
        get; set;
    }

    [JsonProperty("train_no")]
    public string TrainNo
    {
        get; set;
    }

    [JsonProperty("emu_no")]
    public string EmuNo
    {
        get; set;
    }

    // 动车组型号（从EmuNo中提取）
    [JsonIgnore]
    public string EmuNoModel
    {
        get
        {
            if (string.IsNullOrEmpty(EmuNo))
                return null;

            // 提取型号部分（去掉最后4位数字）
            if (EmuNo.Length > 4)
            {
                return EmuNo.Substring(0, EmuNo.Length - 4);
            }
            return EmuNo;
        }
    }

    // 动车组车号（从EmuNo中提取最后4位数字）
    [JsonIgnore]
    public string EmuNoCode
    {
        get
        {
            if (string.IsNullOrEmpty(EmuNo))
                return null;

            // 提取最后4位数字作为车号
            if (EmuNo.Length >= 4)
            {
                return EmuNo.Substring(EmuNo.Length - 4);
            }
            return EmuNo;
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
#endregion

#region 动车组配属查询请求响应及信息
// 动车组配属查询响应
public class EmuAssignmentResponse
{
    [JsonProperty("code")]
    public int Code
    {
        get; set;
    }

    [JsonProperty("data")]
    public EmuAssignmentPagedData Data
    {
        get; set;
    }

    [JsonProperty("message")]
    public string Message
    {
        get; set;
    }
}

// 分页数据包装（动车组配属查询响应）
public class EmuAssignmentPagedData
{
    [JsonProperty("cursor")]
    public int Cursor
    {
        get; set;
    }

    [JsonProperty("count")]
    public int Count
    {
        get; set;
    }

    [JsonProperty("hasMore")]
    public bool HasMore
    {
        get; set;
    }

    [JsonProperty("totalCount")]
    public int TotalCount
    {
        get; set;
    }

    [JsonProperty("data")]
    public ObservableCollection<EmuAssignment> Data
    {
        get; set;
    }
}

// 动车组配属信息
public class EmuAssignment
{
    [JsonProperty("trainModel")]
    public string TrainModel
    {
        get; set;
    }

    [JsonProperty("trainSerialNumber")]
    public string TrainSerialNumber
    {
        get; set;
    }

    [JsonProperty("bureau")]
    public string Bureau
    {
        get; set;
    }

    [JsonProperty("department")]
    public string Department
    {
        get; set;
    }

    [JsonProperty("manufacturer")]
    public string Manufacturer
    {
        get; set;
    }

    [JsonProperty("comment")]
    public string Comment
    {
        get; set;
    }
}
#endregion
