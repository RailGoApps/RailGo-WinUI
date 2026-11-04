import tempfile
import unittest

from app_runtime import resource_path
from app_settings import AppSettingsManager
import web_app


class FakeProtector:
    def encrypt(self, plaintext: str) -> str:
        return f"enc::{plaintext}"

    def decrypt(self, ciphertext: str) -> str:
        if not str(ciphertext).startswith("enc::"):
            raise ValueError("unexpected ciphertext")
        return str(ciphertext)[5:]


class WebAppSettingsApiTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.old_settings = web_app._settings
        self.old_busy = web_app._busy

        self.settings = AppSettingsManager(
            config_path=f"{self.temp_dir.name}\\settings.enc",
            protector=FakeProtector(),
        )
        web_app._settings = self.settings
        web_app._busy = False

        web_app.app.config["TESTING"] = True
        self.client = web_app.app.test_client()
        self.addCleanup(self._restore_globals)

    def _restore_globals(self):
        web_app._settings = self.old_settings
        web_app._busy = self.old_busy

    def test_get_settings_returns_provider_metadata(self):
        response = self.client.get("/api/settings")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["provider"], "deepseek")
        self.assertFalse(payload["has_primary_api_key"])
        self.assertEqual(payload["account"]["status"], "coming_soon")

    def test_put_settings_accepts_primary_and_thinking_keys(self):
        response = self.client.put(
            "/api/settings/api",
            json={
                "provider": "deepseek",
                "primary_api_key": "primary-key",
                "thinking_api_key": "thinking-key",
            },
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["has_primary_api_key"])
        self.assertTrue(payload["has_thinking_api_key"])
        self.assertEqual(
            self.settings.resolve_llm_config(required=True, slot="thinking")["api_key"],
            "thinking-key",
        )

    def test_put_settings_updates_provider_models_and_custom_endpoint(self):
        response = self.client.put(
            "/api/settings/api",
            json={
                "provider": "openai_compatible",
                "primary_api_key": "custom-key",
                "custom_base_url": "https://example.com/v1",
                "models": {"fast-go": "quick", "fast-plus": "balanced", "deep": "reasoning"},
            },
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["provider"], "openai_compatible")
        self.assertEqual(payload["base_url"], "https://example.com/v1")
        self.assertEqual(payload["models"]["deep"], "reasoning")

    def test_delete_primary_key_clears_chat_availability(self):
        self.settings.save_api_settings("deepseek", primary_api_key="primary-key")

        response = self.client.delete("/api/settings/api?slot=primary")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["has_primary_api_key"])
        self.assertFalse(self.settings.has_api_key())

    def test_chat_requires_primary_key(self):
        response = self.client.post("/api/chat", json={"text": "hello"})
        payload = response.get_json()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["code"], "api_not_configured")

    def test_readme_endpoint_still_available_for_about_panel(self):
        response = self.client.get("/api/readme")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertIn("RailGPT", payload["content"])

    def test_index_uses_runtime_title_and_version(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("RailGPT v3.0.0", html)
        self.assertIn("v3.0.0", html)

    def test_favicon_route_serves_release_icon(self):
        response = self.client.get("/favicon.ico")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/x-icon")

    def test_flask_uses_runtime_template_and_static_roots(self):
        self.assertEqual(web_app.app.template_folder, resource_path("templates"))
        self.assertEqual(web_app.app.static_folder, resource_path("static"))


if __name__ == "__main__":
    unittest.main()
