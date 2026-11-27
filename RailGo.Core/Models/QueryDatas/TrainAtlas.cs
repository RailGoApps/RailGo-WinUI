using System.Collections.Generic;
using Newtonsoft.Json;

namespace RailGo.Core.Models.QueryDatas;

#region 列车图鉴搜索响应

public class TrainAtlasResponse
{
    [JsonProperty("success")]
    public bool Success
    {
        get; set;
    }

    [JsonProperty("keyword")]
    public string Keyword
    {
        get; set;
    }

    [JsonProperty("total")]
    public int Total
    {
        get; set;
    }

    [JsonProperty("page")]
    public int Page
    {
        get; set;
    }

    [JsonProperty("pages")]
    public int Pages
    {
        get; set;
    }

    [JsonProperty("models")]
    public List<TrainAtlasModel> Models
    {
        get; set;
    }
}

public class TrainAtlasModel
{
    [JsonProperty("id")]
    public int Id
    {
        get; set;
    }

    [JsonProperty("model_code")]
    public string ModelCode
    {
        get; set;
    }

    [JsonProperty("model_name")]
    public string ModelName
    {
        get; set;
    }

    [JsonProperty("model_type")]
    public string ModelType
    {
        get; set;
    }

    [JsonProperty("manufacturer")]
    public string Manufacturer
    {
        get; set;
    }

    [JsonProperty("description")]
    public string Description
    {
        get; set;
    }

    [JsonProperty("total_photos")]
    public int TotalPhotos
    {
        get; set;
    }

    [JsonProperty("interior_photos")]
    public int InteriorPhotos
    {
        get; set;
    }

    [JsonProperty("exterior_photos")]
    public int ExteriorPhotos
    {
        get; set;
    }

    [JsonProperty("reference_photos")]
    public List<TrainAtlasPhoto> ReferencePhotos
    {
        get; set;
    }
}

public class TrainAtlasPhoto
{
    [JsonProperty("photo_id")]
    public int PhotoId
    {
        get; set;
    }

    [JsonProperty("title")]
    public string Title
    {
        get; set;
    }

    [JsonProperty("thumbnail_url")]
    public string ThumbnailUrl
    {
        get; set;
    }

    [JsonProperty("image_url")]
    public string ImageUrl
    {
        get; set;
    }

    [JsonProperty("train_number")]
    public string TrainNumber
    {
        get; set;
    }

    [JsonProperty("station_name")]
    public string StationName
    {
        get; set;
    }

    [JsonProperty("railway_bureau")]
    public string RailwayBureau
    {
        get; set;
    }

    [JsonProperty("shoot_date")]
    public string ShootDate
    {
        get; set;
    }

    [JsonProperty("category")]
    public string Category
    {
        get; set;
    }

    [JsonProperty("likes_count")]
    public int LikesCount
    {
        get; set;
    }

    [JsonProperty("views_count")]
    public int ViewsCount
    {
        get; set;
    }

    [JsonProperty("share_url")]
    public string ShareUrl
    {
        get; set;
    }

    [JsonIgnore]
    public string ModelName
    {
        get; set;
    }
}

#endregion
