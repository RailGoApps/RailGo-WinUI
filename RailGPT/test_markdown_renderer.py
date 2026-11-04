import unittest

from ui.markdown_renderer import MarkdownRenderer


class MarkdownRendererTest(unittest.TestCase):
    def test_inline_ranked_items_are_split_into_markdown_list(self):
        renderer = MarkdownRenderer(font_size=15)

        html = renderer.render("By speed: 1. G84 2. G94 3. G66")

        self.assertIn("<ol>", html)
        self.assertIn("<li>G84</li>", html)
        self.assertIn("<li>G94</li>", html)
        self.assertIn("<li>G66</li>", html)

    def test_inline_note_bullets_are_split_after_colon(self):
        renderer = MarkdownRenderer(font_size=15)

        normalized = renderer._normalize_markdown("Notes: * normal service * all data grounded")

        self.assertIn("Notes:\n\n*", normalized)
        self.assertIn("\n* all data grounded", normalized)

    def test_single_newlines_are_preserved_as_html_breaks(self):
        renderer = MarkdownRenderer(font_size=15)

        html = renderer.render("Line one\nLine two")

        self.assertIn("Line one<br", html)
        self.assertIn("Line two", html)


if __name__ == "__main__":
    unittest.main()
