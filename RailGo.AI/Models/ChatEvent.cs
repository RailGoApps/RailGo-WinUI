namespace RailGo.AI.Models;

/// <summary>
/// Represents a single SSE event from the RailGPT /api/chat stream.
/// </summary>
public class ChatEvent
{
    /// <summary>token | thinking | pending | attachment | done | error | heartbeat | psw</summary>
    public string Type { get; set; } = string.Empty;

    /// <summary>Text payload (token text, thinking text, error message, etc.)</summary>
    public string? Text { get; set; }

    /// <summary>Attachment data when type == "attachment"</summary>
    public ChatAttachment? Attachment { get; set; }

    /// <summary>Conversation ID when type == "done"</summary>
    public int? Cid { get; set; }

    /// <summary>Conversation title when type == "done"</summary>
    public string? Title { get; set; }
}

/// <summary>
/// Attachment embedded in an AI response (geo route, coach image, etc.)
/// </summary>
public class ChatAttachment
{
    public string? Type { get; set; }
    public string? Content { get; set; }
    public string? MimeType { get; set; }
    public Dictionary<string, object>? Meta { get; set; }

    // ===== Computed bindable properties for x:Bind =====

    public bool IsImage => string.Equals(Type, "image", StringComparison.OrdinalIgnoreCase);
    public bool IsNotImage => !IsImage;
    public string Label => Type?.ToLowerInvariant() switch
    {
        "image" => "图片",
        "code" => "代码",
        "route_map" => "线路地图",
        "table" => "数据表格",
        "chart" => "图表",
        "file" => "文件",
        _ => Type ?? "附件",
    };
}
