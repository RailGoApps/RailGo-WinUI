from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional

from memory.curator import MemoryCuratorAgent
from memory.entity_parser import (
    best_entity_value,
    extract_entities_from_text,
    has_substantive_entities,
    merge_entities,
)
from memory.packets import MemoryContextPackage, MemoryPacket, normalize_confidence
from memory.profile_index import LongTermProfileIndex
from memory.stores import JsonlMemoryPacketStore


HARD_ANCHOR_SOURCES = {"explicit_user", "tool_fact", "followup_contract"}
CONTEXTUAL_REFERENCES = (
    "这",
    "那",
    "它",
    "刚才",
    "上面",
    "前面",
    "这些",
    "那几",
    "这几",
    "推荐的",
    "刚刚",
    "不是说",
    "不是问过",
)


def _derive_route_parts(anchors: Dict[str, str]) -> Dict[str, str]:
    result = dict(anchors)
    route = str(result.get("route") or "").strip()
    if route and "-" in route:
        dep, arr = route.split("-", 1)
        result.setdefault("dep", dep)
        result.setdefault("arr", arr)
    if result.get("query_object") and not result.get("query_type"):
        result["query_type"] = result["query_object"]
    return {key: value for key, value in result.items() if str(value or "").strip()}


def _looks_contextual(text: str) -> bool:
    value = str(text or "")
    return any(token in value for token in CONTEXTUAL_REFERENCES)


def _packet_view(packet: MemoryPacket, use_l1: bool = False) -> Dict[str, Any]:
    payload = packet.to_dict()
    return {
        "id": payload["id"],
        "scope": payload["scope"],
        "kind": payload["kind"],
        "source": payload["source"],
        "confidence": payload["confidence"],
        "summary_l0": payload["summary_l0"],
        "overview_l1": payload["overview_l1"] if use_l1 else "",
        "entities": payload["entities"],
        "slots": payload["slots"],
        "tags": payload["tags"],
    }


class MemoryOrchestrator:
    def __init__(self, root_dir: str):
        self.root_dir = root_dir
        self.v2_root_dir = os.path.join(self.root_dir, "v2")
        os.makedirs(self.v2_root_dir, exist_ok=True)
        self.curator = MemoryCuratorAgent()
        self.packet_store = JsonlMemoryPacketStore(self.v2_root_dir)
        self.profile_index = LongTermProfileIndex(
            self.v2_root_dir,
            packet_path=self.packet_store.path,
        )
        self._event_callback = None

    def set_event_callback(self, callback):
        self._event_callback = callback

    def _emit(self, state_name: str, detail: str):
        callback = self._event_callback
        if not callable(callback):
            return
        try:
            callback(state_name, detail)
        except Exception:
            pass

    def curate_turn(
        self,
        *,
        session=None,
        user_text: str = "",
        ai_text: str = "",
        tasks: Optional[List[Dict[str, Any]]] = None,
        facts: Optional[Dict[str, Any]] = None,
    ) -> List[MemoryPacket]:
        self._emit("MEMORY_CURATING", "memory curator extracting typed packets")
        packets = self.curator.curate_turn(
            session=session,
            user_text=user_text,
            ai_text=ai_text,
            tasks=tasks,
            facts=facts,
        )
        written = self.packet_store.append_packets(packets)
        self.profile_index.update(packets)
        self._emit("MEMORY_PACKET_READY", f"memory packets ready count={len(packets)} written={written}")
        return packets

    def build_context_package(
        self,
        *,
        session=None,
        user_text: str = "",
        session_items: Optional[List[Dict[str, Any]]] = None,
        episodic_items: Optional[List[Dict[str, Any]]] = None,
        long_term_items: Optional[List[Dict[str, Any]]] = None,
    ) -> MemoryContextPackage:
        self._emit("MEMORY_ARBITRATING", "memory arbiter classifying recall candidates")
        session_items = list(session_items or [])
        episodic_items = list(episodic_items or [])
        long_term_items = list(long_term_items or [])

        explicit_entities = extract_entities_from_text(user_text)
        profile_index = self.profile_index.retrieve(user_text=user_text, limit=6)
        hard_anchors = dict(session.get_anchor_snapshot()) if session else {}
        hard_anchors = _derive_route_parts(hard_anchors)

        packets: List[MemoryPacket] = []
        soft_context: List[Dict[str, Any]] = []
        answer_context: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        trace: List[Dict[str, Any]] = []

        for item in session_items:
            packet = self._legacy_packet(item, source="working_memory", kind="working_memory", scope="working")
            packets.append(packet)
            soft_context.append(_packet_view(packet))
            answer_context.append(_packet_view(packet, use_l1=True))
            trace.append({"packet_id": packet.id, "decision": "soft_context", "reason": "active session context"})

        contextual = _looks_contextual(user_text)
        for item in episodic_items:
            packet = self._legacy_packet(item, source="dialogue_memory", kind="legacy_episode", scope="episodic")
            packets.append(packet)
            answer_context.append(_packet_view(packet, use_l1=True))

            decision = "soft_context"
            reason = "same-conversation recall is useful background"
            if contextual:
                explicit_user_entities = extract_entities_from_text(str(item.get("user_text") or ""))
                promotion_packet = MemoryPacket(
                    id=f"{packet.id}:explicit-user",
                    scope="episodic",
                    kind="user_claim",
                    source="explicit_user",
                    text=str(item.get("user_text") or ""),
                    entities=explicit_user_entities,
                    confidence=0.9,
                    provenance={"derived_from": packet.id, "legacy": True},
                    tags=["legacy", "hard_anchor_allowed"],
                )
                promoted = self._promote_packet_anchors(
                    promotion_packet,
                    hard_anchors=hard_anchors,
                    explicit_entities=explicit_entities,
                    source_allowed=True,
                    min_confidence=0.85,
                )
                if promoted:
                    decision = "hard_anchor"
                    reason = "contextual reference resolved from same-conversation memory"
            soft_context.append(_packet_view(packet))
            trace.append({"packet_id": packet.id, "decision": decision, "reason": reason})

        for item in long_term_items:
            packet = self._legacy_packet(item, source="legacy_profile_hint", kind="legacy_profile_hint", scope="long_term")
            packets.append(packet)
            soft_context.append(_packet_view(packet))
            answer_context.append(_packet_view(packet, use_l1=False))
            rejected.append(
                {
                    "packet_id": packet.id,
                    "source": packet.source,
                    "reason": "long-term profile memory is soft-only and cannot become a hard route/date/train anchor",
                }
            )
            trace.append({"packet_id": packet.id, "decision": "soft_only", "reason": "long-term profile guard"})

        if rejected:
            self._emit("MEMORY_REJECTED", f"memory arbiter rejected {len(rejected)} hard-anchor candidates")

        return MemoryContextPackage(
            hard_anchors=_derive_route_parts(hard_anchors),
            profile_index=profile_index,
            soft_context=soft_context[:8],
            answer_context=answer_context[:8],
            packets=[_packet_view(packet, use_l1=False) for packet in packets[:12]],
            rejected=rejected[:12],
            trace=trace[:16],
        )

    def _legacy_packet(self, item: Dict[str, Any], *, source: str, kind: str, scope: str) -> MemoryPacket:
        payload = item if isinstance(item, dict) else {}
        memory_id = str(payload.get("memory_id") or payload.get("id") or f"{source}:{abs(hash(str(payload))) % 10_000_000}")
        text = str(payload.get("text") or payload.get("summary") or payload.get("value") or "").strip()
        entities = payload.get("entities") if isinstance(payload.get("entities"), dict) else {}
        if not has_substantive_entities(entities):
            field = str(payload.get("field") or "").strip()
            value = str(payload.get("value") or "").strip()
            entities = self._entities_from_field(field, value)
        if "confidence" in payload:
            confidence = normalize_confidence(payload.get("confidence"))
        else:
            try:
                legacy_score = float(payload.get("score", 6.0) or 6.0)
            except Exception:
                legacy_score = 6.0
            confidence = max(0.0, min(1.0, legacy_score / 10.0))
        return MemoryPacket(
            id=f"legacy:{memory_id}",
            scope=scope,
            kind=kind,
            source=source,
            text=text,
            summary_l0=text[:180],
            overview_l1=text[:900],
            entities=entities,
            confidence=confidence,
            provenance={"legacy": True, "memory_type": payload.get("memory_type", scope)},
            tags=["legacy", "soft_only"] if source == "legacy_profile_hint" else ["legacy"],
        )

    @staticmethod
    def _entities_from_field(field: str, value: str) -> Dict[str, List[str]]:
        if not field or not value:
            return {}
        if field == "route":
            stations = value.split("-", 1) if "-" in value else []
            return merge_entities({"routes": [value], "stations": stations})
        if field == "train":
            return merge_entities({"trains": [value]})
        if field == "emu":
            return merge_entities({"emus": [value]})
        if field == "station":
            return merge_entities({"stations": [value]})
        if field in {"object", "query_object", "query_type"}:
            return merge_entities({"objects": [value]})
        if field == "date":
            return merge_entities({"dates": [value]})
        return {}

    def _promote_packet_anchors(
        self,
        packet: MemoryPacket,
        *,
        hard_anchors: Dict[str, str],
        explicit_entities: Dict[str, List[str]],
        source_allowed: bool,
        min_confidence: float,
    ) -> bool:
        if not source_allowed or packet.source not in HARD_ANCHOR_SOURCES:
            return False
        if packet.confidence < min_confidence:
            return False

        promoted = False
        for entity_key, anchor_key in (
            ("trains", "train"),
            ("emus", "emu"),
            ("stations", "station"),
            ("routes", "route"),
            ("dates", "date"),
            ("objects", "query_object"),
        ):
            if hard_anchors.get(anchor_key):
                continue
            if explicit_entities.get(entity_key):
                continue
            value = best_entity_value(packet.entities, entity_key)
            if value:
                hard_anchors[anchor_key] = value
                promoted = True

        if "route" in hard_anchors:
            hard_anchors.update(_derive_route_parts(hard_anchors))
        if hard_anchors.get("query_object") and not hard_anchors.get("query_type"):
            hard_anchors["query_type"] = hard_anchors["query_object"]
        return promoted
