from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Dict, Iterable, List

from agent.rail_query_guard import normalize_route_id, normalize_route_with_optional_via
from tools.rail.station_dict import station_dict


TRAIN_PATTERN = re.compile(r"\b[GDKTZC]\d{1,5}\b", re.IGNORECASE)
EMU_PATTERN = re.compile(r"\b(?:CRH|CR|CJ)\d+[A-Z-]+\d+\b", re.IGNORECASE)
DATE_PATTERN = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
DATE_COMPACT_PATTERN = re.compile(r"(?<!\d)\d{8}(?!\d)")

ROUTE_OBJECTS = {
    "left_ticket_s2s",
    "transfer_12306",
    "station_to_station_mini",
    "station_to_station_detail",
    "station_to_station_future",
    "station_to_station_past",
    "s2s_benchmark",
    "s2s_timeband_dep",
    "s2s_timeband_arr",
    "s2s_regular_only",
    "s2s_temporary_only",
    "s2s_bureau_filter",
}

ROUTE_SEPARATORS = ("->", "=>", "→", "到", "至", "开往", "前往", "去往", "-")
ROUTE_FALLBACK_HINTS = (
    "从",
    "到",
    "至",
    "开往",
    "前往",
    "去往",
    "出发",
    "到达",
    "余票",
    "有票",
    "最快",
    "标杆",
    "中转",
    "直达",
    "车次",
    "列车",
    "经停",
    "停站",
    "时刻",
    "路线",
    "线路",
    "路径",
    "怎么走",
)


def unique_list(values: Iterable[str]) -> List[str]:
    seen = set()
    items: List[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        items.append(value)
    return items


def has_substantive_entities(entities: Dict[str, Any]) -> bool:
    if not isinstance(entities, dict):
        return False
    for key in ("trains", "emus", "routes", "dates", "stations", "objects"):
        values = entities.get(key, [])
        if isinstance(values, list) and any(str(item or "").strip() for item in values):
            return True
        if isinstance(values, str) and values.strip():
            return True
    return False


@lru_cache(maxsize=1)
def station_names() -> List[str]:
    names = {
        str(item.get("name") or "").strip()
        for item in getattr(station_dict, "data", {}).values()
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    return sorted((name for name in names if len(name) >= 2), key=lambda item: (-len(item), item))


def _trim_station_token(token: str) -> str:
    value = str(token or "").strip()
    value = re.sub(r"^(今天|今日|明天|后天|昨天|这周[末一二三四五六日天]?|本周[末一二三四五六日天]?|下周[末一二三四五六日天]?|周[末一二三四五六日天]?|星期[一二三四五六日天]?|礼拜[一二三四五六日天]?|从)", "", value)
    value = re.sub(r"(还有余票吗|余票|有票吗|最快标杆车|最快的车|最快的|标杆车|标杆|线路|运行线路|经停|停站|时刻表|查询|查一下|看看|还有吗)$", "", value)
    return value.strip(" ，。！？、:：()（）")


def _resolve_station_name(token: str) -> str | None:
    value = _trim_station_token(token)
    if not value:
        return None
    for candidate in station_names():
        if value == candidate:
            return candidate
    for candidate in station_names():
        if value.startswith(candidate) or value.endswith(candidate) or candidate in value:
            return candidate
    return None


def _extract_station_mentions(text: str) -> List[str]:
    value = str(text or "")
    if not value:
        return []

    matches = []
    for name in station_names():
        start = value.find(name)
        if start < 0:
            continue
        matches.append((start, start + len(name), name))

    matches.sort(key=lambda item: (item[0], -(item[1] - item[0]), item[2]))

    selected: List[tuple[int, int, str]] = []
    for start, end, name in matches:
        overlaps = any(not (end <= kept_start or start >= kept_end) for kept_start, kept_end, _ in selected)
        if overlaps:
            continue
        selected.append((start, end, name))

    selected.sort(key=lambda item: item[0])
    return [name for _, _, name in selected]


def extract_route_from_text(text: str) -> str | None:
    value = str(text or "").strip()
    if not value:
        return None

    normalized = normalize_route_id(value)
    if normalized:
        return normalized

    normalized_via = normalize_route_with_optional_via(value)
    if normalized_via:
        return normalized_via.split("|", 1)[0]

    for separator in ROUTE_SEPARATORS:
        if separator not in value:
            continue
        dep_raw, arr_raw = value.split(separator, 1)
        if re.search(r"\d{1,2}:\d{2}", dep_raw) or re.search(r"\d{1,2}:\d{2}", arr_raw):
            continue
        dep = _resolve_station_name(dep_raw)
        arr = _resolve_station_name(arr_raw)
        if dep and arr and dep != arr:
            return f"{dep}-{arr}"

    station_mentions = _extract_station_mentions(value)
    if len(station_mentions) >= 2 and any(hint in value for hint in ROUTE_FALLBACK_HINTS):
        return f"{station_mentions[0]}-{station_mentions[1]}"
    return None


def extract_text_tokens(text: str) -> List[str]:
    value = str(text or "")
    tokens = re.findall(r"[A-Za-z0-9_:-]{2,}", value)
    chinese_segments = re.findall(r"[\u4e00-\u9fff]{2,}", value)

    for segment in chinese_segments:
        if len(segment) <= 4:
            tokens.append(segment)
            continue
        for index in range(len(segment) - 1):
            tokens.append(segment[index:index + 2])

    return unique_list(token.lower() for token in tokens)


def extract_entities_from_text(text: str) -> Dict[str, List[str]]:
    value = str(text or "")
    trains = unique_list(item.upper() for item in TRAIN_PATTERN.findall(value))
    emus = unique_list(item.upper().replace("-", "") for item in EMU_PATTERN.findall(value))

    dates = unique_list(DATE_PATTERN.findall(value))
    for raw in DATE_COMPACT_PATTERN.findall(value):
        dates.append(f"{raw[:4]}-{raw[4:6]}-{raw[6:]}")
    dates = unique_list(dates)

    route = extract_route_from_text(value)
    routes = [route] if route else []
    if route and "-" in route:
        dep, arr = route.split("-", 1)
        stations = [dep, arr]
    else:
        stations = _extract_station_mentions(value)

    query_hints = []
    keyword_map = (
        ("left_ticket_s2s", ("余票", "有票", "票吗", "票务")),
        ("transfer_12306", ("中转",)),
        ("path_detail", ("经停", "停站", "时刻表", "线路", "路径")),
        ("s2s_benchmark", ("标杆", "最快", "最强")),
        ("telecode", ("电报码",)),
        ("train", ("车底", "担当", "车组")),
        ("emu", ("车组号", "动车组编号", "交路", "最近跑什么")),
    )
    for query_object, keywords in keyword_map:
        if any(keyword in value for keyword in keywords):
            query_hints.append(query_object)

    return {
        "trains": trains,
        "emus": emus,
        "routes": routes,
        "dates": dates,
        "stations": unique_list(stations),
        "objects": unique_list(query_hints),
        "tokens": extract_text_tokens(value),
    }


def merge_entities(*sources: Dict[str, Any]) -> Dict[str, List[str]]:
    merged = {
        "trains": [],
        "emus": [],
        "routes": [],
        "dates": [],
        "stations": [],
        "objects": [],
        "tokens": [],
    }

    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in merged:
            values = source.get(key, [])
            if isinstance(values, list):
                merged[key].extend(str(item) for item in values if str(item or "").strip())
            elif isinstance(values, str) and values.strip():
                merged[key].append(values.strip())

    return {key: unique_list(values) for key, values in merged.items()}


def _extract_route_from_object(obj: str, obj_id: str) -> str | None:
    if obj == "transfer_12306":
        normalized = normalize_route_with_optional_via(obj_id)
        if not normalized:
            return None
        return normalized.split("|", 1)[0]

    if obj in ROUTE_OBJECTS:
        return extract_route_from_text(obj_id)

    return None


def extract_entities_from_tasklike_items(items: Iterable[Dict[str, Any]]) -> Dict[str, List[str]]:
    trains: List[str] = []
    emus: List[str] = []
    routes: List[str] = []
    dates: List[str] = []
    stations: List[str] = []
    objects: List[str] = []
    tokens: List[str] = []

    for item in items or []:
        if not isinstance(item, dict):
            continue

        params = item.get("params") if isinstance(item.get("params"), dict) else item
        obj = str(params.get("object") or "").strip()
        obj_id = str(params.get("id") or "").strip()
        date = str(params.get("date") or "").strip()

        if obj:
            objects.append(obj)
        if date and DATE_PATTERN.fullmatch(date):
            dates.append(date)

        if obj_id:
            trains.extend(candidate.upper() for candidate in TRAIN_PATTERN.findall(obj_id))
            emus.extend(candidate.upper().replace("-", "") for candidate in EMU_PATTERN.findall(obj_id))
            route = _extract_route_from_object(obj, obj_id)
            if route:
                routes.append(route)
                if "-" in route:
                    dep, arr = route.split("-", 1)
                    stations.extend([dep, arr])
            tokens.extend(extract_text_tokens(obj_id))

        for extra_key in ("pretty", "summary", "content", "message"):
            extra_value = params.get(extra_key)
            if isinstance(extra_value, str) and extra_value.strip():
                extracted = extract_entities_from_text(extra_value)
                trains.extend(extracted["trains"])
                emus.extend(extracted["emus"])
                routes.extend(extracted["routes"])
                dates.extend(extracted["dates"])
                stations.extend(extracted["stations"])
                objects.extend(extracted["objects"])
                tokens.extend(extracted["tokens"])

    return {
        "trains": unique_list(trains),
        "emus": unique_list(emus),
        "routes": unique_list(routes),
        "dates": unique_list(dates),
        "stations": unique_list(stations),
        "objects": unique_list(objects),
        "tokens": unique_list(tokens),
    }


def best_entity_value(entities: Dict[str, List[str]], key: str) -> str | None:
    values = entities.get(key) or []
    if not values:
        return None
    return str(values[0])
