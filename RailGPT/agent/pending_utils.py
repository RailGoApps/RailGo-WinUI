from __future__ import annotations

from typing import Any, Dict, Iterable, List


GENERIC_PENDING_FRAGMENTS = (
    "请补充一下",
    "请补充",
    "补充一下信息",
    "补充一下关键信息",
    "补充一个关键信息",
    "我还需要你补充",
    "还需要你补充",
    "还差一个关键信息",
    "才能继续",
    "才能继续查",
    "才能继续查询",
)


def normalize_pending_slots(slot: Iterable[Any] | None) -> List[str]:
    normalized: List[str] = []
    for item in slot or []:
        value = str(item or "").strip()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def is_generic_pending_question(question: str | None) -> bool:
    value = str(question or "").strip()
    if not value:
        return True
    return any(fragment in value for fragment in GENERIC_PENDING_FRAGMENTS)


def _pick_context_value(context: Dict[str, Any], *keys: str) -> str:
    if not isinstance(context, dict):
        return ""

    for key in keys:
        value = str(context.get(key) or "").strip()
        if value:
            return value
    return ""


def _resolve_route_context(context: Dict[str, Any]) -> tuple[str, str, str]:
    route = _pick_context_value(context, "route")
    dep = _pick_context_value(context, "dep")
    arr = _pick_context_value(context, "arr")

    if route and "-" in route:
        route_dep, route_arr = route.split("-", 1)
        dep = dep or route_dep
        arr = arr or route_arr

    return route, dep, arr


def compose_pending_question(
    slot: Iterable[Any] | None = None,
    context: Dict[str, Any] | None = None,
    fallback: str | None = None,
) -> str:
    slots = normalize_pending_slots(slot)
    context = context or {}
    slot_set = set(slots)
    route, dep, arr = _resolve_route_context(context)
    train_no = _pick_context_value(context, "train_no", "train")
    station_name = _pick_context_value(context, "station", "station_name")
    date = _pick_context_value(context, "date")

    slot_contract = context.get("missing_slot_contract")
    if isinstance(slot_contract, dict):
        questions = [
            item
            for item in slot_contract.get("questions", [])
            if isinstance(item, dict) and str(item.get("slot") or "").strip() in slot_set
        ]
        fallback_questions = [
            str(item.get("fallback_question") or "").strip()
            for item in questions
            if str(item.get("fallback_question") or "").strip()
        ]
        if fallback_questions:
            return " ".join(fallback_questions)

    if "hub" in slot_set or "via" in slot_set:
        if dep and arr:
            return f"如果你还是想查{dep}到{arr}的中转方案，告诉我想经哪个中转站或城市，我就继续帮你看。"
        return "如果你想查中转方案，告诉我想经哪个中转站或城市，我就继续帮你看。"

    if "emu_id" in slot_set:
        return "如果你是想查具体动车组，告诉我完整编组号吧，例如 `CR400AFZ2333`，我就继续帮你看。"

    if "train_no" in slot_set or "train" in slot_set:
        if station_name:
            return f"如果你是想确认{station_name}这个站是否停靠，再告诉我具体车次号，比如 `G87`，我就继续帮你看。"
        return "把具体车次号告诉我吧，比如 `G87` 或 `D2216`，我就继续帮你查。"

    if {"dep", "arr"}.issubset(slot_set) or "route" in slot_set:
        if dep and not arr:
            return f"如果你是从{dep}出发，再告诉我要到哪一站，我就继续帮你查。"
        if arr and not dep:
            return f"如果你是要去{arr}，再告诉我从哪里出发，我就继续帮你查。"
        return "把出发站和到达站告诉我吧，我就能继续帮你查车。"

    if "dep" in slot_set:
        if arr:
            return f"如果你是要去{arr}，再告诉我从哪里出发，我就继续帮你查。"
        return "再告诉我从哪里出发，我就继续帮你查。"

    if "arr" in slot_set:
        if dep:
            return f"如果你是从{dep}出发，再告诉我要到哪一站，我就继续帮你查。"
        return "再告诉我要到哪一站，我就继续帮你查。"

    if "station_name" in slot_set:
        if train_no:
            return f"如果你是想看{train_no}这趟车停不停，再告诉我想核验哪个车站，我就继续帮你看。"
        return "把具体车站名告诉我吧，我就继续帮你查。"

    if "date" in slot_set:
        if route:
            return f"如果你想查{route}某一天的结果，直接告诉我日期就行；不特别说明的话，我先按今天看。"
        if train_no:
            return f"如果你想看{train_no}在特定日期的情况，告诉我日期就行；不特别说明的话，我先按今天理解。"
        if date:
            return f"如果你不是查{date}这一天，而是想看别的日期，直接把日期告诉我就行。"
        return "如果你想查特定日期，直接把日期告诉我就行；不特别说明的话，我先按今天看。"

    failed_query = context.get("failed_query")
    if isinstance(failed_query, dict):
        obj = str(failed_query.get("object") or "").strip()
        if obj == "transfer_12306":
            return "我这边还能继续查中转方案，不过还需要你把出发站、到达站和中转站再说清楚一点。"
        if obj == "left_ticket_s2s":
            if dep and not arr:
                return f"如果你是从{dep}出发，再告诉我要到哪一站，我就继续帮你看余票。"
            if arr and not dep:
                return f"如果你是要去{arr}，再告诉我从哪里出发，我就继续帮你看余票。"
            return "余票这边还差一个完整区间，你把出发站和到达站告诉我，我就继续帮你看。"
        if obj.startswith("station_to_station") or obj.startswith("s2s_"):
            if dep and not arr:
                return f"如果你是从{dep}出发，再告诉我要到哪一站，我就继续帮你查车次。"
            if arr and not dep:
                return f"如果你是要去{arr}，再告诉我从哪里出发，我就继续帮你查车次。"
            return "我这边还差一个完整区间，你把出发站和到达站告诉我，我就继续帮你查车次。"

    if fallback and str(fallback).strip() and not is_generic_pending_question(fallback):
        return str(fallback).strip()

    return "你把还缺的那个关键信息再告诉我一句就行，比如出发站、到达站或车次号，我就继续帮你查。"


def normalize_pending_payload(
    question: str | None,
    slot: Iterable[Any] | None = None,
    context: Dict[str, Any] | None = None,
    fallback: str | None = None,
) -> Dict[str, Any]:
    context = context if isinstance(context, dict) else {}
    slots = normalize_pending_slots(slot)
    normalized_question = str(question or "").strip()
    if is_generic_pending_question(normalized_question):
        normalized_question = compose_pending_question(
            slot=slots,
            context=context,
            fallback=fallback,
        )
    return {
        "question": normalized_question,
        "slot": slots,
        "context": context,
    }
