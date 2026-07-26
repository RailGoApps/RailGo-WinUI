using System.Net.Http.Json;
using System.Runtime.CompilerServices;
using System.Text;
using Microsoft.Extensions.Logging;
using Newtonsoft.Json;
using RailGo.AI.Models;

namespace RailGo.AI.Services;

public class AIChatClient : IAIChatClient
{
    private readonly HttpClient _httpClient;
    private readonly IPythonProcessManager _processManager;
    private readonly ILogger<AIChatClient>? _logger;

    public string BaseUrl => _processManager.BaseUrl;

    public AIChatClient(HttpClient httpClient, IPythonProcessManager processManager, ILogger<AIChatClient>? logger = null)
    {
        // Create a dedicated HttpClient for AI streaming requests
        // (the shared singleton may have already sent requests, preventing property changes)
        _httpClient = new HttpClient { Timeout = TimeSpan.FromMinutes(10) };
        _processManager = processManager;
        _logger = logger;
    }

    public async IAsyncEnumerable<ChatEvent> SendMessageAsync(string text, [EnumeratorCancellation] CancellationToken ct = default)
    {
        var payload = JsonConvert.SerializeObject(new { text });
        var content = new StringContent(payload, Encoding.UTF8, "application/json");

        var request = new HttpRequestMessage(HttpMethod.Post, $"{BaseUrl}/api/chat")
        {
            Content = content,
        };
        request.Headers.Accept.Add(new System.Net.Http.Headers.MediaTypeWithQualityHeaderValue("text/event-stream"));

        // Send request — catch error before any yield return
        HttpResponseMessage? response = null;
        Exception? sendError = null;
        try
        {
            response = await _httpClient.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, ct);
            response.EnsureSuccessStatusCode();
        }
        catch (Exception ex)
        {
            sendError = ex;
        }

        if (sendError != null || response == null)
        {
            _logger?.LogError(sendError, "Failed to send chat message.");
            yield return new ChatEvent { Type = "error", Text = sendError?.Message ?? "Unknown error" };
            yield break;
        }

        // Read SSE stream — all yield points are OUTSIDE try-catch blocks
        using var stream = await response.Content.ReadAsStreamAsync(ct);
        using var reader = new StreamReader(stream, Encoding.UTF8);

        while (!reader.EndOfStream && !ct.IsCancellationRequested)
        {
            string? line;
            try
            {
                line = await reader.ReadLineAsync(ct);
            }
            catch
            {
                yield break;
            }
            if (line == null) break;

            if (line.StartsWith("data: "))
            {
                var json = line.Substring(6);
                ChatEvent? ev = null;
                try
                {
                    ev = JsonConvert.DeserializeObject<ChatEvent>(json);
                }
                catch (JsonException ex)
                {
                    _logger?.LogDebug(ex, "Failed to parse SSE event: {Json}", json);
                }

                if (ev != null)
                {
                    yield return ev;
                    if (ev.Type is "done" or "error")
                        yield break;
                }
            }
        }
    }

    public async Task<List<ChatConversation>> GetConversationsAsync(CancellationToken ct = default)
    {
        try
        {
            var response = await _httpClient.GetFromJsonAsync<ConversationListResponse>(
                $"{BaseUrl}/api/conversations", ct);
            return response?.Conversations ?? new List<ChatConversation>();
        }
        catch (Exception ex)
        {
            _logger?.LogError(ex, "Failed to get conversations.");
            return new List<ChatConversation>();
        }
    }

    public async Task<ChatConversationDetail?> CreateConversationAsync(CancellationToken ct = default)
    {
        try
        {
            var response = await _httpClient.PostAsync($"{BaseUrl}/api/conversations", null, ct);
            response.EnsureSuccessStatusCode();
            return await response.Content.ReadFromJsonAsync<ChatConversationDetail>(cancellationToken: ct);
        }
        catch (Exception ex)
        {
            _logger?.LogError(ex, "Failed to create conversation.");
            return null;
        }
    }

    public async Task<ChatConversationDetail?> LoadConversationAsync(int cid, CancellationToken ct = default)
    {
        try
        {
            var response = await _httpClient.PostAsync($"{BaseUrl}/api/conversations/{cid}/load", null, ct);
            // Fallback: some versions use GET
            if (!response.IsSuccessStatusCode)
                response = await _httpClient.GetAsync($"{BaseUrl}/api/conversations/{cid}", ct);

            response.EnsureSuccessStatusCode();
            return await response.Content.ReadFromJsonAsync<ChatConversationDetail>(cancellationToken: ct);
        }
        catch (Exception ex)
        {
            _logger?.LogError(ex, "Failed to load conversation {Cid}.", cid);
            return null;
        }
    }

    public async Task<bool> DeleteConversationAsync(int cid, CancellationToken ct = default)
    {
        try
        {
            var response = await _httpClient.DeleteAsync($"{BaseUrl}/api/conversations/{cid}", ct);
            return response.IsSuccessStatusCode;
        }
        catch (Exception ex)
        {
            _logger?.LogError(ex, "Failed to delete conversation {Cid}.", cid);
            return false;
        }
    }

    public async Task<bool> RenameConversationAsync(int cid, string title, CancellationToken ct = default)
    {
        try
        {
            var payload = JsonConvert.SerializeObject(new { title });
            var content = new StringContent(payload, Encoding.UTF8, "application/json");
            var response = await _httpClient.PutAsync($"{BaseUrl}/api/conversations/{cid}/rename", content, ct);
            return response.IsSuccessStatusCode;
        }
        catch (Exception ex)
        {
            _logger?.LogError(ex, "Failed to rename conversation {Cid}.", cid);
            return false;
        }
    }

    public async Task<List<SuggestionItem>> GetSuggestionsAsync(CancellationToken ct = default)
    {
        try
        {
            return await _httpClient.GetFromJsonAsync<List<SuggestionItem>>(
                $"{BaseUrl}/api/suggestions", ct) ?? new List<SuggestionItem>();
        }
        catch (Exception ex)
        {
            _logger?.LogError(ex, "Failed to get suggestions.");
            return new List<SuggestionItem>();
        }
    }

    public async Task<ServerStatus?> GetStatusAsync(CancellationToken ct = default)
    {
        try
        {
            return await _httpClient.GetFromJsonAsync<ServerStatus>(
                $"{BaseUrl}/api/status", ct);
        }
        catch (Exception ex)
        {
            _logger?.LogError(ex, "Failed to get server status.");
            return null;
        }
    }
}
