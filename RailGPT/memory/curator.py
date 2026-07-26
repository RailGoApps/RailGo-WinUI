from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional

from memory.entity_parser import (
    extract_entities_from_tasklike_items,
    extract_entities_from_text,
    has_substantive_entities,
    merge_entities,
)
from memory.packets import MemoryPacket, utcish_now
from memory.profile_index import is_explicit_preference_statement


def _stable_id(prefix: str, payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _first_text(*values: Any, limit: int = 600) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text[:limit]
    return ""


def _memory_safe_tool_fact(value: Any) -> Any:
    """Remove presentation assets and locators before packet persistence."""

    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if key in {"artifacts", "attachments", "mediaCatalog", "_media_catalog"}:
                continue
            if lowered in {
                "url",
                "pictureurl",
                "picurl",
                "remote_url",
                "local_path",
                "geojson",
                "fallback_svg",
                "source",
                "source_json",
                "_railgo",
                "provider",
                "api_version",
                "endpoint",
            }:
                continue
            cleaned[key] = _memory_safe_tool_fact(item)
        return cleaned
    if isinstance(value, list):
        return [_memory_safe_tool_fact(item) for item in value]
    if isinstance(value, str) and "\n" in value:
        provenance_prefixes = ("source:", "🔎 source", "📌 data source:")
        return "\n".join(
            line
            for line in value.splitlines()
            if not line.strip().lower().startswith(provenance_prefixes)
        )
    return value


class MemoryCuratorAgent:
    """
    Local curator for RailGPT memory.

    It deliberately separates user claims, assistant statements, tool plans and
    tool facts so downstream recall can decide what is safe to use as a hard
    executable anchor.
    """

    def curate_turn(
        self,
        *,
        session=None,
        user_text: str = "",
        ai_text: str = "",
        tasks: Optional[List[Dict[str, Any]]] = None,
        facts: Optional[Dict[str, Any]] = None,
    ) -> List[MemoryPacket]:
        now = utcish_now()
        packets: List[MemoryPacket] = []
        session_id = getattr(session, "session_id", None)

        user_entities = extract_entities_from_text(user_text)
        explicit_preference = is_explicit_preference_statement(user_text)
        if has_substantive_entities(user_entities) or explicit_preference:
            user_tags = ["hard_anchor_allowed", "profile_candidate"]
            if explicit_preference:
                user_tags.append("explicit_preference")
            packets.append(
                MemoryPacket(
                    id=_stable_id("user", {"session": session_id, "text": user_text, "at": now}),
                    scope="dialogue",
                    kind="preference" if explicit_preference else "user_claim",
                    source="explicit_user",
                    text=user_text,
                    summary_l0=str(user_text or "").strip()[:180],
                    overview_l1=str(user_text or "").strip()[:900],
                    entities=user_entities,
                    confidence=0.97 if explicit_preference else 0.92,
                    provenance={"session_id": session_id, "role": "user"},
                    tags=user_tags,
                    created_at=now,
                    last_seen=now,
                )
            )

        task_packets = self._curate_task_packets(tasks or [], session_id=session_id, now=now)
        packets.extend(task_packets)

        fact_packets = self._curate_fact_packets(facts or {}, session_id=session_id, now=now)
        packets.extend(fact_packets)

        assistant_entities = extract_entities_from_text(ai_text)
        if has_substantive_entities(assistant_entities):
            packets.append(
                MemoryPacket(
                    id=_stable_id("assistant", {"session": session_id, "text": ai_text, "at": now}),
                    scope="dialogue",
                    kind="assistant_statement",
                    source="assistant_statement",
                    text=ai_text,
                    summary_l0=str(ai_text or "").strip()[:180],
                    overview_l1=str(ai_text or "").strip()[:900],
                    entities=assistant_entities,
                    confidence=0.35,
                    provenance={"session_id": session_id, "role": "assistant"},
                    tags=["soft_only", "no_hard_anchor"],
                    created_at=now,
                    last_seen=now,
                )
            )

        if session and getattr(session, "followup_mode", False):
            context = dict(getattr(session, "followup_slots", {}) or {})
            packets.append(
                MemoryPacket(
                    id=_stable_id("followup", {"session": session_id, "context": context, "at": now}),
                    scope="working",
                    kind="clarification_contract",
                    source="followup_contract",
                    text=str(getattr(session, "followup_question", "") or ""),
                    summary_l0="Active clarification contract",
                    overview_l1=json.dumps(context, ensure_ascii=False, sort_keys=True),
                    entities=merge_entities(context.get("context", {}) if isinstance(context.get("context"), dict) else {}),
                    slots=context,
                    confidence=0.88,
                    provenance={"session_id": session_id},
                    tags=["hard_anchor_allowed", "working_memory"],
                    created_at=now,
                    last_seen=now,
                )
            )

        return packets

    def _curate_task_packets(self, tasks: Iterable[Dict[str, Any]], *, session_id: Any, now: str) -> List[MemoryPacket]:
        packets: List[MemoryPacket] = []
        for index, task in enumerate(tasks or []):
            if not isinstance(task, dict):
                continue
            params = task.get("params") if isinstance(task.get("params"), dict) else task
            entities = extract_entities_from_tasklike_items([task])
            if not has_substantive_entities(entities):
                continue
            obj = str(params.get("object") or "").strip()
            obj_id = str(params.get("id") or "").strip()
            date = str(params.get("date") or "").strip()
            text = _first_text(params.get("message"), params.get("pretty"), f"{obj}:{obj_id}:{date}")
            packets.append(
                MemoryPacket(
                    id=_stable_id("plan", {"session": session_id, "index": index, "task": task}),
                    scope="working",
                    kind="tool_plan",
                    source="tool_plan",
                    text=text,
                    summary_l0=text[:180],
                    overview_l1=json.dumps(task, ensure_ascii=False, sort_keys=True)[:1200],
                    entities=entities,
                    slots={"object": obj, "id": obj_id, "date": date},
                    confidence=0.78,
                    provenance={"session_id": session_id, "task_index": index},
                    tags=["working_memory"],
                    created_at=now,
                    last_seen=now,
                )
            )
        return packets

    def _curate_fact_packets(self, facts: Dict[str, Any], *, session_id: Any, now: str) -> List[MemoryPacket]:
        packets: List[MemoryPacket] = []
        queries = facts.get("queries", []) if isinstance(facts, dict) else []
        for index, query in enumerate(queries or []):
            if not isinstance(query, dict):
                continue
            entities = extract_entities_from_tasklike_items([query])
            if not has_substantive_entities(entities):
                continue
            obj = str(query.get("object") or "").strip()
            obj_id = str(query.get("id") or "").strip()
            date = str(query.get("date") or "").strip()
            text = _memory_safe_tool_fact(
                _first_text(
                    query.get("pretty"),
                    query.get("summary"),
                    query.get("content"),
                    f"{obj}:{obj_id}:{date}",
                )
            )
            safe_query = _memory_safe_tool_fact(query)
            packets.append(
                MemoryPacket(
                    id=_stable_id("fact", {"session": session_id, "index": index, "query": safe_query}),
                    scope="tool_evidence",
                    kind="tool_fact",
                    source="tool_fact",
                    text=text,
                    summary_l0=text[:180],
                    overview_l1=json.dumps(safe_query, ensure_ascii=False, sort_keys=True)[:2000],
                    entities=entities,
                    slots={"object": obj, "id": obj_id, "date": date},
                    confidence=0.96,
                    provenance={"session_id": session_id, "query_index": index, "domain": query.get("domain", "railway")},
                    tags=["hard_anchor_allowed", "tool_evidence"],
                    created_at=now,
                    last_seen=now,
                )
            )
        return packets
