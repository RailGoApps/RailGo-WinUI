using System.Collections.Generic;
using System.Threading.Tasks;
using RailGo.Core.Models.Settings;
using RailGo.Core.Helpers;

namespace RailGo.Core.Query.Online;

public class SettingsAPIService
{
    private const string GatewayBaseUrl = "https://gateway.zenglingkun.cn";
    private const string TpBaseUrl = "https://tp.railgo.zenglingkun.cn/api";

    /// <summary>
    /// 获取动车组图片贡献者列表
    /// </summary>
    public static async Task<List<string>> GetEmuImageContributorsAsync()
    {
        var url = $"{TpBaseUrl}/user";
        var response = await HttpService.GetAsync<EmuContributorResponse>(url);
        return response?.Data;
    }

    /// <summary>
    /// 获取系统公告（失败时抛出异常，由调用方降级到本地公告）
    /// </summary>
    public static async Task<List<string>> GetNoticesAsync()
    {
        var url = $"{GatewayBaseUrl}/api/v2/notice";
        return await HttpService.GetAsync<List<string>>(url);
    }

    /// <summary>
    /// 获取首页图片广告列表
    /// </summary>
    public static async Task<List<PicAdItem>> GetPicAdsAsync()
    {
        var url = $"{GatewayBaseUrl}/api/v2/pic_ad";
        return await HttpService.GetAsync<List<PicAdItem>>(url);
    }
}
