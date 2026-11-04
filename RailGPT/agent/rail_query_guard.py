from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from tools.rail.station_dict import station_dict


ROUTE_LEADING_PATTERNS = (
    r"^(今天|今日|明天|后天|昨天|请问|帮我|帮忙|麻烦|查一下|查查|看看|查询|我想查|我想看|想查一下|想看一下)",
    r"^\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?",
    r"^\d{8}",
    r"^\d{1,2}月\d{1,2}日?",
)

ROUTE_TRAILING_TOKENS = (
    "还有票吗",
    "有票吗",
    "余票",
    "票吗",
    "票",
    "最快怎么坐",
    "最快怎么走",
    "最快的车",
    "最快的一班",
    "最快的一趟",
    "最快那趟",
    "最快那班",
    "最快的",
    "最快",
    "最早的",
    "最晚的",
    "最便宜的",
    "最合适的",
    "标杆车",
    "标杆的",
    "标杆",
    "最强的",
    "最强",
    "中转方案",
    "中转",
    "怎么坐最快",
    "怎么走最快",
    "怎么坐",
    "怎么走",
    "怎么去",
    "推荐最快",
    "有什么车",
    "有哪些车",
    "有啥车",
    "有什么高铁",
    "有哪些高铁",
    "有啥高铁",
    "有什么车次",
    "有哪些车次",
    "有啥车次",
    "有什么",
    "有哪些",
    "有啥",
    "还有什么",
    "推荐",
    "行程",
    "方案",
    "经停表",
    "经停",
    "停站",
    "路径",
    "路线",
    "列车",
    "车次",
    "高铁",
    "动车",
    "火车",
    "一班",
    "一趟",
    "一下",
    "那个",
    "这趟",
    "那趟",
    "吗",
    "呢",
    "呀",
    "吧",
    "啊",
    "的",
)

ROUTE_SEPARATORS = ("->", "=>", "→", "➡", "⇒", "➜", "➝", "⟶", "⟹", "－", "–", "—", "至")


VALID_ROUTE_LEADING_PATTERNS = (
    r"^(今天|今日|明天|后天|昨天|请问|帮我|帮忙|麻烦|查一下|查看|看看|查询|我想查|我想看|想查一下|想看一下)",
    r"^\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?",
    r"^\d{8}",
    r"^\d{1,2}月\d{1,2}日?",
)

VALID_ROUTE_TRAILING_TOKENS = (
    "还有票吗",
    "有票吗",
    "余票",
    "票吗",
    "最快怎么坐",
    "最快怎么走",
    "最快的车次",
    "最快的列车",
    "最快的车",
    "最快的一班",
    "最快那个",
    "最快的",
    "最快",
    "最早的",
    "最晚的",
    "最便宜的",
    "最合适的",
    "标杆列车",
    "标杆车",
    "标杆的",
    "标杆",
    "最强的",
    "最强",
    "中转方案",
    "中转",
    "怎么坐最快",
    "怎么走最快",
    "怎么坐",
    "怎么走",
    "怎么去",
    "推荐最快",
    "有什么车次",
    "有哪些车次",
    "有什么列车",
    "有哪些列车",
    "有什么车",
    "有哪些车",
    "有什么高铁",
    "有哪些高铁",
    "有什么动车",
    "有哪些动车",
    "有什么",
    "有哪些",
    "推荐一下",
    "推荐",
    "行程",
    "方案",
    "经停表",
    "经停",
    "停站",
    "路径",
    "路线",
    "列车",
    "车次",
    "高铁",
    "动车",
    "火车",
    "一班",
    "一个",
    "那个",
    "这趟",
    "那趟",
    "吗",
    "么",
    "呢",
    "的",
)

VALID_ROUTE_SEPARATORS = (
    "开往",
    "前往",
    "去往",
    "→",
    "➡",
    "⟶",
    "至",
    "到",
)

CALENDAR_PREFIX_PATTERNS = (
    r"^(?:今天|今日|明天|后天|昨天|今晚|今早|今晨)(?:的)?",
    r"^(?:这周|本周|下周|这星期|本星期|下星期|这礼拜|本礼拜|下礼拜)(?:末|[一二三四五六日天])(?:的)?",
    r"^(?:周|星期|礼拜)(?:末|[一二三四五六日天])(?:的)?",
)


@lru_cache(maxsize=1)
def _station_names() -> tuple[str, ...]:
    names = {
        str(info.get("name") or "").strip()
        for info in getattr(station_dict, "data", {}).values()
        if isinstance(info, dict) and str(info.get("name") or "").strip()
    }
    return tuple(sorted(names, key=len, reverse=True))


def clean_station_token(token: Any, is_departure: bool = False) -> str:
    value = str(token or "").strip(" ，。？！?：:()（）")
    if not value:
        return ""

    for pattern in VALID_ROUTE_LEADING_PATTERNS + ROUTE_LEADING_PATTERNS:
        value = re.sub(pattern, "", value)

    for pattern in CALENDAR_PREFIX_PATTERNS:
        value = re.sub(pattern, "", value)

    if is_departure:
        value = re.sub(r"^(从|由)", "", value)
    else:
        value = re.sub(r"^(去|到|至|往|开往|前往|去往)", "", value)

    changed = True
    while changed and value:
        changed = False
        for suffix in VALID_ROUTE_TRAILING_TOKENS + ROUTE_TRAILING_TOKENS:
            if value.endswith(suffix):
                value = value[: -len(suffix)].strip(" ，。？！?：:()（）")
                changed = True
                break

    return value.strip(" ，。？！?：:()（）")


def resolve_station_name(token: Any, is_departure: bool = False) -> str | None:
    value = clean_station_token(token, is_departure=is_departure)
    if not value:
        return None

    direct = _resolve_exact_station_name(value)
    if direct:
        return direct

    without_station_suffix = value[:-1] if value.endswith("站") else value
    direct = _resolve_exact_station_name(without_station_suffix)
    if direct:
        return direct

    for candidate in _station_names():
        if value.startswith(candidate):
            return candidate

    for candidate in _station_names():
        if value.endswith(candidate):
            return candidate

    for candidate in _station_names():
        if candidate in value:
            return candidate

    return None


def normalize_route_id(route_id: Any) -> str | None:
    raw = str(route_id or "").strip()
    if not raw:
        return None

    raw = re.sub(r"(开往|前往|去往|到|至)", "-", raw, count=1)

    for separator in VALID_ROUTE_SEPARATORS + ROUTE_SEPARATORS:
        raw = raw.replace(separator, "-")

    raw = re.sub(r"[-~～—－]+", "-", raw)
    if "-" not in raw:
        return None

    dep_raw, arr_raw = raw.split("-", 1)
    dep = resolve_station_name(dep_raw, is_departure=True)
    arr = resolve_station_name(arr_raw, is_departure=False)

    if not dep or not arr or dep == arr:
        return None

    return f"{dep}-{arr}"


def normalize_route_with_optional_via(query_id: Any) -> str | None:
    raw = str(query_id or "").strip().replace("｜", "|")
    if not raw:
        return None

    parts = [part.strip() for part in raw.split("|")]
    route = normalize_route_id(parts[0]) if parts else None
    if not route:
        return None

    if len(parts) == 1:
        return route

    via = resolve_station_name(parts[1], is_departure=False)
    if not via:
        return None

    return f"{route}|{via}"


def _resolve_exact_station_name(value: str) -> str | None:
    if not value:
        return None

    if len(value) == 3 and value.isascii() and value.isalpha():
        info = station_dict.lookup(value.upper())
        if info and info.get("name"):
            return str(info["name"])
        return None

    telecode = station_dict.telecode_of(value)
    if telecode:
        info = station_dict.lookup(telecode)
        if info and info.get("name"):
            return str(info["name"])

    return None
