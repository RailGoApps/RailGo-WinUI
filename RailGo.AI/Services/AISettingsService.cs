using System.Net.Http.Json;
using System.Text.Json;
using RailGo.AI.Models;

namespace RailGo.AI.Services;

/// <summary>
/// Communicates with the RailGPT Python backend's settings API
/// to fetch and save API configuration (provider, API keys).
/// </summary>
public class AISettingsService
{
    private readonly HttpClient _httpClient;
    private readonly IPythonProcessManager _processManager;

    public AISettingsService(HttpClient httpClient, IPythonProcessManager processManager)
    {
        _httpClient = httpClient;
        _processManager = processManager;
    }

    private string BaseUrl => _processManager.BaseUrl;

    /// <summary>
    /// Fetch current RailGPT API settings from the Python backend.
    /// Returns null if the backend is unreachable.
    /// </summary>
    public async Task<AISettingsPayload?> GetSettingsAsync()
    {
        if (!_processManager.IsRunning) return null;

        try
        {
            using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
            var resp = await _httpClient.GetAsync($"{BaseUrl}/api/settings", cts.Token);
            if (!resp.IsSuccessStatusCode) return null;

            return await resp.Content.ReadFromJsonAsync<AISettingsPayload>(
                new JsonSerializerOptions { PropertyNameCaseInsensitive = true });
        }
        catch
        {
            return null;
        }
    }

    /// <summary>
    /// Save API key settings to the Python backend.
    /// Returns the updated payload on success, or throws on error.
    /// </summary>
    public async Task<AISettingsPayload?> SaveApiSettingsAsync(
        string provider,
        string? primaryApiKey,
        string? thinkingApiKey)
    {
        if (!_processManager.IsRunning)
            throw new InvalidOperationException("RailGPT 后端未运行");

        var body = new Dictionary<string, object> { ["provider"] = provider };
        if (primaryApiKey != null) body["primary_api_key"] = primaryApiKey;
        if (thinkingApiKey != null) body["thinking_api_key"] = thinkingApiKey;

        using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        var resp = await _httpClient.PutAsJsonAsync($"{BaseUrl}/api/settings/api", body, cts.Token);

        if (!resp.IsSuccessStatusCode)
        {
            var error = await resp.Content.ReadFromJsonAsync<JsonElement>();
            var msg = error.TryGetProperty("error", out var e) ? e.GetString() : "保存失败";
            throw new InvalidOperationException(msg ?? "保存 API 设置失败");
        }

        return await resp.Content.ReadFromJsonAsync<AISettingsPayload>(
            new JsonSerializerOptions { PropertyNameCaseInsensitive = true });
    }

    /// <summary>
    /// Delete the API key for a given slot.
    /// </summary>
    public async Task<AISettingsPayload?> DeleteApiKeyAsync(string slot = "primary")
    {
        if (!_processManager.IsRunning)
            throw new InvalidOperationException("RailGPT 后端未运行");

        using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        var resp = await _httpClient.DeleteAsync(
            $"{BaseUrl}/api/settings/api?slot={slot}", cts.Token);

        if (!resp.IsSuccessStatusCode)
        {
            var error = await resp.Content.ReadFromJsonAsync<JsonElement>();
            var msg = error.TryGetProperty("error", out var e) ? e.GetString() : "删除失败";
            throw new InvalidOperationException(msg ?? "删除 API Key 失败");
        }

        return await resp.Content.ReadFromJsonAsync<AISettingsPayload>(
            new JsonSerializerOptions { PropertyNameCaseInsensitive = true });
    }
}
