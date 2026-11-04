from __future__ import annotations

import base64
import ctypes
import json
import os
import threading
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlsplit, urlunsplit


SETTINGS_SCHEMA_VERSION = 3
DEFAULT_PROVIDER_ID = "deepseek"
MODE_IDS = ("fast-go", "fast-plus", "deep")

# The catalog is deliberately helpful rather than restrictive: every mode also
# accepts a user-entered model ID for any compatible Chat Completions endpoint.
PROVIDER_REGISTRY: Dict[str, Dict[str, Any]] = {
    "deepseek": {
        "id": "deepseek",
        "label": "DeepSeek",
        "description": "DeepSeek 官方 API（OpenAI 兼容）",
        "base_url": "https://api.deepseek.com",
        "supports_custom_base_url": False,
        "thinking_strategy": "deepseek",
        "models": {"fast-go": "deepseek-flash", "fast-plus": "deepseek-flash", "deep": "deepseek-flash"},
        "model_options": ["deepseek-flash"],
    },
    "openai": {
        "id": "openai",
        "label": "OpenAI（ChatGPT）",
        "description": "OpenAI 官方 API（Chat Completions）",
        "base_url": "https://api.openai.com/v1",
        "supports_custom_base_url": False,
        "thinking_strategy": "openai",
        "models": {"fast-go": "gpt-5-mini", "fast-plus": "gpt-5-mini", "deep": "gpt-5"},
        "model_options": ["gpt-5-mini", "gpt-5", "gpt-4.1-mini", "gpt-4.1"],
    },
    "openai_compatible": {
        "id": "openai_compatible",
        "label": "OpenAI-compatible",
        "description": "自定义兼容服务（用户提供 Base URL）",
        "base_url": "",
        "supports_custom_base_url": True,
        "thinking_strategy": "generic",
        "models": {"fast-go": "", "fast-plus": "", "deep": ""},
        "model_options": [],
    },
}


class AppSettingsError(RuntimeError):
    """Base application settings error."""


class UnsupportedProviderError(AppSettingsError):
    """Raised when a provider id is not supported."""


class LLMNotConfiguredError(AppSettingsError):
    """Raised when the app has no configured API key or runnable model."""


class _UnsupportedProtector:
    def encrypt(self, plaintext: str) -> str:
        raise AppSettingsError("Windows DPAPI is required to save the API key securely.")

    def decrypt(self, ciphertext: str) -> str:
        raise AppSettingsError("Windows DPAPI is required to read the saved API key.")


class DpapiProtector:
    """Small Windows DPAPI wrapper for user-scoped secret storage."""

    CRYPTPROTECT_UI_FORBIDDEN = 0x01

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    def __init__(self):
        if os.name != "nt":
            raise AppSettingsError("Windows DPAPI is only available on Windows.")
        self._crypt32 = ctypes.windll.crypt32
        self._kernel32 = ctypes.windll.kernel32

    def encrypt(self, plaintext: str) -> str:
        raw = str(plaintext or "").encode("utf-8")
        input_blob, input_buffer = self._blob_from_bytes(raw)
        output_blob = self.DATA_BLOB()
        ok = self._crypt32.CryptProtectData(
            ctypes.byref(input_blob), "RailGPT API Key", None, None, None,
            self.CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output_blob),
        )
        if not ok:
            raise AppSettingsError(f"DPAPI encrypt failed: {ctypes.WinError()}")
        try:
            return base64.b64encode(self._bytes_from_blob(output_blob)).decode("ascii")
        finally:
            self._free_blob(output_blob)
            _ = input_buffer

    def decrypt(self, ciphertext: str) -> str:
        protected = base64.b64decode(str(ciphertext or "").encode("ascii"))
        input_blob, input_buffer = self._blob_from_bytes(protected)
        output_blob = self.DATA_BLOB()
        ok = self._crypt32.CryptUnprotectData(
            ctypes.byref(input_blob), None, None, None, None,
            self.CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output_blob),
        )
        if not ok:
            raise AppSettingsError(f"DPAPI decrypt failed: {ctypes.WinError()}")
        try:
            return self._bytes_from_blob(output_blob).decode("utf-8")
        finally:
            self._free_blob(output_blob)
            _ = input_buffer

    def _blob_from_bytes(self, payload: bytes):
        buffer = ctypes.create_string_buffer(payload)
        return self.DATA_BLOB(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer

    @staticmethod
    def _bytes_from_blob(blob: "DpapiProtector.DATA_BLOB") -> bytes:
        return ctypes.string_at(blob.pbData, blob.cbData) if blob.pbData and blob.cbData else b""

    def _free_blob(self, blob: "DpapiProtector.DATA_BLOB"):
        if blob.pbData:
            self._kernel32.LocalFree(blob.pbData)


def default_settings_path() -> Path:
    appdata_root = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata_root).joinpath("RailGPT", "settings.enc")


def mask_api_key(api_key: str) -> str:
    value = str(api_key or "").strip()
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


def normalize_compatible_base_url(value: str) -> str:
    """Allow HTTPS endpoints and local HTTP development endpoints only."""
    raw = str(value or "").strip()
    if not raw:
        raise AppSettingsError("自定义 OpenAI-compatible 服务需要 Base URL。")
    parsed = urlsplit(raw)
    host = (parsed.hostname or "").lower()
    is_loopback = host in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme not in {"https", "http"}:
        raise AppSettingsError("Base URL 必须使用 HTTPS；本地服务可使用 HTTP。")
    if parsed.scheme == "http" and not is_loopback:
        raise AppSettingsError("非本地 OpenAI-compatible 服务必须使用 HTTPS。")
    if not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AppSettingsError("Base URL 格式无效。")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


class AppSettingsManager:
    def __init__(self, config_path: str | os.PathLike[str] | None = None, protector=None, providers: Dict[str, Dict[str, Any]] | None = None):
        self._providers = dict(providers or PROVIDER_REGISTRY)
        if DEFAULT_PROVIDER_ID not in self._providers:
            raise AppSettingsError("Default provider is missing from the provider registry.")
        self._config_path = Path(config_path or default_settings_path())
        self._protector = protector or (DpapiProtector() if os.name == "nt" else _UnsupportedProtector())
        self._lock = threading.RLock()
        self._cache: Dict[str, Any] | None = None
        self._version = 0

    @property
    def config_path(self) -> str:
        return str(self._config_path)

    @property
    def version(self) -> int:
        return self._version

    def provider_options(self) -> list[Dict[str, Any]]:
        return [{
            "id": provider["id"], "label": provider["label"],
            "description": provider.get("description", ""),
            "supports_custom_base_url": bool(provider.get("supports_custom_base_url")),
            "model_options": list(provider.get("model_options") or []),
            "default_models": dict(provider.get("models") or {}),
        } for provider in self._providers.values()]

    def get_frontend_payload(self) -> Dict[str, Any]:
        state = self._load_state()
        provider = self._provider_for_state(state)
        primary = self._masked_secret(state.get("primary_api_key_ciphertext", ""))
        thinking = self._masked_secret(state.get("thinking_api_key_ciphertext", ""))
        return {
            "schema_version": state["schema_version"], "provider": state["provider"],
            "providers": self.provider_options(), "base_url": self._effective_base_url(state, provider),
            "custom_base_url": state.get("custom_base_url", ""), "models": dict(state["models"]),
            "has_api_key": bool(state.get("primary_api_key_ciphertext")),
            "has_primary_api_key": bool(state.get("primary_api_key_ciphertext")),
            "has_thinking_api_key": bool(state.get("thinking_api_key_ciphertext")),
            "masked_api_key": primary, "masked_primary_api_key": primary, "masked_thinking_api_key": thinking,
            "thinking_uses_primary_fallback": not bool(state.get("thinking_api_key_ciphertext")),
            "config_path": self.config_path, "updated_at": state.get("updated_at", ""),
            "account": {"status": "coming_soon", "title": "未登录", "description": "账号与云同步功能预留中，后续服务器化版本会接入。"},
        }

    def has_api_key(self) -> bool:
        return bool(self._load_state().get("primary_api_key_ciphertext"))

    def save_api_settings(self, provider: str, *, primary_api_key: str | None = None, thinking_api_key: str | None = None, models: Dict[str, Any] | None = None, custom_base_url: str | None = None) -> Dict[str, Any]:
        provider_id = str(provider or "").strip().lower()
        if provider_id not in self._providers:
            raise UnsupportedProviderError(f"Unsupported provider: {provider_id}")
        state = self._load_state()
        changed = state.get("provider") != provider_id
        state["provider"] = provider_id
        provider_meta = self._providers[provider_id]
        if provider_meta.get("supports_custom_base_url"):
            candidate = custom_base_url if custom_base_url is not None else state.get("custom_base_url", "")
            normalized = normalize_compatible_base_url(candidate)
            changed = changed or normalized != state.get("custom_base_url", "")
            state["custom_base_url"] = normalized
        else:
            changed = changed or bool(state.get("custom_base_url"))
            state["custom_base_url"] = ""
        if models is not None:
            normalized_models = self._normalize_models(models, provider_meta, require_complete=True)
            changed = changed or normalized_models != state.get("models")
            state["models"] = normalized_models
        elif changed:
            state["models"] = self._normalize_models({}, provider_meta, require_complete=False)
        if primary_api_key is not None:
            value = str(primary_api_key or "").strip()
            if not value:
                raise AppSettingsError("Primary API key is required.")
            state["primary_api_key_ciphertext"] = self._protect_write(value)
            changed = True
        if thinking_api_key is not None:
            value = str(thinking_api_key or "").strip()
            if not value:
                raise AppSettingsError("Thinking API key is required.")
            state["thinking_api_key_ciphertext"] = self._protect_write(value)
            changed = True
        if not changed:
            raise AppSettingsError("请修改服务商、模型或至少一个 API Key。")
        state["updated_at"] = self._timestamp()
        self._write_state(state)
        return self.get_frontend_payload()

    def delete_api_key(self, slot: str = "primary") -> Dict[str, Any]:
        state = self._load_state()
        target = "primary_api_key_ciphertext" if self._normalize_slot(slot) == "primary" else "thinking_api_key_ciphertext"
        state[target] = ""
        state["updated_at"] = self._timestamp()
        self._write_state(state)
        return self.get_frontend_payload()

    def resolve_llm_config(self, *, required: bool = False, slot: str = "primary", allow_fallback: bool = True) -> Dict[str, Any]:
        state = self._load_state()
        provider = self._provider_for_state(state)
        normalized_slot = self._normalize_slot(slot)
        field = "primary_api_key_ciphertext" if normalized_slot == "primary" else "thinking_api_key_ciphertext"
        ciphertext, resolved_slot = state.get(field, ""), normalized_slot
        if not ciphertext and normalized_slot == "thinking" and allow_fallback:
            ciphertext, resolved_slot = state.get("primary_api_key_ciphertext", ""), "primary"
        api_key = ""
        if ciphertext:
            try:
                api_key = self._protect_read(ciphertext)
            except AppSettingsError as exc:
                raise LLMNotConfiguredError(f"Saved API key could not be read: {exc}") from exc
        models = dict(state["models"])
        if required and not api_key:
            message = "请先在设置中配置 Thinker API Key，或填写主对话 API Key 作为回退。" if normalized_slot == "thinking" else "请先在设置中配置 API Key。"
            raise LLMNotConfiguredError(message)
        if required and any(not str(models.get(mode) or "").strip() for mode in MODE_IDS):
            raise LLMNotConfiguredError("请先为 FAST-GO、FAST-PLUS 和 DEEP 分别选择模型。")
        return {
            "version": self._version, "provider": state["provider"], "provider_label": provider["label"],
            "base_url": self._effective_base_url(state, provider), "models": models,
            "thinking_strategy": provider.get("thinking_strategy", "generic"),
            "api_key": api_key, "configured": bool(api_key), "slot": resolved_slot,
        }

    def _load_state(self) -> Dict[str, Any]:
        with self._lock:
            if self._cache is None:
                self._cache = self._normalize_state(self._read_state_file())
            return dict(self._cache)

    def _read_state_file(self) -> Dict[str, Any]:
        if not self._config_path.exists():
            return {}
        try:
            with self._config_path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return {}

    def _write_state(self, state: Dict[str, Any]):
        normalized = self._normalize_state(state)
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        with self._config_path.open("w", encoding="utf-8") as handle:
            json.dump(normalized, handle, ensure_ascii=False, indent=2)
        with self._lock:
            self._cache = normalized
            self._version += 1

    def _normalize_state(self, payload: Dict[str, Any] | None) -> Dict[str, Any]:
        data = payload if isinstance(payload, dict) else {}
        provider_id = str(data.get("provider") or DEFAULT_PROVIDER_ID).strip().lower()
        if provider_id not in self._providers:
            provider_id = DEFAULT_PROVIDER_ID
        provider = self._providers[provider_id]
        raw_url = str(data.get("custom_base_url") or "").strip()
        custom_base_url = ""
        if provider.get("supports_custom_base_url") and raw_url:
            try:
                custom_base_url = normalize_compatible_base_url(raw_url)
            except AppSettingsError:
                custom_base_url = ""
        models = data.get("models")
        try:
            old_schema = int(data.get("schema_version") or 1)
        except (TypeError, ValueError):
            old_schema = 1
        if provider_id == "deepseek" and old_schema < 3 and isinstance(models, dict):
            legacy_models = {"deepseek-v4-flash", "deepseek-v4-flash-vision-exp", "deepseek-v4-pro"}
            models = {
                mode: "deepseek-flash" if str(value).strip() in legacy_models else value
                for mode, value in models.items()
            }
        return {
            "schema_version": SETTINGS_SCHEMA_VERSION, "provider": provider_id,
            "custom_base_url": custom_base_url,
            "models": self._normalize_models(models, provider, require_complete=False),
            "updated_at": str(data.get("updated_at") or ""),
            "primary_api_key_ciphertext": str(data.get("primary_api_key_ciphertext") or data.get("api_key_ciphertext") or ""),
            "thinking_api_key_ciphertext": str(data.get("thinking_api_key_ciphertext") or ""),
        }

    @staticmethod
    def _normalize_models(value: Any, provider: Dict[str, Any], *, require_complete: bool) -> Dict[str, str]:
        raw = value if isinstance(value, dict) else {}
        defaults = provider.get("models") or {}
        models = {mode: str(raw.get(mode) or defaults.get(mode) or "").strip() for mode in MODE_IDS}
        if require_complete and any(not item for item in models.values()):
            raise AppSettingsError("请为 FAST-GO、FAST-PLUS 和 DEEP 分别填写模型 ID。")
        if any(len(item) > 160 or "\n" in item or "\r" in item for item in models.values()):
            raise AppSettingsError("模型 ID 格式无效。")
        return models

    def _provider_for_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        provider = self._providers.get(state.get("provider"))
        if provider is None:
            raise UnsupportedProviderError(f"Unsupported provider: {state.get('provider')}")
        return provider

    @staticmethod
    def _effective_base_url(state: Dict[str, Any], provider: Dict[str, Any]) -> str:
        return str(state.get("custom_base_url") or "") if provider.get("supports_custom_base_url") else str(provider["base_url"])

    def _masked_secret(self, ciphertext: str) -> str:
        if not ciphertext:
            return ""
        try:
            return mask_api_key(self._protect_read(ciphertext))
        except AppSettingsError:
            return "读取失败，请重新配置"

    def _protect_write(self, plaintext: str) -> str:
        try:
            return self._protector.encrypt(plaintext)
        except AppSettingsError:
            raise
        except Exception as exc:
            raise AppSettingsError(f"Unable to encrypt API key: {exc}") from exc

    def _protect_read(self, ciphertext: str) -> str:
        try:
            return self._protector.decrypt(ciphertext)
        except AppSettingsError:
            raise
        except Exception as exc:
            raise AppSettingsError(f"Unable to decrypt API key: {exc}") from exc

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _normalize_slot(slot: str) -> str:
        normalized = str(slot or "primary").strip().lower()
        if normalized not in {"primary", "thinking"}:
            raise AppSettingsError(f"Unsupported API key slot: {slot}")
        return normalized


_SETTINGS_MANAGER = AppSettingsManager()


def get_app_settings() -> AppSettingsManager:
    return _SETTINGS_MANAGER
