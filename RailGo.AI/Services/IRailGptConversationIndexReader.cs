using RailGo.AI.Models;

namespace RailGo.AI.Services;

public interface IRailGptConversationIndexReader : IDisposable
{
    IReadOnlyList<ChatConversation> LastKnownConversations { get; }
    event EventHandler<IReadOnlyList<ChatConversation>>? ConversationsChanged;
    void Start();
    Task<IReadOnlyList<ChatConversation>> RefreshAsync(CancellationToken cancellationToken = default);
}
