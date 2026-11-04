from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from memory.entity_parser import merge_entities


MEMORY_SCHEMA_VERSION = 2


def utcish_now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def unique_strings(values: Iterable[Any]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def normalize_confidence(value: Any, default: float = 0.0) -> float:
    try:
        score = float(value)
    except Exception:
        score = float(default)
    if score > 1.0:
        score = score / 100.0
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return score


def normalize_entities(entities: Any) -> Dict[str, List[str]]:
    return merge_entities(entities if isinstance(entities, dict) else {})


@dataclass
class MemoryPacket:
    id: str
    scope: str
    kind: str
    source: str
    text: str = ""
    summary_l0: str = ""
    overview_l1: str = ""
    entities: Dict[str, List[str]] = field(default_factory=dict)
    slots: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    provenance: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utcish_now)
    last_seen: str = field(default_factory=utcish_now)
    expires_at: str = ""
    tags: List[str] = field(default_factory=list)
    schema_version: int = MEMORY_SCHEMA_VERSION

    def __post_init__(self):
        self.id = str(self.id or "").strip()
        self.scope = str(self.scope or "dialogue").strip()
        self.kind = str(self.kind or "memory").strip()
        self.source = str(self.source or "unknown").strip()
        self.text = str(self.text or "").strip()
        self.summary_l0 = str(self.summary_l0 or self.text[:160]).strip()
        self.overview_l1 = str(self.overview_l1 or self.text[:1200]).strip()
        self.entities = normalize_entities(self.entities)
        self.slots = dict(self.slots or {})
        self.confidence = normalize_confidence(self.confidence)
        self.provenance = dict(self.provenance or {})
        self.created_at = str(self.created_at or utcish_now()).strip()
        self.last_seen = str(self.last_seen or self.created_at).strip()
        self.expires_at = str(self.expires_at or "").strip()
        self.tags = unique_strings(self.tags)
        self.schema_version = int(self.schema_version or MEMORY_SCHEMA_VERSION)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "scope": self.scope,
            "kind": self.kind,
            "source": self.source,
            "text": self.text,
            "summary_l0": self.summary_l0,
            "overview_l1": self.overview_l1,
            "entities": normalize_entities(self.entities),
            "slots": dict(self.slots),
            "confidence": round(float(self.confidence), 4),
            "provenance": dict(self.provenance),
            "created_at": self.created_at,
            "last_seen": self.last_seen,
            "expires_at": self.expires_at,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryPacket":
        payload = data if isinstance(data, dict) else {}
        return cls(
            id=str(payload.get("id") or payload.get("memory_id") or "").strip(),
            schema_version=int(payload.get("schema_version") or MEMORY_SCHEMA_VERSION),
            scope=str(payload.get("scope") or "dialogue"),
            kind=str(payload.get("kind") or payload.get("memory_type") or "memory"),
            source=str(payload.get("source") or "unknown"),
            text=str(payload.get("text") or payload.get("summary") or ""),
            summary_l0=str(payload.get("summary_l0") or payload.get("summary") or payload.get("text") or ""),
            overview_l1=str(payload.get("overview_l1") or payload.get("text") or payload.get("summary") or ""),
            entities=normalize_entities(payload.get("entities", {})),
            slots=dict(payload.get("slots") or {}),
            confidence=normalize_confidence(payload.get("confidence", payload.get("score", 0.0))),
            provenance=dict(payload.get("provenance") or {}),
            created_at=str(payload.get("created_at") or payload.get("timestamp") or utcish_now()),
            last_seen=str(payload.get("last_seen") or payload.get("timestamp") or utcish_now()),
            expires_at=str(payload.get("expires_at") or ""),
            tags=unique_strings(payload.get("tags") or []),
        )


@dataclass
class MemoryContextPackage:
    hard_anchors: Dict[str, str] = field(default_factory=dict)
    profile_index: List[Dict[str, Any]] = field(default_factory=list)
    soft_context: List[Dict[str, Any]] = field(default_factory=list)
    answer_context: List[Dict[str, Any]] = field(default_factory=list)
    packets: List[Dict[str, Any]] = field(default_factory=list)
    rejected: List[Dict[str, Any]] = field(default_factory=list)
    trace: List[Dict[str, Any]] = field(default_factory=list)
    schema_version: int = MEMORY_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "hard_anchors": dict(self.hard_anchors),
            "profile_index": list(self.profile_index),
            "soft_context": list(self.soft_context),
            "answer_context": list(self.answer_context),
            "packets": list(self.packets),
            "rejected": list(self.rejected),
            "trace": list(self.trace),
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "MemoryContextPackage":
        payload = data if isinstance(data, dict) else {}
        return cls(
            hard_anchors={
                str(key): str(value)
                for key, value in (payload.get("hard_anchors") or {}).items()
                if str(value or "").strip()
            },
            profile_index=list(payload.get("profile_index") or []),
            soft_context=list(payload.get("soft_context") or []),
            answer_context=list(payload.get("answer_context") or []),
            packets=list(payload.get("packets") or []),
            rejected=list(payload.get("rejected") or []),
            trace=list(payload.get("trace") or []),
            schema_version=int(payload.get("schema_version") or MEMORY_SCHEMA_VERSION),
        )
