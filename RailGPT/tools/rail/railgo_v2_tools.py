"""Bounded RailGo V2 operational tools.

Live station operations remain ephemeral. Coach structures and route geometry
are delegated to dedicated local asset stores with their own validity rules.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict

from agent.psw import AgentState
from tools.rail.coach_assets import coach_asset_service
from tools.rail.operational_cache import beijing_now, railgo_operational_cache
from tools.rail.railgo_client import (
    fetch_coach_pic_v2,
    fetch_map_line_v2,
    fetch_station_big_screen_v2,
    fetch_train_delay_all_v2,
    fetch_train_station_access_v2,
    normalize_railgo_date,
)
from tools.rail.route_assets import route_asset_service
from tools.rail.station_dict import station_dict


RAILGO_V2_TOOL_OBJECTS = {
    "train_delay",
    "train_station_access",
    "station_board",
    "coach_layout",
    "train_route_map",
}

_TRAIN_RE = re.compile(r"^[A-Z]{1,3}\d+$")
_KIND_ALIASES = {
    "arrival": "arrival",
    "arrive": "arrival",
    "到达": "arrival",
    "进站": "arrival",
    "departure": "departure",
    "depart": "departure",
    "出发": "departure",
    "发车": "departure",
}


def _normalize_train(value: str) -> str:
    train = str(value or "").strip().upper().replace("次", "")
    if not _TRAIN_RE.fullmatch(train):
        raise ValueError(f"invalid train number: {value!r}")
    return train


def _normalize_station(value: str) -> str:
    station = str(value or "").strip()
    if len(station) == 3 and station.isascii() and station.isalpha():
        return station.upper()

    clean = station[:-1] if station.endswith("站") else station
    telecode = station_dict.telecode_of(clean.strip())
    if not telecode:
        raise ValueError(f"unknown station: {value!r}")
    return str(telecode).upper()


def _normalize_kind(value: str | None, default: str = "departure") -> str:
    key = str(value or default).strip().lower()
    kind = _KIND_ALIASES.get(key)
    if not kind:
        raise ValueError("kind must be arrival or departure")
    return kind


def _parts(value: str) -> list[str]:
    normalized = str(value or "").replace("｜", "|").replace("；", "|")
    return [item.strip() for item in normalized.split("|") if item.strip()]


def _pretty_lines(title: str, data: Any) -> str:
    return "\n".join(
        [
            title,
            json.dumps(data, ensure_ascii=False, indent=2),
        ]
    )


def _fetch_live(psw: Any, obj: str, fetcher):
    if psw:
        psw.set_state(AgentState.QUERYING, f"RailGo v2 network refresh: {obj}")
    return fetcher()


def query_railgo_v2_tool(
    obj: str,
    query_id: str,
    *,
    date: str | None = None,
    psw: Any = None,
) -> Dict[str, Any]:
    """Execute one non-overlapping RailGo V2 capability."""

    obj = str(obj or "").strip()
    if obj not in RAILGO_V2_TOOL_OBJECTS:
        raise ValueError(f"unsupported RailGo V2 tool: {obj}")

    if psw:
        psw.set_state(AgentState.DISPATCH, f"RailGo v2 dispatch -> {obj} ({query_id})")

    result_id = str(query_id or "").strip()
    result_date = date
    operational_result = None

    if obj == "train_delay":
        train = _normalize_train(query_id)
        operational_result = railgo_operational_cache.get_or_fetch(
            obj,
            train,
            lambda: _fetch_live(psw, obj, lambda: fetch_train_delay_all_v2(train)),
            service_date=beijing_now().strftime("%Y-%m-%d"),
            psw=psw,
        )
        payload = operational_result["payload"]
        data = list(payload["data"])[:80]
        title = f"LIVE TRAIN DELAY: {train}"
        result_id = train
    elif obj == "train_station_access":
        pieces = _parts(query_id)
        if len(pieces) < 3:
            raise ValueError("train_station_access id must be TRAIN|STATION|KIND")
        train = _normalize_train(pieces[0])
        station = _normalize_station(pieces[1])
        kind = _normalize_kind(pieces[2] if len(pieces) > 2 else None)
        normalized_date = normalize_railgo_date(
            str(date or beijing_now().strftime("%Y-%m-%d"))
        )
        result_date = datetime.strptime(normalized_date, "%Y%m%d").strftime("%Y-%m-%d")
        cache_key = f"{train}|{station}|{normalized_date}|{kind}"
        operational_result = railgo_operational_cache.get_or_fetch(
            obj,
            cache_key,
            lambda: _fetch_live(
                psw,
                obj,
                lambda: fetch_train_station_access_v2(train, station, result_date, kind),
            ),
            service_date=result_date,
            psw=psw,
        )
        payload = operational_result["payload"]
        data = payload["data"]
        title = f"LIVE STATION ACCESS: {train} at {station} ({kind})"
        result_id = f"{train}|{station}|{kind}"
    elif obj == "station_board":
        pieces = _parts(query_id)
        if len(pieces) < 2:
            raise ValueError("station_board id must be STATION|KIND")
        station = _normalize_station(pieces[0] if pieces else "")
        kind = _normalize_kind(pieces[1])
        cache_key = f"{station}|{kind}"
        operational_result = railgo_operational_cache.get_or_fetch(
            obj,
            cache_key,
            lambda: _fetch_live(
                psw,
                obj,
                lambda: fetch_station_big_screen_v2(station, kind),
            ),
            service_date=beijing_now().strftime("%Y-%m-%d"),
            psw=psw,
        )
        payload = operational_result["payload"]
        data = list(payload["data"])[:40]
        title = f"LIVE STATION BOARD: {station} ({kind})"
        result_id = f"{station}|{kind}"
        station_name = station_dict.name_of(station) or pieces[0]
    elif obj == "coach_layout":
        train = _normalize_train(query_id)
        coach_asset_service.fetcher = fetch_coach_pic_v2
        asset_result = coach_asset_service.get_layout(train, psw=psw)
        data = dict(asset_result["evidence"])
        media_catalog = list(data.pop("mediaCatalog", []) or [])
        payload = {"_railgo": asset_result.get("source") or {}}
        title = f"PUBLISHED COACH LAYOUT: {train}"
        result_id = train
    else:
        train = _normalize_train(query_id)
        route_asset_service.fetcher = fetch_map_line_v2
        asset_result = route_asset_service.get_route(train, psw=psw)
        data = asset_result["evidence"]
        payload = {"_railgo": asset_result.get("source") or {}}
        title = f"TRAIN ROUTE MAP: {train}"
        result_id = train

    if psw:
        psw.set_state(AgentState.RENDERING, f"format RailGo v2 evidence: {obj}")

    result = {
        "domain": "railway",
        "object": obj,
        "id": result_id,
        "payload": None,
        "evidence": data,
        "source": payload.get("_railgo", {}),
        "pretty": _pretty_lines(title, data),
        "note": "Capability-specific evidence with a local freshness certificate",
    }
    if operational_result is not None:
        result["cache_status"] = operational_result["cache_status"]
        result["freshness"] = {
            "fetched_at": operational_result["fetched_at"],
            "expires_at": operational_result["expires_at"],
            "age_seconds": operational_result["age_seconds"],
        }
    if obj == "station_board":
        result["grounded_slots"] = {
            "station": station_name,
            "direction": kind,
        }
    if obj in {"coach_layout", "train_route_map"}:
        result["cache_status"] = asset_result.get("cache_status")
        result["artifacts"] = list(asset_result.get("artifacts") or [])
    if obj == "coach_layout":
        result["_media_catalog"] = media_catalog
    if result_date:
        result["date"] = result_date
    return result


__all__ = ["RAILGO_V2_TOOL_OBJECTS", "query_railgo_v2_tool"]
