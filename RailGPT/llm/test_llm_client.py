import unittest
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

from llm.llm_client import (
    DEEPSEEK_BASE_URL,
    DEEP_MODEL,
    DEEP_REASONING_EFFORT,
    FAST_MODEL,
    LLMClient,
)


class FakeSettings:
    def __init__(self, primary_key="primary-key", thinking_key=None):
        self.primary_key = primary_key
        self.thinking_key = thinking_key
        self.version = 0
        self.models = {
            "fast-go": FAST_MODEL,
            "fast-plus": FAST_MODEL,
            "deep": DEEP_MODEL,
        }

    def has_api_key(self):
        return bool(self.primary_key)

    def rotate(self, *, primary_key=None, thinking_key=None):
        if primary_key is not None:
            self.primary_key = primary_key
        if thinking_key is not None:
            self.thinking_key = thinking_key
        self.version += 1

    def resolve_llm_config(self, *, required=False, slot="primary", allow_fallback=True):
        normalized_slot = "thinking" if slot == "thinking" else "primary"
        resolved_slot = normalized_slot
        api_key = self.primary_key if normalized_slot == "primary" else self.thinking_key
        if not api_key and normalized_slot == "thinking" and allow_fallback:
            api_key = self.primary_key
            resolved_slot = "primary"
        if required and not api_key:
            raise RuntimeError("not configured")
        return {
            "version": self.version,
            "provider": "deepseek",
            "base_url": DEEPSEEK_BASE_URL,
            "models": dict(self.models),
            "api_key": api_key or "",
            "configured": bool(api_key),
            "slot": resolved_slot,
        }


class LLMClientConfigTest(unittest.TestCase):
    def test_uses_deepseek_v4_models_from_current_api_docs(self):
        settings = FakeSettings()
        with patch("llm.llm_client.OpenAI") as openai_cls:
            fast_client = LLMClient(mode="fast-go", settings=settings)
            fast_plus_client = LLMClient(mode="fast-plus", settings=settings)
            deep_client = LLMClient(mode="deep", settings=settings)

        openai_cls.assert_not_called()
        self.assertEqual(fast_client.models["fast-go"], FAST_MODEL)
        self.assertEqual(fast_client.models["fast-plus"], FAST_MODEL)
        self.assertEqual(fast_client.models["deep"], DEEP_MODEL)
        self.assertEqual(fast_client.model, FAST_MODEL)
        self.assertEqual(fast_plus_client.model, FAST_MODEL)
        self.assertEqual(deep_client.model, DEEP_MODEL)

    def test_fast_go_disables_thinking(self):
        fake_client = self._fake_openai_client()
        settings = FakeSettings()
        with patch("llm.llm_client.OpenAI", return_value=fake_client):
            client = LLMClient(mode="fast-go", settings=settings)
            client.generate([{"role": "user", "content": "hi"}])

        fake_client.chat.completions.create.assert_called_once_with(
            model=FAST_MODEL,
            messages=[{"role": "user", "content": "hi"}],
            stream=False,
            extra_body={"thinking": {"type": "disabled"}},
        )

    def test_fast_plus_uses_flash_with_thinking(self):
        fake_client = self._fake_openai_client()
        settings = FakeSettings()
        with patch("llm.llm_client.OpenAI", return_value=fake_client):
            client = LLMClient(mode="fast-plus", settings=settings)
            client.generate([{"role": "user", "content": "hi"}])

        fake_client.chat.completions.create.assert_called_once_with(
            model=FAST_MODEL,
            messages=[{"role": "user", "content": "hi"}],
            stream=False,
            reasoning_effort=DEEP_REASONING_EFFORT,
            extra_body={"thinking": {"type": "enabled"}},
        )

    def test_deep_uses_pro_with_thinking(self):
        fake_client = self._fake_openai_client()
        settings = FakeSettings()
        with patch("llm.llm_client.OpenAI", return_value=fake_client):
            client = LLMClient(mode="deep", settings=settings)
            client.generate([{"role": "user", "content": "hi"}])

        fake_client.chat.completions.create.assert_called_once_with(
            model=DEEP_MODEL,
            messages=[{"role": "user", "content": "hi"}],
            stream=False,
            reasoning_effort=DEEP_REASONING_EFFORT,
            extra_body={"thinking": {"type": "enabled"}},
        )

    def test_stream_outputs_content_tokens_only(self):
        reasoning_chunk = SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(reasoning_content="hidden", content=None))]
        )
        content_chunk = SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(reasoning_content=None, content="visible"))]
        )
        fake_client = self._fake_openai_client(stream_chunks=[reasoning_chunk, content_chunk])
        settings = FakeSettings()
        with patch("llm.llm_client.OpenAI", return_value=fake_client):
            client = LLMClient(mode="deep", settings=settings)
            tokens = list(client.stream_generate([{"role": "user", "content": "hi"}]))

        self.assertEqual(tokens, ["visible"])

    def test_thinking_slot_falls_back_to_primary_key(self):
        fake_client = self._fake_openai_client()
        settings = FakeSettings(primary_key="primary-key", thinking_key=None)
        with patch("llm.llm_client.OpenAI", return_value=fake_client) as openai_cls:
            client = LLMClient(mode="fast-go", settings=settings, credential_slot="thinking")
            client.generate([{"role": "user", "content": "hi"}])

        openai_cls.assert_called_once_with(
            api_key="primary-key",
            base_url=DEEPSEEK_BASE_URL,
        )

    def test_refreshes_openai_client_after_settings_change(self):
        fake_client_one = self._fake_openai_client()
        fake_client_two = self._fake_openai_client()
        settings = FakeSettings(primary_key="first-key")

        with patch("llm.llm_client.OpenAI", side_effect=[fake_client_one, fake_client_two]) as openai_cls:
            client = LLMClient(mode="fast-go", settings=settings)
            client.generate([{"role": "user", "content": "hi"}])
            settings.rotate(primary_key="second-key")
            client.generate([{"role": "user", "content": "again"}])

        self.assertEqual(openai_cls.call_count, 2)
        openai_cls.assert_any_call(api_key="first-key", base_url=DEEPSEEK_BASE_URL)
        openai_cls.assert_any_call(api_key="second-key", base_url=DEEPSEEK_BASE_URL)

    def _fake_openai_client(self, stream_chunks=None):
        fake_client = MagicMock()
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )

        def create(**kwargs):
            if kwargs.get("stream"):
                return iter(stream_chunks or [])
            return response

        fake_client.chat.completions.create.side_effect = create
        return fake_client


if __name__ == "__main__":
    unittest.main()
