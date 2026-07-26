using RailGo.AI.Models;

namespace RailGo.AI.Services;

/// <summary>
/// Client for the RailGPT Flask backend HTTP/SSE API.
/// </summary>
public interface IAIChatClient
{
    /// <summary>Send a message and receive SSE events as a stream.</summary>
    IAsyncEnumerable<ChatEvent> SendMessageAsync(string text, CancellationToken ct = default);

    /// <summary>Get all conversations.</summary>
    Task<List<ChatConversation>> GetConversationsAsync(CancellationToken ct = default);

    /// <summary>Create a new conversation.</summary>
    Task<ChatConversationDetail?> CreateConversationAsync(CancellationToken ct = default);

    /// <summary>Load a conversation by ID.</summary>
    Task<ChatConversationDetail?> LoadConversationAsync(int cid, CancellationToken ct = default);

    /// <summary>Delete a conversation.</summary>
    Task<bool> DeleteConversationAsync(int cid, CancellationToken ct = default);

    /// <summary>Rename a conversation.</summary>
    Task<bool> RenameConversationAsync(int cid, string title, CancellationToken ct = default);

    /// <summary>Get suggestion cards.</summary>
    Task<List<SuggestionItem>> GetSuggestionsAsync(CancellationToken ct = default);

    /// <summary>Get server status.</summary>
    Task<ServerStatus?> GetStatusAsync(CancellationToken ct = default);

    /// <summary>Base URL of the backend.</summary>
    string BaseUrl { get; }
}
