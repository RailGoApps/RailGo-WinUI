"""Persistent coach structure and on-demand media assets.

RailGo identifies a concrete trainset with ``carCode``.  The train-number
binding is short lived, while the structural description of a concrete set is
stored as an immutable local asset.  Remote image URLs never leave this module.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List
from urllib.parse import urlparse

from agent.psw import AgentState
from app_runtime import user_data_path
from tools.rail.http_client import RAILGO_HEADERS, http_get
from tools.rail.rail_store import RailStore, railstore
from tools.rail.railgo_client import (
    RailGoContractError,
    RailGoTemporaryError,
    fetch_coach_pic_v2,
)


_CAR_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{4,39}$")
_ALLOWED_MEDIA_HOSTS = {"res.railgo.zenglingkun.cn"}
_MAX_MEDIA_BYTES = 10 * 1024 * 1024
_MAX_DIMENSION = 12_000
_MAX_PIXELS = 60_000_000


def _emit(psw: Any, state: AgentState, detail: str) -> None:
    if psw:
        psw.set_state(state, detail)


def _stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _clean_items(value: Any, limit: int = 64) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:limit]:
        if isinstance(item, dict):
            result.append(
                {str(k): v for k, v in item.items() if str(k).lower() not in {"url", "pictureurl", "picurl", "remote_url"}}
            )
    return result


def _media_url(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return ""
    return str(item.get("url") or item.get("pictureUrl") or item.get("picUrl") or "").strip()


def _label(item: Dict[str, Any], default: str) -> str:
    return str(
        item.get("pictureName")
        or item.get("coachNo")
        or item.get("name")
        or item.get("label")
        or default
    ).strip()


def _selector_from_label(label: str, default: str) -> str:
    match = re.search(r"(?<!\d)(\d{1,2})(?:号)?车", label)
    if match:
        return f"{int(match.group(1)):02d}"
    normalized = re.sub(r"\s+", "_", label.strip().lower())
    return normalized[:80] or default


def parse_coach_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise RailGoContractError("coach data is not an object")
    car_code = str(data.get("carCode") or "").strip().upper()
    if not _CAR_CODE_RE.fullmatch(car_code):
        raise RailGoContractError("coach data has an invalid carCode")

    car_info = _clean_items(data.get("carInfo"), 32)
    coaches = _clean_items(data.get("coachPicList"), 32)
    details = _clean_items(data.get("coachDetailPicList"), 32)
    if not car_info and not coaches:
        raise RailGoContractError("coach data has no structural information")

    structural = {
        "carCode": car_code,
        "carType": str(data.get("carType") or "").strip(),
        "trainStyle": str(data.get("trainStyle") or "").strip(),
        "carInfo": car_info,
        "coachPicList": coaches,
        "coachDetailPicList": details,
    }
    locators: List[Dict[str, str]] = []
    whole_url = _media_url(data.get("carPic"))
    if whole_url:
        locators.append({"media_kind": "whole_train", "selector": "default", "remote_url": whole_url})
    for index, item in enumerate(data.get("coachPicList") or []):
        if not isinstance(item, dict):
            continue
        url = _media_url(item)
        if url:
            label = _label(item, str(index + 1))
            locators.append(
                {"media_kind": "coach", "selector": _selector_from_label(label, str(index + 1)), "remote_url": url}
            )
    for index, item in enumerate(data.get("coachDetailPicList") or []):
        if not isinstance(item, dict):
            continue
        url = _media_url(item)
        if url:
            label = _label(item, f"interior-{index + 1}")
            locators.append(
                {"media_kind": "interior", "selector": _selector_from_label(label, f"interior-{index + 1}"), "remote_url": url}
            )

    structural["fingerprint"] = _stable_hash(structural)
    return {"structural": structural, "locators": locators}


def _safe_catalog(items: Iterable[Dict[str, Any]], kind: str) -> List[Dict[str, str]]:
    result = []
    for index, item in enumerate(items):
        label = _label(item, f"{kind}-{index + 1}")
        result.append({"kind": kind, "selector": _selector_from_label(label, str(index + 1)), "label": label})
    return result


def _image_dimensions(payload: bytes, mime_type: str) -> tuple[int, int]:
    if mime_type == "image/png" and payload.startswith(b"\x89PNG\r\n\x1a\n") and len(payload) >= 24:
        return struct.unpack(">II", payload[16:24])
    if mime_type == "image/jpeg" and payload.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 9 < len(payload):
            if payload[offset] != 0xFF:
                offset += 1
                continue
            marker = payload[offset + 1]
            offset += 2
            if marker in {0xD8, 0xD9}:
                continue
            if offset + 2 > len(payload):
                break
            size = int.from_bytes(payload[offset:offset + 2], "big")
            if size < 2 or offset + size > len(payload):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                height = int.from_bytes(payload[offset + 3:offset + 5], "big")
                width = int.from_bytes(payload[offset + 5:offset + 7], "big")
                return width, height
            offset += size
    raise RailGoContractError("unsupported or damaged coach image")


class CoachAssetService:
    def __init__(
        self,
        store: RailStore = railstore,
        fetcher: Callable[[str], Dict[str, Any]] = fetch_coach_pic_v2,
        media_root: str | None = None,
    ):
        self.store = store
        self.fetcher = fetcher
        self.media_root = Path(media_root or user_data_path("media", "coach"))
        self.media_root.mkdir(parents=True, exist_ok=True)

    def _evidence(self, train: str, asset: Dict[str, Any], binding: Dict[str, Any] | None) -> Dict[str, Any]:
        coaches = list(asset.get("coach_catalog_json") or [])
        details = list(asset.get("detail_catalog_json") or [])
        labels = {
            (item["kind"], item["selector"]): item["label"]
            for item in (_safe_catalog(coaches, "coach") + _safe_catalog(details, "interior"))
        }
        media_catalog = [
            {
                "kind": item["media_kind"],
                "selector": item["selector"],
                "label": labels.get((item["media_kind"], item["selector"]), "整列总图" if item["media_kind"] == "whole_train" else item["selector"]),
            }
            for item in self.store.list_coach_media_locators(str(asset.get("car_code") or ""))
        ]
        return {
            "train": train,
            "carCode": asset.get("car_code"),
            "carType": asset.get("car_type"),
            "trainStyle": asset.get("train_style"),
            "carInfo": list(asset.get("car_info_json") or []),
            "coachPicList": coaches,
            "coachDetailPicList": details,
            "mediaCatalog": media_catalog or (_safe_catalog(coaches, "coach") + _safe_catalog(details, "interior")),
            "bindingObservedAt": (binding or {}).get("observed_at"),
            "bindingFresh": bool((binding or {}).get("fresh")),
        }

    def get_layout(self, train: str, psw: Any = None) -> Dict[str, Any]:
        train = str(train or "").strip().upper()
        binding = self.store.get_coach_binding(train)
        if binding and binding.get("fresh"):
            asset = self.store.get_coach_asset(binding["car_code"])
            if asset:
                _emit(psw, AgentState.COACH_BINDING_HIT, f"coach binding hit {train}->{binding['car_code']}")
                return {"evidence": self._evidence(train, asset, binding), "source": binding.get("source_json") or {}, "cache_status": "fresh"}

        _emit(psw, AgentState.COACH_BINDING_MISS, f"coach binding refresh required for {train}")
        try:
            payload = self.fetcher(train)
            parsed = parse_coach_payload(payload.get("data") if isinstance(payload, dict) else {})
        except RailGoTemporaryError:
            if binding and binding.get("stale_usable"):
                asset = self.store.get_coach_asset(binding["car_code"])
                if asset:
                    _emit(psw, AgentState.COACH_BINDING_STALE, f"temporary RailGo failure; using stale coach binding for {train}")
                    return {"evidence": self._evidence(train, asset, binding), "source": binding.get("source_json") or {}, "cache_status": "stale"}
            raise

        structural = parsed["structural"]
        outcome = self.store.save_coach_asset(
            {
                "car_code": structural["carCode"],
                "car_type": structural["carType"],
                "train_style": structural["trainStyle"],
                "car_info": structural["carInfo"],
                "coach_catalog": structural["coachPicList"],
                "detail_catalog": structural["coachDetailPicList"],
                "structural_fingerprint": structural["fingerprint"],
            }
        )
        if outcome == "conflict":
            _emit(psw, AgentState.COACH_ASSET_CONFLICT, f"immutable coach asset conflict: {structural['carCode']}")
        elif outcome == "inserted":
            _emit(psw, AgentState.COACH_ASSET_WRITE, f"coach asset stored: {structural['carCode']}")
        self.store.save_coach_media_locators(structural["carCode"], parsed["locators"])
        source = payload.get("_railgo") if isinstance(payload, dict) else {}
        self.store.save_coach_binding(train, structural["carCode"], source)
        stored = self.store.get_coach_asset(structural["carCode"])
        if not stored:
            raise RailGoContractError("coach asset could not be stored")
        current = self.store.get_coach_binding(train)
        return {"evidence": self._evidence(train, stored, current), "source": source or {}, "cache_status": "network"}

    def resolve_media(
        self,
        train: str,
        media_kind: str,
        selector: str = "default",
        psw: Any = None,
    ) -> Dict[str, Any]:
        layout = self.get_layout(train, psw=psw)
        car_code = str(layout["evidence"]["carCode"])
        selector = str(selector or "default")
        cached = self.store.get_coach_media_asset(car_code, media_kind, selector)
        if cached and Path(cached["local_path"]).is_file():
            _emit(psw, AgentState.MEDIA_CACHE_HIT, f"coach media hit {car_code}:{media_kind}:{selector}")
            return self._attachment(cached, train, car_code, layout["cache_status"])

        locator = self.store.get_coach_media_locator(car_code, media_kind, selector)
        if not locator:
            raise RailGoContractError(f"coach image target is unavailable: {media_kind}/{selector}")
        url = str(locator["remote_url"])
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_MEDIA_HOSTS:
            _emit(psw, AgentState.MEDIA_REJECTED, "coach image URL rejected by allowlist")
            raise RailGoContractError("coach image URL is not an approved RailGo asset")

        response = http_get(url, timeout=20, min_interval=0.3, headers=RAILGO_HEADERS)
        if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
            raise RailGoTemporaryError(f"coach image temporary HTTP {response.status_code}")
        if response.status_code != 200:
            raise RailGoContractError(f"coach image HTTP {response.status_code}")
        payload = response.content
        if not payload or len(payload) > _MAX_MEDIA_BYTES:
            _emit(psw, AgentState.MEDIA_REJECTED, "coach image size rejected")
            raise RailGoContractError("coach image is empty or too large")
        mime = str(response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
        if mime not in {"image/png", "image/jpeg"}:
            _emit(psw, AgentState.MEDIA_REJECTED, f"coach image MIME rejected: {mime}")
            raise RailGoContractError("coach image has an unsupported MIME type")
        width, height = _image_dimensions(payload, mime)
        if width > _MAX_DIMENSION or height > _MAX_DIMENSION or width * height > _MAX_PIXELS:
            _emit(psw, AgentState.MEDIA_REJECTED, "coach image dimensions rejected")
            raise RailGoContractError("coach image dimensions exceed safety limits")

        digest = hashlib.sha256(payload).hexdigest()
        suffix = ".png" if mime == "image/png" else ".jpg"
        path = self.media_root.joinpath(f"{digest}{suffix}")
        if not path.exists():
            path.write_bytes(payload)
        record = {
            "car_code": car_code,
            "media_kind": media_kind,
            "selector": selector,
            "content_hash": digest,
            "mime_type": mime,
            "local_path": str(path),
        }
        self.store.save_coach_media_asset(record)
        _emit(psw, AgentState.MEDIA_CACHE_WRITE, f"coach media stored {digest[:12]}")
        return self._attachment(record, train, car_code, layout["cache_status"])

    @staticmethod
    def _attachment(record: Dict[str, Any], train: str, car_code: str, cache_status: str) -> Dict[str, Any]:
        return {
            "type": "coach_image",
            "asset_id": record["content_hash"],
            "mime_type": record["mime_type"],
            "caption": f"{train} · {car_code} · {record['media_kind']} {record['selector']}",
            "source": "RailGo",
            "cache_status": cache_status,
        }


coach_asset_service = CoachAssetService()


__all__ = ["CoachAssetService", "coach_asset_service", "parse_coach_payload"]
