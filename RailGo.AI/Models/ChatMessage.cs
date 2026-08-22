using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace RailGo.AI.Models;

/// <summary>
/// UI-facing message displayed in the chat view. Implements INPC for x:Bind reactivity.
/// Attachments collection changes automatically trigger HasAttachments re-evaluation.
/// </summary>
public class ChatMessage : INotifyPropertyChanged
{
    private string _content = string.Empty;
    private string? _thinking;
    private bool _isStreaming;
    private bool _isThinkingExpanded;
    private ObservableCollection<ChatAttachment> _attachments;
    private ChatMessageRole _role;

    public string Id { get; set; } = Guid.NewGuid().ToString("N")[..8];
    public DateTime Timestamp { get; set; } = DateTime.Now;

    public ChatMessage()
    {
        _attachments = new ObservableCollection<ChatAttachment>();
        _attachments.CollectionChanged += (_, _) =>
        {
            OnPropertyChanged(nameof(HasAttachments));
        };
    }

    public ChatMessageRole Role
    {
        get => _role;
        set
        {
            _role = value;
            OnPropertyChanged();
            OnPropertyChanged(nameof(IsAIMessage));
            OnPropertyChanged(nameof(IsUserMessage));
            OnPropertyChanged(nameof(IsNotStreaming));
        }
    }

    public string Content
    {
        get => _content;
        set { _content = value; OnPropertyChanged(); OnPropertyChanged(nameof(HasContent)); }
    }

    public ObservableCollection<ChatAttachment> Attachments
    {
        get => _attachments;
        set
        {
            if (_attachments != null)
                _attachments.CollectionChanged -= OnAttachmentsCollectionChanged;
            _attachments = value;
            if (_attachments != null)
                _attachments.CollectionChanged += OnAttachmentsCollectionChanged;
            OnPropertyChanged();
            OnPropertyChanged(nameof(HasAttachments));
        }
    }

    public string? Thinking
    {
        get => _thinking;
        set { _thinking = value; OnPropertyChanged(); OnPropertyChanged(nameof(HasThinking)); }
    }

    public bool IsStreaming
    {
        get => _isStreaming;
        set { _isStreaming = value; OnPropertyChanged(); OnPropertyChanged(nameof(IsNotStreaming)); }
    }

    public bool IsThinkingExpanded
    {
        get => _isThinkingExpanded;
        set { _isThinkingExpanded = value; OnPropertyChanged(); }
    }

    // ===== Computed bindable properties for x:Bind =====

    public bool IsAIMessage => Role == ChatMessageRole.AI;
    public bool IsUserMessage => Role == ChatMessageRole.User;
    public bool HasThinking => !string.IsNullOrWhiteSpace(Thinking);
    public bool HasContent => !string.IsNullOrWhiteSpace(Content);
    public bool HasAttachments => Attachments.Count > 0;
    public bool IsNotStreaming => Role == ChatMessageRole.AI && !IsStreaming;

    public string TimestampText => Timestamp.ToString("HH:mm");

    public event PropertyChangedEventHandler? PropertyChanged;

    protected void OnPropertyChanged([CallerMemberName] string? name = null)
    {
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }

    private void OnAttachmentsCollectionChanged(object? sender, System.Collections.Specialized.NotifyCollectionChangedEventArgs e)
    {
        OnPropertyChanged(nameof(HasAttachments));
    }
}

public enum ChatMessageRole
{
    User,
    AI,
    System
}
