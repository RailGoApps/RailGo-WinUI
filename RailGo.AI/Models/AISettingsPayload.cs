using System.Text.Json.Serialization;

namespace RailGo.AI.Models;

/// <summary>
/// Mirrors the Python backend's /api/settings response (get_frontend_payload).
/// </summary>
public class AISettingsPayload
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; }

    [JsonPropertyName("provider")]
    public string Provider { get; set; } = "deepseek";

    [JsonPropertyName("providers")]
    public List<ProviderOption> Providers { get; set; } = new();

    [JsonPropertyName("has_api_key")]
    public bool HasApiKey { get; set; }

    [JsonPropertyName("has_primary_api_key")]
    public bool HasPrimaryApiKey { get; set; }

    [JsonPropertyName("has_thinking_api_key")]
    public bool HasThinkingApiKey { get; set; }

    [JsonPropertyName("masked_api_key")]
    public string MaskedApiKey { get; set; } = "";

    [JsonPropertyName("masked_primary_api_key")]
    public string MaskedPrimaryApiKey { get; set; } = "";

    [JsonPropertyName("masked_thinking_api_key")]
    public string MaskedThinkingApiKey { get; set; } = "";

    [JsonPropertyName("thinking_uses_primary_fallback")]
    public bool ThinkingUsesPrimaryFallback { get; set; }

    [JsonPropertyName("config_path")]
    public string ConfigPath { get; set; } = "";

    [JsonPropertyName("updated_at")]
    public string UpdatedAt { get; set; } = "";

    [JsonPropertyName("account")]
    public AccountInfo? Account { get; set; }

    [JsonPropertyName("busy")]
    public bool Busy { get; set; }
}

public class ProviderOption
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("label")]
    public string Label { get; set; } = "";

    [JsonPropertyName("description")]
    public string Description { get; set; } = "";

    [JsonPropertyName("supports_custom_base_url")]
    public bool SupportsCustomBaseUrl { get; set; }
}

public class AccountInfo
{
    [JsonPropertyName("status")]
    public string Status { get; set; } = "";

    [JsonPropertyName("title")]
    public string Title { get; set; } = "";

    [JsonPropertyName("description")]
    public string Description { get; set; } = "";
}
