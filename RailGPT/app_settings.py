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


SETTINGS_SCHEMA_VERSION = 1
DEFAULT_PROVIDER_ID = "deepseek"

PROVIDER_REGISTRY: Dict[str, Dict[str, Any]] = {
    "deepseek": {
        "id": "deepseek",
        "label": "DeepSeek",
        "description": "DeepSeek API (OpenAI-compatible request format)",
        "base_url": "https://api.deepseek.com",
        "supports_custom_base_url": False,
        "models": {
            "fast-go": "deepseek-v4-flash",
            "fast-plus": "deepseek-v4-flash",
            "deep": "deepseek-v4-pro",
        },
    }
}


class AppSettingsError(RuntimeError):
    """Base application settings error."""


class UnsupportedProviderError(AppSettingsError):
    """Raised when a provider id is not supported."""


class LLMNotConfiguredError(AppSettingsError):
    """Raised when the app has no configured API key."""


class _UnsupportedProtector:
    def encrypt(self, plaintext: str) -> str:
        raise AppSettingsError("Windows DPAPI is required to save the API key securely.")

    def decrypt(self, ciphertext: str) -> str:
        raise AppSettingsError("Windows DPAPI is required to read the saved API key.")


class DpapiProtector:
    """Small Windows DPAPI wrapper for user-scoped secret storage."""

    CRYPTPROTECT_UI_FORBIDDEN = 0x01

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

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
            ctypes.byref(input_blob),
            "RailGPT API Key",
            None,
            None,
            None,
            self.CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output_blob),
        )
        if not ok:
            raise AppSettingsError(f"DPAPI encrypt failed: {ctypes.WinError()}")

        try:
            protected = self._bytes_from_blob(output_blob)
            return base64.b64encode(protected).decode("ascii")
        finally:
            self._free_blob(output_blob)
            _ = input_buffer

    def decrypt(self, ciphertext: str) -> str:
        protected = base64.b64decode(str(ciphertext or "").encode("ascii"))
        input_blob, input_buffer = self._blob_from_bytes(protected)
        output_blob = self.DATA_BLOB()

        ok = self._crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            None,
            None,
            None,
            None,
            self.CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output_blob),
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
        blob = self.DATA_BLOB(
            cbData=len(payload),
            pbData=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
        )
        return blob, buffer

    @staticmethod
    def _bytes_from_blob(blob: "DpapiProtector.DATA_BLOB") -> bytes:
        if not blob.pbData or not blob.cbData:
            return b""
        return ctypes.string_at(blob.pbData, blob.cbData)

    def _free_blob(self, blob: "DpapiProtector.DATA_BLOB"):
        if blob.pbData:
            self._kernel32.LocalFree(blob.pbData)


def default_settings_path() -> Path:
    appdata_root = (
        os.environ.get("APPDATA")
        or os.environ.get("LOCALAPPDATA")
        or str(Path.home() / "AppData" / "Roaming")
    )
    return Path(appdata_root).joinpath("RailGPT", "settings.enc")


def mask_api_key(api_key: str) -> str:
    value = str(api_key or "").strip()
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


class AppSettingsManager:
    def __init__(
        self,
        config_path: str | os.PathLike[str] | None = None,
        protector=None,
        providers: Dict[str, Dict[str, Any]] | None = None,
    ):
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
        return [
            {
                "id": provider["id"],
                "label": provider["label"],
                "description": provider.get("description", ""),
                "supports_custom_base_url": bool(provider.get("supports_custom_base_url")),
            }
            for provider in self._providers.values()
        ]

    def get_frontend_payload(self) -> Dict[str, Any]:
        state = self._load_state()
        masked_primary = ""
        masked_thinking = ""
        if state.get("primary_api_key_ciphertext"):
            try:
                masked_primary = mask_api_key(self._protect_read(state["primary_api_key_ciphertext"]))
            except AppSettingsError:
                masked_primary = "读取失败，请重新配置"
        if state.get("thinking_api_key_ciphertext"):
            try:
                masked_thinking = mask_api_key(self._protect_read(state["thinking_api_key_ciphertext"]))
            except AppSettingsError:
                masked_thinking = "读取失败，请重新配置"

        return {
            "schema_version": state["schema_version"],
            "provider": state["provider"],
            "providers": self.provider_options(),
            "has_api_key": bool(state.get("primary_api_key_ciphertext")),
            "has_primary_api_key": bool(state.get("primary_api_key_ciphertext")),
            "has_thinking_api_key": bool(state.get("thinking_api_key_ciphertext")),
            "masked_api_key": masked_primary,
            "masked_primary_api_key": masked_primary,
            "masked_thinking_api_key": masked_thinking,
            "thinking_uses_primary_fallback": not bool(state.get("thinking_api_key_ciphertext")),
            "config_path": self.config_path,
            "updated_at": state.get("updated_at", ""),
            "account": {
                "status": "coming_soon",
                "title": "未登录",
                "description": "账号与云同步功能预留中，后续服务器化版本会接入。",
            },
        }

    def has_api_key(self) -> bool:
        state = self._load_state()
        return bool(state.get("primary_api_key_ciphertext"))

    def save_api_settings(
        self,
        provider: str,
        *,
        primary_api_key: str | None = None,
        thinking_api_key: str | None = None,
    ) -> Dict[str, Any]:
        provider = str(provider or "").strip().lower()

        if provider not in self._providers:
            raise UnsupportedProviderError(f"Unsupported provider: {provider}")

        state = self._load_state()
        state["provider"] = provider
        updated = False
        if primary_api_key is not None:
            primary_value = str(primary_api_key or "").strip()
            if not primary_value:
                raise AppSettingsError("Primary API key is required.")
            state["primary_api_key_ciphertext"] = self._protect_write(primary_value)
            updated = True
        if thinking_api_key is not None:
            thinking_value = str(thinking_api_key or "").strip()
            if not thinking_value:
                raise AppSettingsError("Thinking API key is required.")
            state["thinking_api_key_ciphertext"] = self._protect_write(thinking_value)
            updated = True
        if not updated:
            raise AppSettingsError("At least one API key must be provided.")

        state["updated_at"] = self._timestamp()
        self._write_state(state)
        return self.get_frontend_payload()

    def delete_api_key(self, slot: str = "primary") -> Dict[str, Any]:
        state = self._load_state()
        normalized_slot = self._normalize_slot(slot)
        target_field = "primary_api_key_ciphertext" if normalized_slot == "primary" else "thinking_api_key_ciphertext"
        state[target_field] = ""
        state["updated_at"] = self._timestamp()
        self._write_state(state)
        return self.get_frontend_payload()

    def resolve_llm_config(
        self,
        *,
        required: bool = False,
        slot: str = "primary",
        allow_fallback: bool = True,
    ) -> Dict[str, Any]:
        state = self._load_state()
        provider_id = state.get("provider", DEFAULT_PROVIDER_ID)
        provider = self._providers.get(provider_id)
        if provider is None:
            raise UnsupportedProviderError(f"Unsupported provider: {provider_id}")

        normalized_slot = self._normalize_slot(slot)
        api_key = ""
        resolved_slot = normalized_slot
        field_name = "primary_api_key_ciphertext" if normalized_slot == "primary" else "thinking_api_key_ciphertext"
        ciphertext = state.get(field_name, "")
        if not ciphertext and normalized_slot == "thinking" and allow_fallback:
            fallback_ciphertext = state.get("primary_api_key_ciphertext", "")
            if fallback_ciphertext:
                ciphertext = fallback_ciphertext
                resolved_slot = "primary"
        if ciphertext:
            try:
                api_key = self._protect_read(ciphertext)
            except AppSettingsError as exc:
                raise LLMNotConfiguredError(f"Saved API key could not be read: {exc}") from exc

        if required and not api_key:
            if normalized_slot == "thinking":
                raise LLMNotConfiguredError("请先在设置中配置 Thinker API Key，或填写主对话 API Key 作为回退。")
            raise LLMNotConfiguredError("请先在设置中配置 API Key。")

        return {
            "version": self._version,
            "provider": provider_id,
            "base_url": provider["base_url"],
            "models": dict(provider["models"]),
            "api_key": api_key,
            "configured": bool(api_key),
            "slot": resolved_slot,
        }

    def _load_state(self) -> Dict[str, Any]:
        with self._lock:
            if self._cache is None:
                raw = self._read_state_file()
                self._cache = self._normalize_state(raw)
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
        provider = str(data.get("provider") or DEFAULT_PROVIDER_ID).strip().lower()
        if provider not in self._providers:
            provider = DEFAULT_PROVIDER_ID
        return {
            "schema_version": SETTINGS_SCHEMA_VERSION,
            "provider": provider,
            "updated_at": str(data.get("updated_at") or ""),
            "primary_api_key_ciphertext": str(
                data.get("primary_api_key_ciphertext")
                or data.get("api_key_ciphertext")
                or ""
            ),
            "thinking_api_key_ciphertext": str(data.get("thinking_api_key_ciphertext") or ""),
        }

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
