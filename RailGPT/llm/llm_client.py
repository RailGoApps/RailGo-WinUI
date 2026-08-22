from __future__ import annotations

from openai import OpenAI

from app_settings import DEFAULT_PROVIDER_ID, LLMNotConfiguredError, PROVIDER_REGISTRY, get_app_settings


DEEPSEEK_BASE_URL = PROVIDER_REGISTRY[DEFAULT_PROVIDER_ID]["base_url"]
FAST_MODEL = PROVIDER_REGISTRY[DEFAULT_PROVIDER_ID]["models"]["fast-go"]
DEEP_MODEL = PROVIDER_REGISTRY[DEFAULT_PROVIDER_ID]["models"]["deep"]
DEEP_REASONING_EFFORT = "high"


class LLMClient:
    """
    Runtime-configurable DeepSeek/OpenAI-compatible client.

    Supports:
    - generate(): non-streaming full output
    - stream_generate(): streaming token output
    - set_mode(): runtime model switch between fast-go / fast-plus / deep
    - shared settings-backed API key refresh without restart
    """

    def __init__(self, mode="deep", settings=None, credential_slot: str = "primary"):
        self.settings = settings or get_app_settings()
        self.credential_slot = str(credential_slot or "primary").strip().lower()
        self.client = None
        self.mode = None
        self.model = None
        self.models = {}
        self._client_signature = None
        self.set_mode(mode)

    def set_mode(self, mode: str):
        mode = str(mode).strip().lower()
        if mode in {"fast", "fastgo"}:
            mode = "fast-go"
        elif mode == "fastplus":
            mode = "fast-plus"

        model_map = self._resolve_model_map()
        if mode not in model_map:
            raise ValueError(f"Unknown mode: {mode}. Use: fast-go / fast-plus / deep")

        self.mode = mode
        self.models = dict(model_map)
        self.model = self.models[mode]

        # Keep console logging ASCII-only for Windows GBK terminals.
        print(f"[LLM] switched -> {mode.upper()} ({self.model})")

    def get_mode(self) -> str:
        return self.mode

    def is_configured(self) -> bool:
        if self.credential_slot == "thinking":
            bundle = self.settings.resolve_llm_config(
                required=False,
                slot="thinking",
                allow_fallback=True,
            )
            return bool(bundle.get("configured"))
        return self.settings.has_api_key()

    def _resolve_model_map(self):
        bundle = self.settings.resolve_llm_config(
            required=False,
            slot=self.credential_slot,
            allow_fallback=True,
        )
        return dict(bundle["models"])

    def _ensure_client(self):
        bundle = self.settings.resolve_llm_config(
            required=True,
            slot=self.credential_slot,
            allow_fallback=True,
        )
        self.models = dict(bundle["models"])
        self.model = self.models[self.mode]

        signature = (
            bundle["version"],
            bundle["provider"],
            bundle["base_url"],
            bundle["api_key"],
            bundle.get("slot"),
        )
        if self.client is None or self._client_signature != signature:
            self.client = OpenAI(
                api_key=bundle["api_key"],
                base_url=bundle["base_url"],
            )
            self._client_signature = signature

        return self.client

    def _thinking_request_kwargs(self) -> dict:
        if self.mode in {"fast-plus", "deep"}:
            return {
                "reasoning_effort": DEEP_REASONING_EFFORT,
                "extra_body": {"thinking": {"type": "enabled"}},
            }

        return {
            "extra_body": {"thinking": {"type": "disabled"}},
        }

    def generate(
        self,
        messages: list[dict],
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> str:
        client = self._ensure_client()
        if timeout is not None or max_retries is not None:
            option_kwargs = {}
            if timeout is not None:
                option_kwargs["timeout"] = timeout
            if max_retries is not None:
                option_kwargs["max_retries"] = int(max_retries)
            client = client.with_options(**option_kwargs)

        response = client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=False,
            **self._thinking_request_kwargs(),
        )
        return response.choices[0].message.content

    def stream_generate(self, messages: list[dict]):
        response = self._ensure_client().chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True,
            **self._thinking_request_kwargs(),
        )

        for chunk in response:
            delta = chunk.choices[0].delta
            if not delta:
                continue

            # DeepSeek thinking-mode streams reasoning_content separately.
            # UI bubbles should receive final answer tokens only.
            token = getattr(delta, "content", None)
            if token:
                yield token

    def stream_generate_full(self, messages: list[dict]) -> str:
        full_text = ""

        response = self._ensure_client().chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True,
            **self._thinking_request_kwargs(),
        )

        for chunk in response:
            delta = chunk.choices[0].delta
            if not delta:
                continue

            # Do not print reasoning_content; keep console output to final answer.
            token = getattr(delta, "content", None)
            if token:
                print(token, end="", flush=True)
                full_text += token

        print()
        return full_text


__all__ = [
    "DEEPSEEK_BASE_URL",
    "DEEP_MODEL",
    "DEEP_REASONING_EFFORT",
    "FAST_MODEL",
    "LLMClient",
    "LLMNotConfiguredError",
]
