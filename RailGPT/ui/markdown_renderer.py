import re

import markdown


INLINE_ORDERED_LIST_RE = re.compile(r"([:：])\s+(\d+\.\s+)")
INLINE_BULLET_LIST_RE = re.compile(r"([:：])\s+([*-]\s+)")
ORDERED_ITEM_BREAK_RE = re.compile(r"(?<!\n)[ \t]+(?=(?:\d+\.\s+[A-Za-z0-9\u4e00-\u9fff]))")
BULLET_ITEM_BREAK_RE = re.compile(r"(?<!\n)[ \t]+(?=(?:[*-]\s+[A-Za-z0-9`\u4e00-\u9fff]))")


class MarkdownRenderer:
    def __init__(self, font_size: int = 17):
        self.font_size = font_size

    def set_font_size(self, size: int):
        self.font_size = size

    def _normalize_markdown(self, text: str) -> str:
        normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            return ""

        normalized = INLINE_ORDERED_LIST_RE.sub(r"\1\n\n\2", normalized)
        normalized = INLINE_BULLET_LIST_RE.sub(r"\1\n\n\2", normalized)
        normalized = ORDERED_ITEM_BREAK_RE.sub("\n", normalized)
        normalized = BULLET_ITEM_BREAK_RE.sub("\n", normalized)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)

        cleaned_lines = [line.rstrip() for line in normalized.split("\n")]
        return "\n".join(cleaned_lines).strip()

    def render(self, text: str, role: str = "ai") -> str:
        normalized = self._normalize_markdown(text)
        html = markdown.markdown(
            normalized,
            extensions=["extra", "sane_lists", "nl2br"],
            output_format="html5",
        )

        if role == "user":
            text_color = "#ffffff"
            muted_text = "rgba(255,255,255,0.86)"
            code_bg = "rgba(255,255,255,0.16)"
            pre_bg = "rgba(255,255,255,0.12)"
            table_border = "rgba(255,255,255,0.18)"
            quote_border = "rgba(255,255,255,0.55)"
            link_color = "#ffffff"
        else:
            text_color = "#111827"
            muted_text = "#4b5563"
            code_bg = "#e8eefb"
            pre_bg = "#f7f8fb"
            table_border = "rgba(17,24,39,0.14)"
            quote_border = "#2563eb"
            link_color = "#1d4ed8"

        style = f"""
        <style>
        html, body {{
            background: transparent;
            margin: 0;
            padding: 0;
        }}

        body, .message-root, .message-root p, .message-root li, .message-root div,
        .message-root span, .message-root td, .message-root th, .message-root blockquote {{
            color: {text_color};
            font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
            font-size: {self.font_size}px;
            line-height: 1.68;
        }}

        p {{
            margin: 0 0 10px 0;
        }}

        h1, h2, h3, h4 {{
            margin: 12px 0 8px 0;
            color: {text_color};
            line-height: 1.35;
        }}

        h1 {{
            font-size: {self.font_size + 5}px;
        }}

        h2 {{
            font-size: {self.font_size + 3}px;
        }}

        h3, h4 {{
            font-size: {self.font_size + 1}px;
        }}

        ul, ol {{
            margin: 6px 0 10px 18px;
            padding: 0 0 0 10px;
        }}

        li {{
            margin: 0 0 6px 0;
        }}

        strong {{
            color: {text_color};
            font-weight: 650;
        }}

        em {{
            color: {muted_text};
        }}

        code {{
            background: {code_bg};
            padding: 2px 6px;
            border-radius: 6px;
            font-family: Consolas, "Cascadia Mono", monospace;
        }}

        pre {{
            background: {pre_bg};
            padding: 12px 14px;
            border-radius: 12px;
            margin: 10px 0 12px 0;
            white-space: pre-wrap;
        }}

        pre code {{
            background: transparent;
            padding: 0;
            border-radius: 0;
        }}

        blockquote {{
            border-left: 4px solid {quote_border};
            margin: 10px 0;
            padding: 2px 0 2px 12px;
            color: {muted_text};
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 10px 0 12px 0;
            table-layout: fixed;
        }}

        th, td {{
            border: 1px solid {table_border};
            padding: 8px 10px;
            text-align: left;
            vertical-align: top;
            word-wrap: break-word;
        }}

        th {{
            background: rgba(37,99,235,0.08);
        }}

        hr {{
            border: none;
            border-top: 1px solid {table_border};
            margin: 12px 0;
        }}

        a {{
            color: {link_color};
            text-decoration: none;
        }}
        </style>
        """

        return (
            "<html><head>"
            + style
            + "</head><body><div class=\"message-root\">"
            + html
            + "</div></body></html>"
        )
