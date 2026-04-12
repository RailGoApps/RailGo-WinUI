using System.Collections.Generic;
using System.Threading.Tasks;
using RailGo.Core.Models.Settings;
using RailGo.Core.Helpers;

namespace RailGo.Core.Query.Online;

public class SettingsAPIService
{
    private const string StateBaseUrl = "https://api.state.railgo.zenglingkun.cn";
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
    /// 获取系统公告
    /// </summary>
    public static async Task<List<string>> GetNoticesAsync()
    {
        var url = $"{StateBaseUrl}/notice";
        return await HttpService.GetAsync<List<string>>(url);
    }

    /// <summary>
    /// 获取轮播图URL列表
    /// </summary>
    public static async Task<List<string>> GetBannerImagesAsync()
    {
        var url = $"{StateBaseUrl}/pic";
        return await HttpService.GetAsync<List<string>>(url);
    }
}