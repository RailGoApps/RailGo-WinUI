namespace RailGo.AI.Models;

/// <summary>
/// Metadata for a single conversation returned by RailGPT.
/// </summary>
public class ChatConversation
{
    public int Id { get; set; }
    public string Title { get; set; } = string.Empty;
    public DateTime CreatedAt { get; set; }
    public DateTime UpdatedAt { get; set; }
    public int MessageCount { get; set; }

    /// <summary>Formatted updated-at string for display (relative or absolute).</summary>
    public string UpdatedAtText =>
        UpdatedAt.Date == DateTime.Now.Date
            ? UpdatedAt.ToString("HH:mm")
            : UpdatedAt.ToString("MM-dd HH:mm");
}

/// <summary>
/// Full conversation detail including messages.
/// </summary>
public class ChatConversationDetail
{
    public int Id { get; set; }
    public string Title { get; set; } = string.Empty;
    public List<ChatConversationMessage> Messages { get; set; } = new();
}

public class ChatConversationMessage
{
    public string Role { get; set; } = string.Empty;  // "user" | "ai" | "system"
    public string Content { get; set; } = string.Empty;
    public List<ChatAttachment>? Attachments { get; set; }
    public DateTime Timestamp { get; set; }
}

public class ConversationListResponse
{
    public List<ChatConversation> Conversations { get; set; } = new();
}

public class SuggestionItem
{
    public string Label { get; set; } = string.Empty;
    public string Text { get; set; } = string.Empty;
}

public class ServerStatus
{
    public bool Busy { get; set; }
    public int? CurrentId { get; set; }
    public bool HasApiKey { get; set; }
    public bool HasThinkingApiKey { get; set; }
}
