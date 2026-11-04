"""RailGo V1/V2 compatibility client.

RailGo V2 and V1 use different hosts and response envelopes.  This module
keeps those transport details out of the railway tools and normalizes V2
train-main data to the legacy internal shape consumed by RailStore.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

import requests

from tools.rail.http_client import RAILGO_HEADERS, http_get
from tools.rail.railgo_bridge import bridge_enabled, query_railgo_host
from utils.net_retry import RetryExhausted, truncated_binary_backoff


RAILGO_V2_BASE_URL = "https://rg-api.zenglingkun.cn/api/v2"
RAILGO_V1_BASE_URL = "https://data.railgo.zenglingkun.cn/api"
RAILGO_ATTRIBUTION_URL = "https://railgo.dev"
RAILGO_TIMEOUT_SECONDS = 20
RAILGO_RETRY_ATTEMPTS = 5
RAILGO_TRANSIENT_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class RailGoError(RuntimeError):
    """Base error for RailGo transport or response-contract failures."""


class RailGoNotFoundError(RailGoError):
    """The requested train does not exist or does not run on that date."""


class RailGoTemporaryError(RailGoError):
    """A transient RailGo/network failure for which V1 fallback is safe."""


class RailGoContractError(RailGoError):
    """RailGo returned a response that does not match its documented schema."""


def _bridge_call(method: str, **params: Any) -> Any:
    """Call the in-process RailGo query service when the host exposes it."""

    if not bridge_enabled():
        return None
    try:
        return query_railgo_host(method, **params)
    except Exception as exc:
        raise RailGoTemporaryError(f"RailGo host bridge failed for {method}: {exc}") from exc


def _key_insensitive(value: Any) -> Any:
    """Normalize the host's PascalCase JSON without changing nested values."""

    if isinstance(value, list):
        return [_key_insensitive(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized = {}
    for key, item in value.items():
        text = str(key)
        normalized[text[:1].lower() + text[1:] if text else text] = _key_insensitive(item)
    return normalized


def normalize_railgo_date(value: str | None) -> str:
    """Return a RailGo date as YYYYMMDD and reject ambiguous input."""

    raw = str(value or "").strip()
    if not raw:
        return datetime.now().strftime("%Y%m%d")

    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y%m%d")
        except ValueError:
            continue

    raise ValueError(f"Invalid RailGo date: {value!r}")


def railgo_source(api_version: str, endpoint: str, **extra: Any) -> Dict[str, Any]:
    """Build source metadata without mixing it into railway facts."""

    metadata: Dict[str, Any] = {
        "provider": "RailGo",
        "api_version": api_version,
        "endpoint": endpoint,
        "url": RAILGO_ATTRIBUTION_URL,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }
    metadata.update({key: value for key, value in extra.items() if value is not None})
    return metadata


def _response_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()[:300]

    if isinstance(payload, dict):
        return str(payload.get("msg") or payload.get("message") or payload.get("error") or payload)[:300]
    return str(payload)[:300]


def _get_json(
    url: str,
    *,
    params: Dict[str, Any] | None = None,
    timeout: int = RAILGO_TIMEOUT_SECONDS,
) -> Any:
    """GET with bounded transient retry; provider/date fallback stays upstream."""

    def request_once() -> Any:
        response = http_get(
            url,
            params=params,
            timeout=timeout,
            min_interval=0.3,
            headers=RAILGO_HEADERS,
        )

        if response.status_code in {400, 404}:
            raise RailGoNotFoundError(
                _response_message(response) or f"HTTP {response.status_code}"
            )
        if response.status_code in RAILGO_TRANSIENT_STATUSES:
            raise requests.HTTPError(
                f"RailGo temporary HTTP {response.status_code}: {_response_message(response)}",
                response=response,
            )
        if response.status_code >= 400:
            raise RailGoError(
                f"RailGo HTTP {response.status_code}: {_response_message(response)}"
            )

        try:
            return response.json()
        except ValueError as exc:
            raise RailGoContractError("RailGo returned non-JSON content") from exc

    try:
        return truncated_binary_backoff(
            request_once,
            max_attempts=RAILGO_RETRY_ATTEMPTS,
            retry_on_status=RAILGO_TRANSIENT_STATUSES,
            context="RailGo request",
        )
    except RetryExhausted as exc:
        raise RailGoTemporaryError(f"RailGo request exhausted retries: {exc}") from exc


def _normalize_train_main(data: Dict[str, Any], train_no: str, query_date: str) -> Dict[str, Any]:
    payload = dict(data)

    number_full = payload.get("numberFull") or [train_no]
    if isinstance(number_full, str):
        number_full = [number_full]
    payload["numberFull"] = [str(item).strip().upper() for item in number_full if str(item).strip()]

    payload["bureauName"] = (
        payload.get("bureauName")
        or payload.get("bureauShortName")
        or payload.get("bureau")
        or ""
    )
    payload["rundays"] = [
        normalize_railgo_date(day)
        for day in (payload.get("rundays") or payload.get("runways") or [])
    ]
    payload["timetable"] = [
        dict(stop) for stop in (payload.get("timetable") or []) if isinstance(stop, dict)
    ]
    payload["_railgo"] = railgo_source(
        "v2",
        "/api/v2/getTrainMain",
        query_date=query_date,
        train_no=train_no,
    )
    return payload


def fetch_train_main_v2(train_no: str, query_date: str) -> Dict[str, Any]:
    """Fetch exact-date train facts from RailGo V2."""

    train_no = str(train_no or "").strip().upper()
    query_date = normalize_railgo_date(query_date)
    raw = _get_json(
        f"{RAILGO_V2_BASE_URL}/getTrainMain",
        params={"trainNum": train_no, "date": query_date},
    )

    if not isinstance(raw, dict):
        raise RailGoContractError("RailGo V2 train-main response is not an object")
    if raw.get("success") is not True:
        raise RailGoContractError(str(raw.get("msg") or "RailGo V2 request was not successful"))

    data = raw.get("data")
    if not isinstance(data, dict) or not data:
        raise RailGoContractError("RailGo V2 train-main response has no data object")
    return _normalize_train_main(data, train_no, query_date)


def _fetch_v2_data(
    endpoint: str,
    *,
    params: Dict[str, Any],
    expected_types: tuple[type, ...] = (dict, list),
) -> Dict[str, Any]:
    """Return a normalized V2 evidence envelope for dynamic tools."""

    raw = _get_json(f"{RAILGO_V2_BASE_URL}/{endpoint}", params=params)
    if not isinstance(raw, dict):
        raise RailGoContractError(f"RailGo V2 {endpoint} response is not an object")

    if raw.get("success") is not True:
        message = str(raw.get("msg") or raw.get("message") or "request failed")
        lowered = message.lower()
        if any(token in lowered for token in ("not found", "不存在", "未找到", "无此")):
            raise RailGoNotFoundError(message)
        raise RailGoContractError(f"RailGo V2 {endpoint}: {message}")

    data = raw.get("data")
    if not isinstance(data, expected_types):
        names = "/".join(item.__name__ for item in expected_types)
        raise RailGoContractError(f"RailGo V2 {endpoint} data is not {names}")

    return {
        "data": data,
        "_railgo": railgo_source("v2", f"/api/v2/{endpoint}", **params),
    }


def fetch_train_station_access_v2(
    train_no: str,
    station_telecode: str,
    query_date: str,
    kind: str,
) -> Dict[str, Any]:
    """Fetch platform/check-gate/exit information for a train at one station."""

    params = {
        "trainNum": str(train_no or "").strip().upper(),
        "stationTelecode": str(station_telecode or "").strip().upper(),
        "date": normalize_railgo_date(query_date),
        "kind": str(kind or "departure").strip().lower(),
    }
    return _fetch_v2_data("getExit", params=params)


def fetch_train_delay_all_v2(train_no: str) -> Dict[str, Any]:
    """Fetch current delay status for all reported stations of a train."""

    return _fetch_v2_data(
        "getTrainDelayAll",
        params={"trainNum": str(train_no or "").strip().upper()},
        expected_types=(list,),
    )


def fetch_station_big_screen_v2(station_telecode: str, kind: str) -> Dict[str, Any]:
    """Fetch the live arrival or departure board for a station."""

    return _fetch_v2_data(
        "getStationBigScreen",
        params={
            "stationTelecode": str(station_telecode or "").strip().upper(),
            "kind": str(kind or "departure").strip().lower(),
        },
        expected_types=(list,),
    )


def fetch_coach_pic_v2(train_no: str) -> Dict[str, Any]:
    """Fetch published coach composition, capacity and image information."""

    return _fetch_v2_data(
        "getCoachPic",
        params={"train": str(train_no or "").strip().upper()},
    )


def fetch_map_line_v2(train_no: str) -> Dict[str, Any]:
    """Fetch GCJ-02 route coordinates for a train."""

    return _fetch_v2_data(
        "mapLine",
        params={"train": str(train_no or "").strip().upper()},
        expected_types=(list, dict),
    )


def fetch_train_path_v1(train_no: str) -> Dict[str, Any]:
    """Fetch the legacy cached train profile for temporary V2 fallback."""

    train_no = str(train_no or "").strip().upper()
    raw = _get_json(
        f"{RAILGO_V1_BASE_URL}/train/query",
        params={"train": train_no},
    )
    if not isinstance(raw, dict) or not raw:
        raise RailGoContractError("RailGo V1 train response is empty or invalid")

    payload = dict(raw)
    payload["_railgo"] = railgo_source("v1", "/api/train/query", train_no=train_no)
    return payload


def fetch_s2s_v1(dep: str, arr: str, query_date: str) -> List[Dict[str, Any]]:
    """Fetch station-to-station services from the still-supported V1 API."""

    raw = _get_json(
        f"{RAILGO_V1_BASE_URL}/train/sts_query",
        params={"from": dep, "to": arr, "date": normalize_railgo_date(query_date)},
    )
    if not isinstance(raw, list):
        raise RailGoContractError("RailGo V1 station-to-station response is not a list")
    if any(not isinstance(item, dict) for item in raw):
        raise RailGoContractError("RailGo V1 station-to-station list contains invalid items")
    return raw


def fetch_station_v1(telecode: str) -> Dict[str, Any]:
    """Fetch station metadata from the still-supported V1 API."""

    telecode = str(telecode or "").strip().upper()
    raw = _get_json(
        f"{RAILGO_V1_BASE_URL}/station/query",
        params={"telecode": telecode},
    )
    if not isinstance(raw, dict) or not isinstance(raw.get("data"), dict):
        raise RailGoContractError("RailGo V1 station response has no data object")

    payload = dict(raw)
    payload["_railgo"] = railgo_source("v1", "/api/station/query", telecode=telecode)
    return payload


def _v1_list_payload(raw: Any, endpoint: str) -> List[Any]:
    """Accept both the documented list and a compatible {data: [...]} envelope."""

    data = raw.get("data") if isinstance(raw, dict) else raw
    if not isinstance(data, list):
        raise RailGoContractError(f"RailGo V1 {endpoint} response is not a list")
    return data


def fetch_station_preselect_v1(keyword: str) -> Dict[str, Any]:
    """Fetch fuzzy station-name candidates for an explicit user keyword."""

    keyword = str(keyword or "").strip()
    if not keyword:
        raise ValueError("station preselect keyword is required")
    host_data = _bridge_call("station.search", keyword=keyword)
    if host_data is not None:
        data = _key_insensitive(host_data)
        if not isinstance(data, list):
            raise RailGoContractError("RailGo host station preselect response is not a list")
        return {
            "data": data,
            "_railgo": railgo_source("host", "station.search", keyword=keyword),
        }
    raw = _get_json(
        f"{RAILGO_V1_BASE_URL}/station/preselect",
        params={"keyword": keyword},
    )
    data = _v1_list_payload(raw, "station/preselect")
    if any(not isinstance(item, dict) for item in data):
        raise RailGoContractError("RailGo V1 station preselect contains an invalid item")
    return {
        "data": data,
        "_railgo": railgo_source("v1", "/api/station/preselect", keyword=keyword),
    }


def fetch_train_preselect_v1(keyword: str) -> Dict[str, Any]:
    """Fetch fuzzy train-number candidates for an explicit user keyword."""

    keyword = str(keyword or "").strip().upper()
    if not keyword:
        raise ValueError("train preselect keyword is required")
    host_data = _bridge_call("train.search", keyword=keyword)
    if host_data is not None:
        data = _key_insensitive(host_data)
        if not isinstance(data, list):
            raise RailGoContractError("RailGo host train preselect response is not a list")
        return {
            "data": [str(item) for item in data],
            "_railgo": railgo_source("host", "train.search", keyword=keyword),
        }
    raw = _get_json(
        f"{RAILGO_V1_BASE_URL}/train/preselect",
        params={"keyword": keyword},
    )
    data = _v1_list_payload(raw, "train/preselect")
    if any(not isinstance(item, str) for item in data):
        raise RailGoContractError("RailGo V1 train preselect contains an invalid item")
    return {
        "data": data,
        "_railgo": railgo_source("v1", "/api/train/preselect", keyword=keyword),
    }


def fetch_random_train_v1() -> Dict[str, Any]:
    """Fetch one random train from the maintained V1 catalogue."""

    raw = _get_json(f"{RAILGO_V1_BASE_URL}/lucky")
    data = raw.get("data") if isinstance(raw, dict) and isinstance(raw.get("data"), dict) else raw
    if not isinstance(data, dict) or not str(data.get("number") or "").strip():
        raise RailGoContractError("RailGo V1 random train response has no train number")
    return {
        "data": data,
        "_railgo": railgo_source("v1", "/api/lucky"),
    }


__all__ = [
    "RAILGO_V1_BASE_URL",
    "RAILGO_V2_BASE_URL",
    "RAILGO_RETRY_ATTEMPTS",
    "RAILGO_ATTRIBUTION_URL",
    "RailGoContractError",
    "RailGoError",
    "RailGoNotFoundError",
    "RailGoTemporaryError",
    "fetch_coach_pic_v2",
    "fetch_map_line_v2",
    "fetch_random_train_v1",
    "fetch_s2s_v1",
    "fetch_station_big_screen_v2",
    "fetch_station_preselect_v1",
    "fetch_station_v1",
    "fetch_train_delay_all_v2",
    "fetch_train_main_v2",
    "fetch_train_path_v1",
    "fetch_train_preselect_v1",
    "fetch_train_station_access_v2",
    "normalize_railgo_date",
    "railgo_source",
]
