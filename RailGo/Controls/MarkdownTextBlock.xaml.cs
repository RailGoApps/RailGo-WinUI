using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace RailGo.Controls;

/// <summary>
/// Reusable Markdown-rendering control for WinUI 3.
/// Bind Text to any markdown string; renders as formatted RichTextBlock content.
/// </summary>
public sealed partial class MarkdownTextBlock : UserControl
{
    // ==================== Dependency Properties ====================

    public static readonly DependencyProperty TextProperty =
        DependencyProperty.Register(
            nameof(Text),
            typeof(string),
            typeof(MarkdownTextBlock),
            new PropertyMetadata(string.Empty, OnTextChanged));

    public static readonly DependencyProperty IsMarkdownProperty =
        DependencyProperty.Register(
            nameof(IsMarkdown),
            typeof(bool),
            typeof(MarkdownTextBlock),
            new PropertyMetadata(true, OnTextChanged));

    /// <summary>The markdown text to render.</summary>
    public string Text
    {
        get => (string)GetValue(TextProperty);
        set => SetValue(TextProperty, value);
    }

    /// <summary>When false, renders as plain text (no markdown processing).</summary>
    public bool IsMarkdown
    {
        get => (bool)GetValue(IsMarkdownProperty);
        set => SetValue(IsMarkdownProperty, value);
    }

    // ==================== Constructor ====================

    public MarkdownTextBlock()
    {
        InitializeComponent();
    }

    // ==================== Rendering ====================

    private static void OnTextChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
    {
        if (d is MarkdownTextBlock control)
        {
            // Always re-render from the current Text property value,
            // since e.NewValue may be a bool (from IsMarkdown changes) not a string.
            control.RenderContent(control.Text);
        }
    }

    private void RenderContent(string text)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            ContentBlock.Blocks.Clear();
            return;
        }

        if (IsMarkdown)
        {
            MarkdownRenderer.Render(ContentBlock, text);
        }
        else
        {
            ContentBlock.Blocks.Clear();
            var p = new Microsoft.UI.Xaml.Documents.Paragraph();
            p.Inlines.Add(new Microsoft.UI.Xaml.Documents.Run { Text = text });
            ContentBlock.Blocks.Add(p);
        }
    }
}
