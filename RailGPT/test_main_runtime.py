import socket
import unittest
from unittest import mock

from flask import Flask

import app_runtime
import main
import web_app
from window_api import WindowAPI


class MainRuntimeTest(unittest.TestCase):
    def test_release_metadata_uses_current_version_and_default_port(self):
        self.assertEqual(app_runtime.APP_VERSION, "3.0.0")
        self.assertEqual(app_runtime.DEFAULT_PORT, 5033)
        self.assertEqual(app_runtime.WINDOW_TITLE, "RailGPT v3.0.0")

    def test_start_flask_falls_back_when_preferred_port_is_busy(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((app_runtime.DEFAULT_HOST, 0))
            busy_port = probe.getsockname()[1]

        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind((app_runtime.DEFAULT_HOST, busy_port))
        blocker.listen(1)
        self.addCleanup(blocker.close)

        flask_app = Flask(__name__)

        @flask_app.route("/")
        def _index():
            return "ok"

        server, actual_port = main._start_flask(flask_app, preferred_port=busy_port)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)

        self.assertNotEqual(actual_port, busy_port)
        self.assertTrue(main._wait_for_server(main._format_url(app_runtime.DEFAULT_HOST, actual_port), timeout=5.0))

    @mock.patch("main.webbrowser.open")
    def test_browser_fallback_uses_runtime_url(self, open_mock):
        main._launch_browser_fallback("http://127.0.0.1:5033")
        open_mock.assert_called_once_with("http://127.0.0.1:5033", new=1, autoraise=True)


class WindowApiExportTest(unittest.TestCase):
    def setUp(self):
        self.old_export = web_app.export_conversation_markdown
        self.old_suggest = web_app.suggest_export_filename
        self.addCleanup(self._restore_web_helpers)

    def _restore_web_helpers(self):
        web_app.export_conversation_markdown = self.old_export
        web_app.suggest_export_filename = self.old_suggest

    def test_desktop_export_uses_native_save_dialog(self):
        dialog_calls = []

        class FakeWindow:
            def create_file_dialog(self, *args, **kwargs):
                dialog_calls.append((args, kwargs))
                return ("C:\\temp\\chat.md",)

        web_app.suggest_export_filename = lambda cid: f"chat_{cid}.md"
        web_app.export_conversation_markdown = lambda cid, save_path=None: save_path

        api = WindowAPI()
        api._window = FakeWindow()

        result = api.exportConversation(7)

        self.assertTrue(result["ok"], result.get("error", ""))
        self.assertEqual(result["path"], "C:\\temp\\chat.md")
        self.assertEqual(dialog_calls[0][1]["save_filename"], "chat_7.md")

    def test_desktop_export_cancellation_is_non_fatal(self):
        class FakeWindow:
            def create_file_dialog(self, *args, **kwargs):
                return None

        api = WindowAPI()
        api._window = FakeWindow()

        result = api.exportConversation(9)

        self.assertFalse(result["ok"])
        self.assertTrue(result.get("cancelled"), result.get("error", ""))


if __name__ == "__main__":
    unittest.main()
