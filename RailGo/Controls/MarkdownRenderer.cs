using Markdig;
using Markdig.Extensions.Tables;
using Markdig.Syntax;
using Markdig.Syntax.Inlines;
using Microsoft.UI.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Documents;
using Microsoft.UI.Xaml.Media;
using Windows.UI;
using Windows.UI.Text;
using MdBlock = Markdig.Syntax.Block;
using MdInline = Markdig.Syntax.Inlines.Inline;
using WBlock = Microsoft.UI.Xaml.Documents.Block;
using WInline = Microsoft.UI.Xaml.Documents.Inline;

namespace RailGo.Controls;

/// <summary>
/// Converts Markdown text (via Markdig) into WinUI 3 RichTextBlock Block elements.
/// Used by MarkdownTextBlock control and can be reused elsewhere.
/// </summary>
public static class MarkdownRenderer
{
    private static readonly MarkdownPipeline Pipeline = new MarkdownPipelineBuilder()
        .UseAdvancedExtensions()
        .Build();

    // ==================== Public API ====================

    /// <summary>Render markdown text into a RichTextBlock.</summary>
    public static void Render(RichTextBlock target, string markdown)
    {
        target.Blocks.Clear();
        if (string.IsNullOrWhiteSpace(markdown))
            return;

        try
        {
            var document = Markdig.Markdown.Parse(markdown, Pipeline);
            foreach (var block in document)
            {
                var rendered = RenderBlock(block, target);
                if (rendered != null)
                    target.Blocks.Add(rendered);
            }
        }
        catch
        {
            // Fallback to plain text on parse error (e.g., streaming partial markdown)
            target.Blocks.Add(PlainParagraph(markdown));
        }
    }

    /// <summary>Render inline-only markdown into Inline elements for a Paragraph.</summary>
    public static IEnumerable<WInline> RenderInlines(string markdown, Paragraph parent)
    {
        var document = Markdig.Markdown.Parse(markdown, Pipeline);
        foreach (var block in document)
        {
            if (block is ParagraphBlock para)
            {
                foreach (var inline in para.Inline!)
                    yield return RenderInline(inline, parent);
            }
        }
    }

    // ==================== Block Renderers ====================

    private static WBlock? RenderBlock(MdBlock block, RichTextBlock rtb)
    {
        return block switch
        {
            ParagraphBlock pb => RenderParagraph(pb),
            HeadingBlock hb => RenderHeading(hb),
            FencedCodeBlock fcb => new Paragraph { Inlines = { RenderFencedCode(fcb) } },
            CodeBlock cb => new Paragraph { Inlines = { RenderCodeBlock(cb) } },
            ListBlock lb => RenderList(lb, rtb),
            QuoteBlock qb => RenderQuote(qb),
            ThematicBreakBlock => RenderThematicBreak(),
            Table tb => new Paragraph { Inlines = { RenderTable(tb) } },
            _ => null,
        };
    }

    private static Paragraph RenderParagraph(ParagraphBlock pb)
    {
        var p = new Paragraph { LineHeight = 22 };
        if (pb.Inline != null)
        {
            foreach (var inline in pb.Inline)
                p.Inlines.Add(RenderInline(inline, p));
        }
        return p;
    }

    private static Paragraph RenderHeading(HeadingBlock hb)
    {
        var p = new Paragraph();
        double fontSize = hb.Level switch
        {
            1 => 24,
            2 => 20,
            3 => 17,
            4 => 15,
            _ => 14,
        };
        var weight = hb.Level <= 2 ? FontWeights.SemiBold : FontWeights.Normal;
        p.Margin = hb.Level switch
        {
            1 => new Thickness(0, 16, 0, 8),
            2 => new Thickness(0, 12, 0, 6),
            _ => new Thickness(0, 8, 0, 4),
        };

        if (hb.Inline != null)
        {
            // Wrap all inlines with heading styling
            var contentRun = new Run();
            foreach (var inline in hb.Inline)
            {
                // Flatten heading content into styled text
                FlattenInlineToText(inline, contentRun);
            }
            contentRun.FontSize = fontSize;
            contentRun.FontWeight = weight;
            p.Inlines.Add(contentRun);
        }
        return p;
    }

    private static void FlattenInlineToText(MdInline inline, Run target)
    {
        if (inline is LiteralInline lit)
        {
            if (!string.IsNullOrEmpty(target.Text))
                target.Text += lit.Content.ToString();
            else
                target.Text = lit.Content.ToString();
        }
        else if (inline is EmphasisInline em && em.FirstChild is MdInline child)
        {
            FlattenInlineToText(child, target);
        }
    }

    private static InlineUIContainer RenderFencedCode(FencedCodeBlock fcb)
    {
        return BuildCodeContainer(
            fcb.Lines.ToString(),
            fcb.Info?.Replace("\\", "") ?? "");
    }

    private static InlineUIContainer RenderCodeBlock(CodeBlock cb)
    {
        return BuildCodeContainer(cb.Lines.ToString(), "");
    }

    private static InlineUIContainer BuildCodeContainer(string code, string language)
    {
        var headerText = string.IsNullOrWhiteSpace(language) ? "代码" : language;

        var stack = new StackPanel();

        // Language label bar
        var headerBorder = new Border
        {
            Background = new SolidColorBrush(Color.FromArgb(0x40, 0x00, 0x00, 0x00)),
            Padding = new Thickness(12, 4, 12, 4),
            CornerRadius = new CornerRadius(8, 8, 0, 0),
        };
        var headerTb = new TextBlock
        {
            Text = headerText,
            FontSize = 11,
            Foreground = new SolidColorBrush(Color.FromArgb(0xFF, 0x88, 0x88, 0x88)),
            FontFamily = new FontFamily("Segoe UI"),
        };
        headerBorder.Child = headerTb;
        stack.Children.Add(headerBorder);

        // Code text
        var codeTb = new TextBlock
        {
            Text = code.TrimEnd(),
            FontFamily = new FontFamily("Consolas"),
            FontSize = 13,
            Padding = new Thickness(12, 8, 12, 8),
            TextWrapping = TextWrapping.Wrap,
            Foreground = new SolidColorBrush(Color.FromArgb(0xFF, 0xDD, 0xDD, 0xDD)),
        };
        stack.Children.Add(codeTb);

        var outerBorder = new Border
        {
            Background = new SolidColorBrush(Color.FromArgb(0xFF, 0x1E, 0x1E, 0x1E)),
            CornerRadius = new CornerRadius(8),
            Child = stack,
            Margin = new Thickness(0, 6, 0, 6),
        };

        return new InlineUIContainer { Child = outerBorder };
    }

    private static Paragraph RenderList(ListBlock lb, RichTextBlock rtb)
    {
        var container = new Paragraph();
        bool isOrdered = lb.IsOrdered;
        int index = 1;

        foreach (var item in lb)
        {
            if (item is ListItemBlock li)
            {
                var prefix = isOrdered ? $"{index}. " : "  •  ";
                index++;

                var prefixRun = new Run { Text = prefix };
                container.Inlines.Add(prefixRun);

                if (li.Count > 0 && li[0] is ParagraphBlock itemPara && itemPara.Inline != null)
                {
                    foreach (var inline in itemPara.Inline)
                        container.Inlines.Add(RenderInline(inline, container));
                }

                container.Inlines.Add(new LineBreak());
            }
        }
        container.Margin = new Thickness(4, 2, 0, 2);
        return container;
    }

    private static Paragraph RenderQuote(QuoteBlock qb)
    {
        // Render inner blocks inline with a left-accent border look
        var p = new Paragraph
        {
            Margin = new Thickness(0, 4, 0, 4),
        };

        // Left accent bar via InlineUIContainer
        var accentBar = new Border
        {
            Width = 3,
            Background = new SolidColorBrush(Color.FromArgb(0xFF, 0x60, 0x60, 0x60)),
            CornerRadius = new CornerRadius(2),
        };

        foreach (var block in qb)
        {
            if (block is ParagraphBlock qPara && qPara.Inline != null)
            {
                foreach (var inline in qPara.Inline)
                    p.Inlines.Add(RenderInline(inline, p));
            }
        }
        return p;
    }

    private static Paragraph RenderThematicBreak()
    {
        var border = new Border
        {
            Height = 1,
            Background = new SolidColorBrush(Color.FromArgb(0x40, 0x80, 0x80, 0x80)),
            Margin = new Thickness(0, 12, 0, 12),
        };
        var p = new Paragraph();
        p.Inlines.Add(new InlineUIContainer { Child = border });
        return p;
    }

    private static InlineUIContainer RenderTable(Table table)
    {
        if (!table.Any()) return new InlineUIContainer { Child = new TextBlock { Text = "" } };

        // Count max columns across all rows
        int colCount = 0;
        foreach (TableRow row in table)
            colCount = Math.Max(colCount, row.Count);

        // Count rows (including header)
        int rowCount = table.Count();
        bool hasHeader = rowCount > 0;

        var grid = new Grid
        {
            Margin = new Thickness(0, 6, 0, 6),
            BorderThickness = new Thickness(1),
            BorderBrush = new SolidColorBrush(Color.FromArgb(0x30, 0x80, 0x80, 0x80)),
            CornerRadius = new CornerRadius(6),
        };

        for (int c = 0; c < colCount; c++)
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto, MinWidth = 80 });

        int r = 0;
        foreach (TableRow tableRow in table)
        {
            grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

            bool isHeader = hasHeader && r == 0;
            int c = 0;
            foreach (TableCell cell in tableRow)
            {
                if (c >= colCount) break;

                string cellText = ExtractPlainText(cell);
                var cellTb = new TextBlock
                {
                    Text = cellText,
                    FontSize = 13,
                    FontWeight = isHeader ? FontWeights.SemiBold : FontWeights.Normal,
                    Padding = new Thickness(10, 6, 10, 6),
                    TextWrapping = TextWrapping.Wrap,
                };

                var cellBorder = new Border
                {
                    Background = isHeader
                        ? new SolidColorBrush(global::Windows.UI.Color.FromArgb(0x15, 0x00, 0x00, 0x00))
                        : new SolidColorBrush(Microsoft.UI.Colors.Transparent),
                    Child = cellTb,
                };

                Grid.SetRow(cellBorder, r);
                Grid.SetColumn(cellBorder, c);
                grid.Children.Add(cellBorder);
                c++;
            }
            r++;
        }

        var outerBorder = new Border
        {
            Child = new ScrollViewer
            {
                HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
                HorizontalScrollMode = ScrollMode.Auto,
                Content = grid,
            },
            Margin = new Thickness(0, 6, 0, 6),
        };

        return new InlineUIContainer { Child = outerBorder };
    }

    private static string ExtractPlainText(TableCell cell)
    {
        if (cell.Count == 0) return "";
        var sb = new System.Text.StringBuilder();
        foreach (var item in cell)
        {
            if (item is ParagraphBlock pb && pb.Inline != null)
            {
                foreach (var inline in pb.Inline)
                    AppendInlineText(inline, sb);
            }
        }
        return sb.ToString().Trim();
    }

    private static void AppendInlineText(MdInline inline, System.Text.StringBuilder sb)
    {
        if (inline is LiteralInline lit)
            sb.Append(lit.Content.ToString());
        else if (inline is CodeInline ci)
            sb.Append(ci.Content);
        else if (inline is EmphasisInline em)
        {
            foreach (var child in em)
                if (child is MdInline childInline)
                    AppendInlineText(childInline, sb);
        }
        else if (inline is LinkInline link)
        {
            foreach (var child in link)
                if (child is MdInline childInline)
                    AppendInlineText(childInline, sb);
        }
        else if (inline is LineBreakInline)
            sb.Append(' ');
    }

    // ==================== Inline Renderers ====================

    private static WInline RenderInline(MdInline inline, Paragraph parent)
    {
        return inline switch
        {
            LiteralInline lit => new Run { Text = lit.Content.ToString() },
            CodeInline code => RenderCodeInline(code),
            EmphasisInline em => RenderEmphasis(em, parent),
            LinkInline link => RenderLink(link),
            LineBreakInline => new LineBreak(),
            HtmlEntityInline he => new Run { Text = he.Transcoded.ToString() },
            HtmlInline => new Run(), // Skip raw HTML
            _ => RenderFallbackInline(inline, parent),
        };
    }

    private static WInline RenderCodeInline(CodeInline code)
    {
        // Subtle monospace + background for inline code
        var border = new Border
        {
            Background = new SolidColorBrush(Color.FromArgb(0x20, 0x00, 0x00, 0x00)),
            CornerRadius = new CornerRadius(3),
            Padding = new Thickness(4, 1, 4, 1),
        };
        var tb = new TextBlock
        {
            Text = code.Content,
            FontFamily = new FontFamily("Consolas"),
            FontSize = 12.5,
        };
        border.Child = tb;
        return new InlineUIContainer { Child = border };
    }

    private static WInline RenderEmphasis(EmphasisInline em, Paragraph parent)
    {
        // em.DelimiterChar: '*' = emphasis (italic), '~' variants = strikethrough
        bool isBold = em.DelimiterCount == 2 && (em.DelimiterChar == '*' || em.DelimiterChar == '_');
        bool isStrike = em.DelimiterChar == '~';

        if (isStrike)
        {
            var strikeSpan = new Span();
            if (em.FirstChild is MdInline child)
            {
                if (child is LiteralInline lit)
                    strikeSpan.Inlines.Add(new Run { Text = lit.Content.ToString() });
                else
                    strikeSpan.Inlines.Add(RenderInline(child, parent));
            }
            // Underline used as strikethrough approximation in WinUI
            foreach (var run in FlattenSpanToRuns(strikeSpan))
                run.TextDecorations = TextDecorations.Strikethrough;
            return strikeSpan.Inlines.Count > 0 ? strikeSpan.Inlines[0] : new Run();
        }

        if (isBold)
        {
            var bold = new Bold();
            foreach (var child in em)
            {
                if (child is MdInline childInline)
                    bold.Inlines.Add(RenderInline(childInline, parent));
            }
            return bold;
        }
        else
        {
            var italic = new Italic();
            foreach (var child in em)
            {
                if (child is MdInline childInline)
                    italic.Inlines.Add(RenderInline(childInline, parent));
            }
            return italic;
        }
    }

    private static List<Run> FlattenSpanToRuns(Span span)
    {
        var runs = new List<Run>();
        foreach (var inline in span.Inlines)
        {
            if (inline is Run r)
                runs.Add(r);
            else if (inline is Span childSpan)
                runs.AddRange(FlattenSpanToRuns(childSpan));
            else if (inline is Bold b)
                foreach (var bi in b.Inlines)
                    if (bi is Run br) runs.Add(br);
            else if (inline is Italic i)
                foreach (var ii in i.Inlines)
                    if (ii is Run ir) runs.Add(ir);
        }
        return runs;
    }

    private static WInline RenderLink(LinkInline link)
    {
        var hyperlink = new Hyperlink();
        try
        {
            hyperlink.NavigateUri = new Uri(link.Url);
        }
        catch { /* ignore invalid URLs */ }

        hyperlink.UnderlineStyle = UnderlineStyle.Single;
        hyperlink.Foreground = new SolidColorBrush(Color.FromArgb(0xFF, 0x4D, 0xA3, 0xF2));

        // Link text
        string linkText;
        if (link.IsImage)
        {
            linkText = $"[图片: {link.Url}]";
        }
        else
        {
            var sb = new System.Text.StringBuilder();
            foreach (var child in link)
            {
                if (child is LiteralInline lit)
                    sb.Append(lit.Content.ToString());
                else if (child is CodeInline ci)
                    sb.Append(ci.Content);
            }
            linkText = sb.Length > 0 ? sb.ToString() : link.Url ?? link.Label ?? "";
        }

        hyperlink.Inlines.Add(new Run { Text = linkText });
        return hyperlink;
    }

    private static WInline RenderFallbackInline(MdInline inline, Paragraph parent)
    {
        // Generic handler for unknown inline types: try to extract text
        if (inline is ContainerInline ci)
        {
            var span = new Span();
            foreach (var child in ci)
            {
                if (child is MdInline childInline)
                    span.Inlines.Add(RenderInline(childInline, parent));
            }
            if (span.Inlines.Count > 0)
                return (WInline)span.Inlines[0];
        }
        return new Run { Text = inline.ToString() ?? "" };
    }

    // ==================== Helpers ====================

    private static Paragraph PlainParagraph(string text)
    {
        var p = new Paragraph();
        p.Inlines.Add(new Run { Text = text });
        return p;
    }
}
