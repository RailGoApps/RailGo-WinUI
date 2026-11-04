import json
import os
import tempfile
import unittest

from app_settings import AppSettingsError, AppSettingsManager, DpapiProtector, mask_api_key, normalize_compatible_base_url


class FakeProtector:
    def encrypt(self, plaintext: str) -> str:
        return f"enc::{plaintext}"

    def decrypt(self, ciphertext: str) -> str:
        if not str(ciphertext).startswith("enc::"):
            raise ValueError("unexpected ciphertext")
        return str(ciphertext)[5:]


class AppSettingsManagerTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.settings_path = os.path.join(self.temp_dir.name, "settings.enc")
        self.manager = AppSettingsManager(
            config_path=self.settings_path,
            protector=FakeProtector(),
        )

    def test_masks_api_key_for_frontend(self):
        self.manager.save_api_settings("deepseek", primary_api_key="sk-1234567890")
        payload = self.manager.get_frontend_payload()

        self.assertTrue(payload["has_primary_api_key"])
        self.assertEqual(payload["masked_primary_api_key"], "sk-1*****7890")

    def test_thinking_slot_falls_back_to_primary_key(self):
        self.manager.save_api_settings("deepseek", primary_api_key="primary-key")

        bundle = self.manager.resolve_llm_config(
            required=True,
            slot="thinking",
            allow_fallback=True,
        )

        self.assertEqual(bundle["api_key"], "primary-key")
        self.assertEqual(bundle["slot"], "primary")

    def test_thinking_slot_prefers_dedicated_key(self):
        self.manager.save_api_settings(
            "deepseek",
            primary_api_key="primary-key",
            thinking_api_key="thinking-key",
        )

        bundle = self.manager.resolve_llm_config(
            required=True,
            slot="thinking",
            allow_fallback=True,
        )

        self.assertEqual(bundle["api_key"], "thinking-key")
        self.assertEqual(bundle["slot"], "thinking")

    def test_delete_thinking_key_preserves_primary_key(self):
        self.manager.save_api_settings(
            "deepseek",
            primary_api_key="primary-key",
            thinking_api_key="thinking-key",
        )

        payload = self.manager.delete_api_key("thinking")

        self.assertTrue(payload["has_primary_api_key"])
        self.assertFalse(payload["has_thinking_api_key"])
        self.assertTrue(self.manager.has_api_key())

    def test_legacy_single_key_payload_is_migrated_to_primary_slot(self):
        with open(self.settings_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "provider": "deepseek",
                    "api_key_ciphertext": "enc::legacy-key",
                    "updated_at": "2026-05-03 12:00:00",
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )

        legacy_manager = AppSettingsManager(
            config_path=self.settings_path,
            protector=FakeProtector(),
        )
        bundle = legacy_manager.resolve_llm_config(required=True, slot="primary")

        self.assertEqual(bundle["api_key"], "legacy-key")
        self.assertTrue(legacy_manager.has_api_key())

    def test_v1_state_migrates_to_flash_without_losing_key(self):
        with open(self.settings_path, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "provider": "deepseek", "primary_api_key_ciphertext": "enc::legacy-key"}, handle)
        manager = AppSettingsManager(config_path=self.settings_path, protector=FakeProtector())
        payload = manager.get_frontend_payload()

        self.assertEqual(payload["schema_version"], 3)
        self.assertEqual(payload["models"]["deep"], "deepseek-flash")
        self.assertEqual(manager.resolve_llm_config(required=True)["api_key"], "legacy-key")

    def test_openai_models_are_saved_per_mode(self):
        self.manager.save_api_settings(
            "openai",
            primary_api_key="openai-key",
            models={"fast-go": "gpt-5-mini", "fast-plus": "gpt-4.1-mini", "deep": "gpt-5"},
        )
        bundle = self.manager.resolve_llm_config(required=True)

        self.assertEqual(bundle["base_url"], "https://api.openai.com/v1")
        self.assertEqual(bundle["thinking_strategy"], "openai")
        self.assertEqual(bundle["models"]["deep"], "gpt-5")

    def test_custom_compatible_provider_accepts_loopback_and_rejects_plain_http_remote(self):
        self.manager.save_api_settings(
            "openai_compatible",
            primary_api_key="local-key",
            custom_base_url="http://127.0.0.1:8000/v1/",
            models={"fast-go": "local-fast", "fast-plus": "local-plus", "deep": "local-deep"},
        )
        self.assertEqual(self.manager.get_frontend_payload()["base_url"], "http://127.0.0.1:8000/v1")
        with self.assertRaises(AppSettingsError):
            normalize_compatible_base_url("http://example.com/v1")

    def test_custom_compatible_provider_requires_all_model_slots(self):
        with self.assertRaises(AppSettingsError):
            self.manager.save_api_settings(
                "openai_compatible",
                primary_api_key="custom-key",
                custom_base_url="https://example.com/v1",
                models={"fast-go": "a", "fast-plus": "", "deep": "c"},
            )


class HelpersTest(unittest.TestCase):
    def test_mask_api_key_handles_short_values(self):
        self.assertEqual(mask_api_key("abcd"), "****")
        self.assertEqual(mask_api_key(""), "")

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
    def test_dpapi_round_trip(self):
        protector = DpapiProtector()
        ciphertext = protector.encrypt("railgpt-secret")
        plaintext = protector.decrypt(ciphertext)
        self.assertEqual(plaintext, "railgpt-secret")


if __name__ == "__main__":
    unittest.main()
