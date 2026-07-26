from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from agent.capabilities import capability_catalog, context_fingerprint
from memory.entity_parser import (
    best_entity_value,
    extract_entities_from_tasklike_items,
    extract_entities_from_text,
    has_substantive_entities,
    merge_entities,
)

ANCHOR_PRIORITY = {
    "restore_assistant": 5,
    "assistant": 5,
    "restore_user": 30,
    "memory_state": 60,
    "tasks": 80,
    "facts": 90,
    "user": 100,
    "manual": 110,
}


class SessionMemory:
    def __init__(self):
        self.messages: List[Dict[str, str]] = []
        self.session_id: Optional[int] = None
        self.title = "New Chat"
        self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M")

        self._reset_anchors()

        self.recent_entities: List[Dict[str, Any]] = []
        self.memory_recall: Dict[str, Any] = {
            "session": [],
            "episodic": [],
            "long_term": [],
            "anchor_candidates": {},
            "memory_context_package": {},
        }
        self.context_agent_state: Dict[str, Any] = {}

        self.followup_mode = False
        self.followup_question = None
        self.followup_slots = {}

    def _sanitize_recent_entities(self, entries: Iterable[Dict[str, Any]], limit: int = 20) -> List[Dict[str, Any]]:
        sanitized: List[Dict[str, Any]] = []
        for item in entries or []:
            if not isinstance(item, dict):
                continue
            entities = merge_entities(item.get("entities", {}))
            if not has_substantive_entities(entities):
                continue
            sanitized.append(
                {
                    "source": str(item.get("source") or "memory_state"),
                    "timestamp": str(item.get("timestamp") or ""),
                    "entities": entities,
                }
            )
        return sanitized[-limit:]

    def set_session_id(self, session_id: Optional[int]):
        self.session_id = session_id

    def add_user_message(self, text: str):
        if not text:
            return
        self.messages.append({"role": "user", "content": text})
        self._remember_text(text, source="user")
        self._trim()

    def add_ai_message(self, text: str):
        if not text:
            return
        self.messages.append({"role": "assistant", "content": text})
        self._remember_text(text, source="assistant")
        self._trim()

    def _trim(self, max_len: int = 120):
        if len(self.messages) > max_len:
            self.messages = self.messages[-max_len:]
        if len(self.recent_entities) > 80:
            self.recent_entities = self.recent_entities[-80:]

    def get_recent_messages(self, n: int = 8):
        return self.messages[-n:]

    def _context_budget(self, mode: str | None = None) -> Dict[str, int]:
        normalized = str(mode or "").strip().lower()
        if normalized in {"fast", "fastgo"}:
            normalized = "fast-go"
        if normalized == "fastplus":
            normalized = "fast-plus"

        if normalized == "fast-go":
            return {"max_messages": 8, "max_chars": 6000}
        if normalized == "fast-plus":
            return {"max_messages": 24, "max_chars": 12000}
        return {"max_messages": 80, "max_chars": 24000}

    def build_llm_history(
        self,
        mode: str | None = None,
        max_messages: Optional[int] = None,
        max_chars: Optional[int] = None,
        exclude_current_user: bool = True,
        latest_user_text: str = "",
    ) -> List[Dict[str, str]]:
        budget = self._context_budget(mode)
        limit_messages = int(max_messages or budget["max_messages"])
        limit_chars = int(max_chars or budget["max_chars"])
        latest_user_text = str(latest_user_text or "").strip()

        source_messages: List[Dict[str, str]] = []
        for message in self.messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "").strip()
            content = str(message.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            source_messages.append({"role": role, "content": content})

        if exclude_current_user and source_messages:
            last = source_messages[-1]
            if last.get("role") == "user" and (not latest_user_text or last.get("content") == latest_user_text):
                source_messages = source_messages[:-1]

        picked: List[Dict[str, str]] = []
        total_chars = 0
        for message in reversed(source_messages):
            content = message["content"]
            projected = total_chars + len(content)
            if picked and (len(picked) >= limit_messages or projected > limit_chars):
                break
            if projected > limit_chars:
                content = content[-max(256, limit_chars - total_chars):]
            picked.append({"role": message["role"], "content": content})
            total_chars += len(content)
            if len(picked) >= limit_messages or total_chars >= limit_chars:
                break

        picked.reverse()
        return picked

    def build_agent_context_package(
        self,
        mode: str | None = None,
        user_text: str = "",
        date_resolution: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        history = self.build_llm_history(
            mode=mode,
            exclude_current_user=True,
            latest_user_text=user_text,
        )
        last_assistant_message = ""
        for message in reversed(history):
            if str(message.get("role") or "").strip() == "assistant":
                last_assistant_message = str(message.get("content") or "").strip()
                break
        dialogue_excerpt = "\n".join(
            f"{str(item.get('role') or '').strip()}: {str(item.get('content') or '').strip()}"
            for item in history[-4:]
            if str(item.get("role") or "").strip() in {"user", "assistant"}
            and str(item.get("content") or "").strip()
        )
        has_recent_substantive_answer = bool(
            last_assistant_message
            and (
                len(last_assistant_message) >= 48
                or len(str(extract_entities_from_text(last_assistant_message).get("tokens") or [])) > 0
            )
        )
        memory_package = {}
        if isinstance(self.memory_recall, dict):
            memory_package = self.memory_recall.get("memory_context_package", {}) or {}

        followup_contract = {}
        if self.in_followup():
            followup_contract = {
                "question": self.followup_question,
                "slots": self.followup_slots,
                "context_text": self.get_followup_context(),
            }

        package = {
            "schema_version": 1,
            "mode": str(mode or ""),
            "latest_user_text": str(user_text or ""),
            "dialogue_history": history,
            "dialogue_history_count": len(history),
            "dialogue_excerpt": dialogue_excerpt,
            "last_assistant_message": last_assistant_message,
            "has_recent_substantive_answer": has_recent_substantive_answer,
            "memory_context_package": memory_package,
            "followup_contract": followup_contract,
            "explicit_entities": extract_entities_from_text(user_text),
            "date_resolution": date_resolution or {},
            "working_anchors": self.get_anchor_snapshot(),
            "tool_contract_summary": capability_catalog(level="l0"),
        }
        package["context_fingerprint"] = context_fingerprint(
            {
                "mode": package["mode"],
                "latest_user_text": package["latest_user_text"],
                "dialogue_history": history,
                "followup_contract": followup_contract,
                "working_anchors": package["working_anchors"],
                "hard_anchors": memory_package.get("hard_anchors", {}) if isinstance(memory_package, dict) else {},
                "profile_index": [
                    str(item.get("id") or "")
                    for item in (memory_package.get("profile_index", []) if isinstance(memory_package, dict) else [])[:6]
                    if isinstance(item, dict)
                ],
            }
        )
        return package

    def build_agent_context_view(
        self,
        role: str,
        mode: str | None = None,
        user_text: str = "",
        date_resolution: Optional[Dict[str, Any]] = None,
        include_dialogue: bool = False,
    ) -> Dict[str, Any]:
        """Return one role-scoped context payload without duplicating chat history."""

        package = self.build_agent_context_package(
            mode=mode,
            user_text=user_text,
            date_resolution=date_resolution,
        )
        role = str(role or "router").strip().lower()
        scoped = dict(package)
        # Capability selection has its own registry prompt. Repeating the whole
        # catalog in every role view wastes context and can blur role boundaries.
        scoped.pop("tool_contract_summary", None)

        if not include_dialogue:
            scoped.pop("dialogue_history", None)
            scoped.pop("dialogue_excerpt", None)
            scoped.pop("last_assistant_message", None)

        memory_package = package.get("memory_context_package", {})
        if isinstance(memory_package, dict):
            if role == "router":
                scoped["memory_context_package"] = {
                    "schema_version": memory_package.get("schema_version", 2),
                    "hard_anchors": dict(memory_package.get("hard_anchors") or {}),
                    "profile_index": list(memory_package.get("profile_index") or [])[:4],
                }
            elif role == "context_resolver":
                safe_soft_context = [
                    item
                    for item in (memory_package.get("soft_context") or [])
                    if isinstance(item, dict) and item.get("source") != "legacy_profile_hint"
                ]
                scoped["memory_context_package"] = {
                    "schema_version": memory_package.get("schema_version", 2),
                    "hard_anchors": dict(memory_package.get("hard_anchors") or {}),
                    "soft_context": safe_soft_context[:3],
                }
            elif role == "answer":
                scoped["memory_context_package"] = {
                    "schema_version": memory_package.get("schema_version", 2),
                    "hard_anchors": dict(memory_package.get("hard_anchors") or {}),
                    "profile_index": list(memory_package.get("profile_index") or [])[:6],
                }
            elif role == "date":
                hard_anchors = dict(memory_package.get("hard_anchors") or {})
                scoped["memory_context_package"] = {
                    "schema_version": memory_package.get("schema_version", 2),
                    "hard_anchors": {"date": hard_anchors["date"]} if hard_anchors.get("date") else {},
                }
                working_date = str((scoped.get("working_anchors") or {}).get("date") or "").strip()
                scoped["working_anchors"] = {"date": working_date} if working_date else {}

        return scoped

    def format_agent_context_package(
        self,
        mode: str | None = None,
        user_text: str = "",
        date_resolution: Optional[Dict[str, Any]] = None,
    ) -> str:
        return json.dumps(
            self.build_agent_context_package(
                mode=mode,
                user_text=user_text,
                date_resolution=date_resolution,
            ),
            ensure_ascii=False,
            indent=2,
        )

    def get_recent_entity_pool(self, max_items: int = 8) -> Dict[str, List[str]]:
        pool = {
            "trains": [],
            "emus": [],
            "routes": [],
            "dates": [],
            "stations": [],
            "objects": [],
            "tokens": [],
        }

        for item in self.recent_entities[-max_items:]:
            if not isinstance(item, dict):
                continue
            entities = item.get("entities", {})
            if not isinstance(entities, dict):
                continue
            for key in pool:
                values = entities.get(key, [])
                if isinstance(values, list):
                    pool[key].extend(str(value) for value in values if str(value or "").strip())

        return {key: list(dict.fromkeys(values)) for key, values in pool.items() if values}

    def _reset_anchors(self):
        self.last_train: Optional[str] = None
        self.last_emu: Optional[str] = None
        self.last_query_type: Optional[str] = None
        self.last_route: Optional[str] = None
        self.last_dep: Optional[str] = None
        self.last_arr: Optional[str] = None
        self.last_date: Optional[str] = None
        self.last_object: Optional[str] = None
        self._anchor_priority = {
            "train": -1,
            "emu": -1,
            "query_type": -1,
            "route": -1,
            "dep": -1,
            "arr": -1,
            "date": -1,
            "query_object": -1,
        }
        self._anchor_source = {
            "train": "",
            "emu": "",
            "query_type": "",
            "route": "",
            "dep": "",
            "arr": "",
            "date": "",
            "query_object": "",
        }

    def _set_anchor_value(self, key: str, value: Optional[str], source: str, force: bool = False):
        normalized = str(value or "").strip()
        if not normalized:
            return

        priority = ANCHOR_PRIORITY.get(source, 0)
        if not force and priority < self._anchor_priority.get(key, -1):
            return

        self._anchor_priority[key] = priority
        self._anchor_source[key] = source
        setattr(
            self,
            {
                "train": "last_train",
                "emu": "last_emu",
                "query_type": "last_query_type",
                "route": "last_route",
                "dep": "last_dep",
                "arr": "last_arr",
                "date": "last_date",
                "query_object": "last_object",
            }[key],
            normalized,
        )

    def update_anchor(
        self,
        *,
        train: str | None = None,
        emu: str | None = None,
        query_type: str | None = None,
        route: str | None = None,
        dep: str | None = None,
        arr: str | None = None,
        date: str | None = None,
        query_object: str | None = None,
        source: str = "manual",
        force: bool = False,
    ):
        self._set_anchor_value("train", train, source=source, force=force)
        self._set_anchor_value("emu", emu, source=source, force=force)
        self._set_anchor_value("query_type", query_type, source=source, force=force)
        if route:
            self._set_anchor_value("route", route, source=source, force=force)
            if "-" in route:
                route_dep, route_arr = route.split("-", 1)
                self._set_anchor_value("dep", route_dep, source=source, force=force)
                self._set_anchor_value("arr", route_arr, source=source, force=force)
        self._set_anchor_value("dep", dep, source=source, force=force)
        self._set_anchor_value("arr", arr, source=source, force=force)
        self._set_anchor_value("date", date, source=source, force=force)
        self._set_anchor_value("query_object", query_object, source=source, force=force)

    def update_from_tasks(self, tasks: Iterable[Dict[str, Any]]):
        entities = extract_entities_from_tasklike_items(tasks)
        self._remember_entities(entities, source="tasks")
        for task in tasks or []:
            if not isinstance(task, dict):
                continue
            params = task.get("params", {})
            if isinstance(params, dict):
                obj = str(params.get("object") or "").strip()
                if obj:
                    self.update_anchor(query_type=obj, query_object=obj, source="tasks")

    def update_from_facts(self, facts: Dict[str, Any]):
        if not isinstance(facts, dict):
            return

        query_entities = extract_entities_from_tasklike_items(facts.get("queries", []))
        self._remember_entities(query_entities, source="facts")

        for query in facts.get("queries", []):
            if not isinstance(query, dict):
                continue
            obj = str(query.get("object") or "").strip()
            if obj:
                self.update_anchor(query_type=obj, query_object=obj, source="facts")

    def _remember_text(self, text: str, source: str):
        entities = extract_entities_from_text(text)
        self._remember_entities(entities, source=source)

    def _remember_entities(self, entities: Dict[str, List[str]], source: str):
        merged = merge_entities(entities)
        if not has_substantive_entities(merged):
            return

        route = best_entity_value(merged, "routes")
        dep = None
        arr = None
        if route and "-" in route:
            dep, arr = route.split("-", 1)

        if source not in {"assistant", "restore_assistant"}:
            self.update_anchor(
                train=best_entity_value(merged, "trains"),
                emu=best_entity_value(merged, "emus"),
                route=route,
                dep=dep,
                arr=arr,
                date=best_entity_value(merged, "dates"),
                query_type=best_entity_value(merged, "objects"),
                query_object=best_entity_value(merged, "objects"),
                source=source,
            )

        self.recent_entities.append(
            {
                "source": source,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "entities": merged,
            }
        )
        self._trim()

    def get_anchor_snapshot(self) -> Dict[str, str]:
        snapshot = {
            "train": self.last_train or "",
            "emu": self.last_emu or "",
            "route": self.last_route or "",
            "dep": self.last_dep or "",
            "arr": self.last_arr or "",
            "date": self.last_date or "",
            "query_type": self.last_query_type or "",
            "query_object": self.last_object or "",
        }
        return {key: value for key, value in snapshot.items() if value}

    def set_memory_recall(self, recall_bundle: Optional[Dict[str, Any]]):
        if not isinstance(recall_bundle, dict):
            self.memory_recall = {
                "session": [],
                "episodic": [],
                "long_term": [],
                "anchor_candidates": {},
                "memory_context_package": {},
            }
            return
        self.memory_recall = recall_bundle

    def set_context_agent_state(self, context_state: Optional[Dict[str, Any]]):
        if not isinstance(context_state, dict):
            self.context_agent_state = {}
            return

        try:
            confidence_value = int(context_state.get("confidence") or 0)
        except Exception:
            confidence_value = 0

        self.context_agent_state = {
            "intent_category": str(context_state.get("intent_category") or "").strip(),
            "rewritten_user_text": str(context_state.get("rewritten_user_text") or "").strip(),
            "resolved_route": str(context_state.get("resolved_route") or "").strip(),
            "resolved_train_numbers": [
                str(item).strip()
                for item in (context_state.get("resolved_train_numbers") or [])
                if str(item or "").strip()
            ],
            "resolved_emu": str(context_state.get("resolved_emu") or "").strip(),
            "resolved_date": str(context_state.get("resolved_date") or "").strip(),
            "resolved_station_mentions": [
                str(item).strip()
                for item in (context_state.get("resolved_station_mentions") or [])
                if str(item or "").strip()
            ],
            "resolved_query_object": str(context_state.get("resolved_query_object") or "").strip(),
            "confidence": confidence_value,
            "reason": str(context_state.get("reason") or "").strip(),
        }

    def get_context_agent_state(self) -> Dict[str, Any]:
        return dict(self.context_agent_state or {})

    def has_usable_context_agent_state(self, min_confidence: int = 55) -> bool:
        state = self.get_context_agent_state()
        if not state:
            return False

        try:
            confidence_value = int(state.get("confidence") or 0)
        except Exception:
            confidence_value = 0

        if confidence_value < int(min_confidence):
            return False

        if str(state.get("intent_category") or "").strip().lower() == "unknown":
            return False

        return True

    def get_context_agent_context(self, min_confidence: int = 0) -> str:
        state = self.get_context_agent_state()
        if not state:
            return ""

        try:
            confidence_gate = int(min_confidence)
        except Exception:
            confidence_gate = 0
        if confidence_gate > 0 and not self.has_usable_context_agent_state(min_confidence=confidence_gate):
            return ""

        lines: List[str] = ["FAST context-agent intent shaping:"]
        intent_category = str(state.get("intent_category") or "").strip()
        if intent_category:
            lines.append(f"- intent_category={intent_category}")

        rewritten = str(state.get("rewritten_user_text") or "").strip()
        if rewritten:
            lines.append(f"- rewritten_user_text={rewritten}")

        resolved_route = str(state.get("resolved_route") or "").strip()
        if resolved_route:
            lines.append(f"- resolved_route={resolved_route}")

        resolved_trains = state.get("resolved_train_numbers") or []
        if resolved_trains:
            lines.append("- resolved_trains=" + ", ".join(resolved_trains[:6]))

        resolved_emu = str(state.get("resolved_emu") or "").strip()
        if resolved_emu:
            lines.append(f"- resolved_emu={resolved_emu}")

        resolved_date = str(state.get("resolved_date") or "").strip()
        if resolved_date:
            lines.append(f"- resolved_date={resolved_date}")

        resolved_query_object = str(state.get("resolved_query_object") or "").strip()
        if resolved_query_object:
            lines.append(f"- resolved_query_object={resolved_query_object}")

        reason = str(state.get("reason") or "").strip()
        if reason:
            lines.append(f"- reason={reason}")

        confidence = state.get("confidence")
        try:
            confidence_value = int(confidence)
        except Exception:
            confidence_value = 0
        if confidence_value > 0:
            lines.append(f"- confidence={confidence_value}")

        return "\n".join(lines)

    def resolve_anchor(self, key: str) -> Optional[str]:
        key = str(key or "").strip()
        if not key:
            return None

        direct_map = {
            "train": self.last_train,
            "emu": self.last_emu,
            "route": self.last_route,
            "dep": self.last_dep,
            "arr": self.last_arr,
            "date": self.last_date,
            "query_type": self.last_query_type,
            "query_object": self.last_object,
        }
        if direct_map.get(key):
            return direct_map[key]

        package = self.memory_recall.get("memory_context_package", {})
        if isinstance(package, dict):
            hard_anchors = package.get("hard_anchors", {})
            if isinstance(hard_anchors, dict):
                value = hard_anchors.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        anchor_candidates = self.memory_recall.get("anchor_candidates", {})
        value = anchor_candidates.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def get_memory_context(self, max_items: int = 3) -> str:
        lines: List[str] = []
        package = self.memory_recall.get("memory_context_package", {})
        if isinstance(package, dict) and package:
            hard_anchors = package.get("hard_anchors", {})
            if isinstance(hard_anchors, dict) and hard_anchors:
                anchor_line = ", ".join(f"{key}={value}" for key, value in hard_anchors.items() if value)
                if anchor_line:
                    lines.append("Memory OS hard anchors:")
                    lines.append(f"- {anchor_line}")

            soft_context = package.get("soft_context", [])
            if isinstance(soft_context, list) and soft_context:
                lines.append("Memory OS soft context:")
                for item in soft_context[:max_items]:
                    text = str(item.get("summary_l0") or item.get("text") or "").strip()
                    source = str(item.get("source") or item.get("kind") or "memory").strip()
                    if text:
                        lines.append(f"- [{source}] {text}")

            answer_context = package.get("answer_context", [])
            if isinstance(answer_context, list) and answer_context:
                lines.append("Memory OS answer context:")
                for item in answer_context[:max_items]:
                    text = str(item.get("summary_l0") or item.get("text") or "").strip()
                    source = str(item.get("source") or item.get("kind") or "memory").strip()
                    if text:
                        lines.append(f"- [{source}] {text}")

        anchors = self.get_anchor_snapshot()
        if anchors:
            anchor_line = ", ".join(f"{key}={value}" for key, value in anchors.items())
            lines.append("Active memory anchors:")
            lines.append(f"- {anchor_line}")

        recent_pool = self.get_recent_entity_pool(max_items=max_items + 2)
        if recent_pool.get("trains"):
            lines.append("Recent referenced trains:")
            lines.append("- " + ", ".join(recent_pool["trains"][:6]))
        if recent_pool.get("routes"):
            lines.append("Recent referenced routes:")
            lines.append("- " + ", ".join(recent_pool["routes"][:4]))
        if recent_pool.get("objects"):
            lines.append("Recent referenced query objects:")
            lines.append("- " + ", ".join(recent_pool["objects"][:6]))

        for section_key, label in (
            ("session", "Session recall"),
            ("episodic", "Same-conversation recall"),
            ("long_term", "Long-term recall"),
        ):
            items = self.memory_recall.get(section_key, [])
            if not isinstance(items, list) or not items:
                continue
            lines.append(f"{label}:")
            for item in items[:max_items]:
                text = str(item.get("text") or "").strip()
                if text:
                    lines.append(f"- {text}")

        return "\n".join(lines)

    def restore_from_messages(
        self,
        messages: Iterable[Dict[str, Any]],
        session_id: Optional[int] = None,
        memory_state: Optional[Dict[str, Any]] = None,
    ):
        if session_id is not None:
            self.session_id = session_id

        self.messages = []
        self.recent_entities = []
        self._reset_anchors()
        self.set_memory_recall(None)
        self.set_context_agent_state(None)

        for message in messages or []:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "").strip()
            content = str(message.get("content") or "")
            if role not in {"user", "assistant"} or not content:
                continue
            self.messages.append({"role": role, "content": content})
            self._remember_text(content, source=f"restore_{role}")

        self._trim()
        self.exit_followup()
        self.restore_state(memory_state)

    def export_state(self) -> Dict[str, Any]:
        return {
            "schema_version": 2,
            "anchors": self.get_anchor_snapshot(),
            "anchor_priority": dict(self._anchor_priority),
            "anchor_source": dict(self._anchor_source),
            "recent_entities": self._sanitize_recent_entities(self.recent_entities, limit=12),
            "context_agent_state": dict(self.context_agent_state),
            "followup_mode": self.followup_mode,
            "followup_question": self.followup_question,
            "followup_slots": dict(self.followup_slots),
        }

    def restore_state(self, state: Optional[Dict[str, Any]]):
        if not isinstance(state, dict):
            return

        anchors = state.get("anchors", {})
        if isinstance(anchors, dict):
            self.update_anchor(
                train=anchors.get("train"),
                emu=anchors.get("emu"),
                query_type=anchors.get("query_type"),
                route=anchors.get("route"),
                dep=anchors.get("dep"),
                arr=anchors.get("arr"),
                date=anchors.get("date"),
                query_object=anchors.get("query_object"),
                source="memory_state",
                force=True,
            )

        priority = state.get("anchor_priority", {})
        if isinstance(priority, dict):
            for key, value in priority.items():
                if key in self._anchor_priority:
                    try:
                        self._anchor_priority[key] = max(self._anchor_priority[key], int(value))
                    except Exception:
                        continue

        source = state.get("anchor_source", {})
        if isinstance(source, dict):
            for key, value in source.items():
                if key in self._anchor_source and str(value or "").strip():
                    self._anchor_source[key] = str(value).strip()

        recent_entities = state.get("recent_entities", [])
        if isinstance(recent_entities, list):
            self.recent_entities = self._sanitize_recent_entities(recent_entities, limit=20)

        self.set_context_agent_state(state.get("context_agent_state"))

        if state.get("followup_mode"):
            self.enter_followup(
                question=str(state.get("followup_question") or "").strip(),
                slot=(state.get("followup_slots") or {}).get("slot", []),
                context=(state.get("followup_slots") or {}).get("context", {}),
            )

    def enter_followup(
        self,
        question: str,
        slot: list = None,
        context: dict = None,
        slots: dict = None,
    ):
        if slots is not None:
            context = slots

        self.followup_mode = True
        self.followup_question = question
        self.followup_slots = {
            "slot": slot or [],
            "context": context or {},
        }

    def exit_followup(self):
        self.followup_mode = False
        self.followup_question = None
        self.followup_slots = {}

    def in_followup(self) -> bool:
        return self.followup_mode

    def get_followup_context(self) -> str:
        if not self.followup_mode:
            return ""

        slot = self.followup_slots.get("slot", [])
        context = self.followup_slots.get("context", {})
        memory_context = self.get_memory_context(max_items=2)

        lines = [
            "FOLLOW-UP MODE ACTIVE.",
            f"Previous question: {self.followup_question}",
            f"Missing slot list: {slot}",
            f"Context: {context}",
            "User reply should fill these slots directly.",
        ]
        if memory_context:
            lines.append(memory_context)
        return "\n".join(lines)
