import unittest

try:
    from PyQt5.QtWidgets import QApplication

    from ui.chat_area import ChatArea
    from ui.dropdown import RailGPTDropdown
    from ui.font_selector import FontSelector

    _APP = QApplication.instance() or QApplication([])
    PYQT_AVAILABLE = True
except ImportError:
    PYQT_AVAILABLE = False


@unittest.skipUnless(PYQT_AVAILABLE, "Legacy PyQt UI controls are optional and not part of the current runtime stack")
class UIControlsTest(unittest.TestCase):
    def test_font_selector_button_click_updates_current_size(self):
        calls = []
        selector = FontSelector(callback=calls.append)

        selector.buttons[2].click()

        self.assertEqual(selector.current_index, 2)
        self.assertEqual(selector.current_size(), 18)
        self.assertEqual(calls[-1], 18)

    def test_mode_selector_button_click_emits_selected_value(self):
        selected = []
        dropdown = RailGPTDropdown([("FAST-GO", "fast-go"), ("FAST-PLUS", "fast-plus"), ("DEEP", "deep")])
        dropdown.selected.connect(selected.append)

        dropdown._buttons[1].click()

        self.assertEqual(dropdown.current, "fast-plus")
        self.assertEqual(selected[-1], "fast-plus")

    def test_chat_area_finalize_current_ai_populates_empty_placeholder(self):
        chat = ChatArea()
        chat.add_ai("")

        chat.finalize_current_ai("final answer from final event")

        self.assertIsNotNone(chat.current_ai_bubble)
        self.assertEqual(chat.current_ai_bubble.text, "final answer from final event")

    def test_chat_area_finalize_current_ai_replaces_streamed_text(self):
        chat = ChatArea()
        chat.add_ai("")
        chat.append_token("partial")

        chat.finalize_current_ai("final answer")

        self.assertEqual(chat.current_ai_bubble.text, "partial")


if __name__ == "__main__":
    unittest.main()
