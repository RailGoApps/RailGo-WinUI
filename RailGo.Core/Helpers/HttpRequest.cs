using System;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json;

namespace RailGo.Core.Helpers;

public class HttpService
{
    private static readonly HttpClient _httpClient;
    private static readonly JsonSerializerSettings _jsonSettings;

    static HttpService()
    {
        _httpClient = new HttpClient
        {
            Timeout = TimeSpan.FromSeconds(30)
        };
        _httpClient.DefaultRequestHeaders.Add("User-Agent", "RailGo-Desktop/1.0");

        _jsonSettings = new JsonSerializerSettings
        {
            NullValueHandling = NullValueHandling.Ignore,
            DateFormatString = "yyyy-MM-dd HH:mm:ss"
        };
    }

    /// <summary>
    /// GET 请求
    /// </summary>
    public static async Task<T> GetAsync<T>(string url)
    {
        try
        {
            var response = await _httpClient.GetAsync(url);
            response.EnsureSuccessStatusCode();

            var json = await response.Content.ReadAsStringAsync();
            return JsonConvert.DeserializeObject<T>(json, _jsonSettings);
        }
        catch (Exception ex)
        {
            throw new HttpRequestException($"GET请求失败: {url}", ex);
        }
    }

    /// <summary>
    /// POST 请求
    /// </summary>
    public static async Task<T> PostAsync<T>(string url, object data)
    {
        try
        {
            var json = JsonConvert.SerializeObject(data, _jsonSettings);
            var content = new StringContent(json, Encoding.UTF8, "application/json");

            var response = await _httpClient.PostAsync(url, content);
            response.EnsureSuccessStatusCode();

            var responseJson = await response.Content.ReadAsStringAsync();
            return JsonConvert.DeserializeObject<T>(responseJson, _jsonSettings);
        }
        catch (Exception ex)
        {
            throw new HttpRequestException($"POST请求失败: {url}", ex);
        }
    }

    public static async Task<T> PostFormAsync<T>(string url, IEnumerable<KeyValuePair<string, string>> formData)
    {
        try
        {
            var content = new FormUrlEncodedContent(formData);

            var response = await _httpClient.PostAsync(url, content);
            response.EnsureSuccessStatusCode();

            var responseJson = await response.Content.ReadAsStringAsync();
            return JsonConvert.DeserializeObject<T>(responseJson, _jsonSettings);
        }
        catch (Exception ex)
        {
            throw new HttpRequestException($"POST表单请求失败: {url}", ex);
        }
    }

    /// <summary>
    /// 下载文件
    /// </summary>
    public static async Task<byte[]> DownloadFileAsync(string url)
    {
        try
        {
            var downloadClient = new HttpClient
            {
                Timeout = TimeSpan.FromMinutes(5)
            };
            downloadClient.DefaultRequestHeaders.Add("User-Agent", "RailGo-Desktop/1.0");
            var result = await downloadClient.GetByteArrayAsync(url);
            return result;
        }
        catch (Exception ex)
        {
            throw new HttpRequestException($"文件下载失败: {url}", ex);
        }
    }
}
