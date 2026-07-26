from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime
from typing import Any, Dict, Iterable, List

from memory.entity_parser import extract_entities_from_text
from memory.packets import MemoryPacket


PROFILE_INDEX_SCHEMA_VERSION = 3
PROFILE_CATEGORIES = {
    "trains": "train",
    "emus": "emu",
    "routes": "route",
    "stations": "station",
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _stable_key(category: str, value: str) -> str:
    digest = hashlib.sha1(f"{category}:{value}".encode("utf-8")).hexdigest()[:16]
    return f"{category}:{digest}"


def is_explicit_preference_statement(text: str) -> bool:
    """Recognize explicit user preferences, never preference questions."""

    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact or "我" not in compact:
        return False
    if not any(token in compact for token in ("喜欢", "偏好", "最爱", "习惯", "不喜欢")):
        return False
    if any(token in compact for token in ("猜猜", "你觉得", "什么", "哪个", "哪一", "吗", "？", "?")):
        return False
    return True


class LongTermProfileIndex:
    """A small, auditable long-term profile index.

    Like a coding agent's MEMORY.md, the short index is always cheap to load,
    while topic evidence stays in separate files. Entries are soft profile
    hints only: they can describe explicit preferences or recurring interests,
    but can never become railway facts or executable routing slots.
    """

    def __init__(self, root_dir: str, packet_path: str = ""):
        self.root_dir = root_dir
        self.index_path = os.path.join(root_dir, "index.json")
        self.memory_md_path = os.path.join(root_dir, "MEMORY.md")
        self.topics_dir = os.path.join(root_dir, "topics")
        self.packet_path = packet_path
        self._lock = threading.RLock()
        self._candidate_signals: Dict[str, Dict[str, Any]] = {}
        self._seen_packet_ids = set()
        os.makedirs(self.topics_dir, exist_ok=True)
        self.data = self._load()
        self._bootstrap()

    @staticmethod
    def _empty() -> Dict[str, Any]:
        return {
            "schema_version": PROFILE_INDEX_SCHEMA_VERSION,
            "updated_at": "",
            "entries": {},
        }

    def _load(self) -> Dict[str, Any]:
        if not os.path.exists(self.index_path):
            return self._empty()
        try:
            with open(self.index_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict):
                return self._empty()
            if int(payload.get("schema_version") or 0) != PROFILE_INDEX_SCHEMA_VERSION:
                return self._empty()
            payload.setdefault("entries", {})
            payload["schema_version"] = PROFILE_INDEX_SCHEMA_VERSION
            return payload
        except Exception:
            return self._empty()

    def _bootstrap(self):
        packets: List[MemoryPacket] = []
        if self.packet_path and os.path.exists(self.packet_path):
            try:
                with open(self.packet_path, "r", encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            payload = json.loads(line)
                        except Exception:
                            continue
                        if isinstance(payload, dict) and payload.get("source") == "explicit_user":
                            packets.append(MemoryPacket.from_dict(payload))
            except Exception:
                pass

        # Legacy episodic files preserve the original user text. They are a
        # safer migration source than the old mixed user/assistant count bucket.
        episodic_dir = os.path.join(os.path.dirname(self.root_dir), "episodic")
        seen_turns = {
            (
                str(packet.provenance.get("session_id") or ""),
                packet.text.strip(),
            )
            for packet in packets
        }
        if os.path.isdir(episodic_dir):
            for name in sorted(os.listdir(episodic_dir)):
                if not name.endswith(".json"):
                    continue
                path = os.path.join(episodic_dir, name)
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        snapshots = json.load(handle)
                except Exception:
                    continue
                if not isinstance(snapshots, list):
                    continue
                session_id = os.path.splitext(name)[0].lstrip("0") or "0"
                for position, snapshot in enumerate(snapshots):
                    if not isinstance(snapshot, dict):
                        continue
                    text = str(snapshot.get("user_text") or "").strip()
                    if not text or (session_id, text) in seen_turns:
                        continue
                    entities = extract_entities_from_text(text)
                    packets.append(
                        MemoryPacket(
                            id=f"legacy-user:{session_id}:{position + 1}",
                            scope="long_term",
                            kind="user_claim",
                            source="explicit_user",
                            text=text,
                            summary_l0=text[:180],
                            entities=entities,
                            confidence=0.9,
                            provenance={"session_id": session_id, "legacy_episode": True},
                            tags=["legacy", "profile_candidate", "soft_only"],
                            created_at=str(snapshot.get("timestamp") or ""),
                            last_seen=str(snapshot.get("timestamp") or ""),
                        )
                    )
                    seen_turns.add((session_id, text))

        changed = self._update_locked(packets)
        if changed or not os.path.exists(self.index_path):
            self._persist_locked()

    def update(self, packets: Iterable[MemoryPacket]) -> int:
        with self._lock:
            changed = self._update_locked(packets)
            if changed:
                self._persist_locked()
            return changed

    def _update_locked(self, packets: Iterable[MemoryPacket]) -> int:
        entries = self.data.setdefault("entries", {})
        changed = 0

        for packet in packets or []:
            if not isinstance(packet, MemoryPacket) or packet.source != "explicit_user":
                continue
            if not packet.id or packet.id in self._seen_packet_ids:
                continue
            self._seen_packet_ids.add(packet.id)
            preference = is_explicit_preference_statement(packet.text)
            candidates_touched: List[str] = []

            for entity_group, category in PROFILE_CATEGORIES.items():
                for value in dict.fromkeys(packet.entities.get(entity_group, []) or []):
                    value = str(value or "").strip()
                    if not value:
                        continue
                    key = self._touch_candidate(
                        category=category,
                        value=value,
                        packet=packet,
                        explicit_preference=preference,
                    )
                    candidates_touched.append(key)

            # Stable non-rail preferences may have no railway entity at all.
            if preference and not candidates_touched:
                normalized = re.sub(r"\s+", " ", packet.text).strip()[:160]
                key = self._touch_candidate(
                    category="preference",
                    value=normalized,
                    packet=packet,
                    explicit_preference=True,
                )
                candidates_touched.append(key)

            for key in candidates_touched:
                candidate = self._candidate_signals[key]
                if float(candidate.get("importance_score") or 0.0) < 0.68:
                    continue
                existing = entries.get(key) if isinstance(entries.get(key), dict) else {}
                existing_profile = {
                    name: value
                    for name, value in existing.items()
                    if name != "consolidated_at"
                }
                if existing_profile == candidate:
                    continue
                consolidated = dict(candidate)
                consolidated["consolidated_at"] = _now()
                entries[key] = consolidated
                changed += 1

        if changed:
            self.data["updated_at"] = _now()
        return changed

    def _touch_candidate(
        self,
        *,
        category: str,
        value: str,
        packet: MemoryPacket,
        explicit_preference: bool,
    ) -> str:
        key = _stable_key(category, value)
        entry = self._candidate_signals.setdefault(
            key,
            {
                "id": key,
                "category": category,
                "value": value,
                "mention_count": 0,
                "explicit_preference_count": 0,
                "first_seen": packet.created_at,
                "last_seen": packet.last_seen or packet.created_at,
                "evidence": [],
            },
        )
        entry["mention_count"] = int(entry.get("mention_count") or 0) + 1
        if explicit_preference:
            entry["explicit_preference_count"] = int(entry.get("explicit_preference_count") or 0) + 1
        entry["importance_score"] = self._importance_score(
            category=category,
            mention_count=int(entry.get("mention_count") or 0),
            explicit_preference_count=int(entry.get("explicit_preference_count") or 0),
        )
        entry["last_seen"] = packet.last_seen or packet.created_at or _now()
        evidence = list(entry.get("evidence") or [])
        evidence.append(
            {
                "packet_id": packet.id,
                "text": packet.text[:220],
                "created_at": packet.created_at,
                "session_id": packet.provenance.get("session_id"),
                "explicit_preference": bool(explicit_preference),
            }
        )
        entry["evidence"] = evidence[-12:]
        return key

    @staticmethod
    def _importance_score(category: str, mention_count: int, explicit_preference_count: int) -> float:
        if explicit_preference_count > 0:
            return 1.0
        base = {
            "train": 0.35,
            "emu": 0.35,
            "route": 0.22,
            "station": 0.16,
            "preference": 1.0,
        }.get(category, 0.12)
        score = base + max(0, mention_count - 1) * 0.18
        return round(min(0.92, score), 4)

    def retrieve(self, user_text: str, limit: int = 6) -> List[Dict[str, Any]]:
        with self._lock:
            entries = list((self.data.get("entries") or {}).values())
        query_entities = extract_entities_from_text(user_text)
        explicit_values = {
            str(value)
            for group in PROFILE_CATEGORIES
            for value in query_entities.get(group, []) or []
        }

        ranked: List[tuple[float, Dict[str, Any]]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            value = str(entry.get("value") or "").strip()
            count = int(entry.get("mention_count") or 0)
            preference_count = int(entry.get("explicit_preference_count") or 0)
            exact = value in explicit_values or (value and value in str(user_text or ""))
            if not exact and preference_count <= 0 and count < 2:
                continue
            score = float(count * 2 + preference_count * 20 + (18 if exact else 0))
            classification = "explicit_preference" if preference_count > 0 else "recurring_interest"
            if classification == "explicit_preference":
                summary = f"用户曾明确表达偏好：{value}"
            else:
                summary = f"用户曾主动关注 {value}（{count} 次）；这只代表关注频率，不代表最喜欢"
            ranked.append(
                (
                    score,
                    {
                        "id": entry.get("id"),
                        "category": entry.get("category"),
                        "value": value,
                        "classification": classification,
                        "mention_count": count,
                        "explicit_preference_count": preference_count,
                        "summary_l0": summary,
                        "last_seen": entry.get("last_seen", ""),
                        "importance_score": entry.get("importance_score", 0.0),
                        "confidence": 0.98 if preference_count > 0 else min(0.82, 0.5 + count * 0.04),
                        "allowed_usage": "soft_profile_only",
                        "tags": ["profile", "soft_only", classification],
                    },
                )
            )

        ranked.sort(key=lambda item: (-item[0], str(item[1].get("category")), str(item[1].get("value"))))
        max_items = max(1, int(limit))
        if explicit_values:
            return [item for _, item in ranked[:max_items]]

        # A generic profile question should see a small cross-section instead
        # of six stations crowding out trains, EMUs, and explicit preferences.
        selected: List[Dict[str, Any]] = []
        selected_ids = set()
        category_quotas = (("preference", 2), ("train", 2), ("emu", 2), ("route", 1), ("station", 1))
        for category, quota in category_quotas:
            candidates = [item for _, item in ranked if item.get("category") == category][:quota]
            for candidate in candidates:
                selected.append(candidate)
                selected_ids.add(candidate.get("id"))
                if len(selected) >= max_items:
                    return selected
        for _, item in ranked:
            if item.get("id") in selected_ids:
                continue
            selected.append(item)
            if len(selected) >= max_items:
                break
        return selected

    def _persist_locked(self):
        os.makedirs(self.root_dir, exist_ok=True)
        temp_path = self.index_path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(self.data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(temp_path, self.index_path)
        self._write_topics_locked()
        self._write_memory_md_locked()

    def _write_topics_locked(self):
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for entry in (self.data.get("entries") or {}).values():
            if isinstance(entry, dict):
                grouped.setdefault(str(entry.get("category") or "other"), []).append(entry)
        for category, entries in grouped.items():
            entries.sort(
                key=lambda item: (
                    -int(item.get("explicit_preference_count") or 0),
                    -int(item.get("mention_count") or 0),
                    str(item.get("value") or ""),
                )
            )
            path = os.path.join(self.topics_dir, f"{category}.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(entries[:250], handle, ensure_ascii=False, indent=2, sort_keys=True)

    def _write_memory_md_locked(self):
        entries = [item for item in (self.data.get("entries") or {}).values() if isinstance(item, dict)]
        entries.sort(
            key=lambda item: (
                -int(item.get("explicit_preference_count") or 0),
                -int(item.get("mention_count") or 0),
                str(item.get("value") or ""),
            )
        )
        lines = [
            "# RailGPT User Memory",
            "",
            "> Soft profile index only. Never use these entries as railway facts, live status, dates, or routing slots.",
            "> Repeated attention is not the same as an explicit favorite.",
            "",
            "## Explicit Preferences",
        ]
        preferences = [item for item in entries if int(item.get("explicit_preference_count") or 0) > 0][:12]
        lines.extend(
            f"- {item.get('category')}: {item.get('value')}"
            for item in preferences
        )
        if not preferences:
            lines.append("- None recorded yet.")
        lines.extend(["", "## Recurring Interests"])
        recurring = [item for item in entries if int(item.get("mention_count") or 0) >= 2][:24]
        lines.extend(
            f"- {item.get('category')}: {item.get('value')} ({int(item.get('mention_count') or 0)} mentions)"
            for item in recurring
        )
        if not recurring:
            lines.append("- None recorded yet.")
        lines.extend(["", "Detailed provenance is stored under `topics/`.", ""])
        with open(self.memory_md_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines[:190]))
