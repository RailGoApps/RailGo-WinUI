"""Anonymous per-installation identity used for polite provider traffic."""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

from app_runtime import user_data_path


_LOCK = threading.Lock()
_CACHED = ""


def get_installation_id() -> str:
    global _CACHED
    if _CACHED:
        return _CACHED
    with _LOCK:
        if _CACHED:
            return _CACHED
        path = Path(user_data_path("client_identity.json"))
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            data = {}
        candidate = str(data.get("installation_id") or "").strip().lower()
        try:
            installation_id = str(uuid.UUID(candidate))
        except (ValueError, AttributeError):
            installation_id = str(uuid.uuid4())
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "installation_id": installation_id,
                        "purpose": "Anonymous RailGPT client identification for polite data-provider traffic",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        _CACHED = installation_id
        return installation_id


__all__ = ["get_installation_id"]
