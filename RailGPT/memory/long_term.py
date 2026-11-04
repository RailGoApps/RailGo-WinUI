from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from memory.entity_parser import extract_entities_from_text, merge_entities
from memory.embedding_index import JsonEmbeddingCache, MemoryEmbedder


class LongTermMemoryStore:
    def __init__(self, root_dir: str, embedder: Optional[MemoryEmbedder] = None):
        self.root_dir = root_dir
        os.makedirs(self.root_dir, exist_ok=True)
        self.path = os.path.join(self.root_dir, "profile.json")
        self.embedder = embedder or MemoryEmbedder(enabled=False)
        self.vector_cache = JsonEmbeddingCache(
            os.path.join(self.root_dir, "vectors.json"),
            model_name=self.embedder.model_name,
        )
        self.data = self._load()

    def _load(self) -> Dict[str, Any]:
        if not os.path.exists(self.path):
            return {
                "routes": {},
                "trains": {},
                "emus": {},
                "stations": {},
                "objects": {},
                "updated": "",
            }
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return {
                "routes": {},
                "trains": {},
                "emus": {},
                "stations": {},
                "objects": {},
                "updated": "",
            }

    def _save(self):
        self.data["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(self.data, handle, ensure_ascii=False, indent=2)

    def _touch_bucket(self, bucket_name: str, value: str):
        if not value:
            return
        bucket = self.data.setdefault(bucket_name, {})
        item = bucket.setdefault(value, {"count": 0, "last_seen": ""})
        item["count"] += 1
        item["last_seen"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def record_turn(self, entities: Dict[str, List[str]]):
        merged = merge_entities(entities)
        for route in merged["routes"]:
            self._touch_bucket("routes", route)
        for train in merged["trains"]:
            self._touch_bucket("trains", train)
        for emu in merged["emus"]:
            self._touch_bucket("emus", emu)
        for station in merged["stations"]:
            self._touch_bucket("stations", station)
        for obj in merged["objects"]:
            self._touch_bucket("objects", obj)
        self._save()

    def warm_cache(self, block: bool = True) -> int:
        if not self.embedder.enabled:
            return 0

        entries = self._build_embedding_entries()
        if not entries:
            return 0

        vectors = self.vector_cache.get_vectors(entries, self.embedder, block=block)
        return len(vectors)

    def retrieve(self, user_text: str, session=None, limit: int = 3) -> List[Dict[str, Any]]:
        query_entities = extract_entities_from_text(user_text)
        anchor_snapshot = session.get_anchor_snapshot() if session else {}

        items: List[Dict[str, Any]] = []
        for bucket_name, prefix, label in (
            ("routes", "route", "你最近多次关注区间"),
            ("trains", "train", "你最近多次关注车次"),
            ("emus", "emu", "你最近多次关注动车组"),
            ("stations", "station", "你最近多次关注车站"),
            ("objects", "object", "你最近多次追问的主题"),
        ):
            bucket = self.data.get(bucket_name, {})
            if not isinstance(bucket, dict):
                continue
            for value, payload in bucket.items():
                count = int(payload.get("count", 0))
                if count <= 0:
                    continue
                score = float(count)
                if value in query_entities.get("routes", []) or value in query_entities.get("trains", []) or value in query_entities.get("emus", []) or value in query_entities.get("stations", []) or value in query_entities.get("objects", []):
                    score += 8.0
                if value and any(value == anchor for anchor in anchor_snapshot.values()):
                    score += 6.0
                if score < 2.0:
                    continue
                items.append(
                    {
                        "memory_type": "long_term",
                        "score": score,
                        "field": prefix,
                        "value": value,
                        "text": f"{label}: {value}（累计 {count} 次）",
                    }
                )

        if self.embedder.enabled and items:
            entries = [
                {
                    "key": f"{item['field']}:{item['value']}",
                    "text": item["text"],
                }
                for item in items
            ]
            embedding_scores = self.vector_cache.query(user_text, entries, self.embedder, block=False)
            for item in items:
                key = f"{item['field']}:{item['value']}"
                embedding_score = embedding_scores.get(key, 0.0)
                item["embedding_score"] = round(embedding_score, 3)
                item["score"] += max(0.0, embedding_score) * 3.5

        items.sort(key=lambda item: (-item["score"], item["text"]))
        return items[:limit]

    def _build_embedding_entries(self) -> List[Dict[str, str]]:
        entries: List[Dict[str, str]] = []
        for bucket_name, prefix, label in (
            ("routes", "route", "你最近多次关注区间"),
            ("trains", "train", "你最近多次关注车次"),
            ("emus", "emu", "你最近多次关注动车组"),
            ("stations", "station", "你最近多次关注车站"),
            ("objects", "object", "你最近多次追问的主题"),
        ):
            bucket = self.data.get(bucket_name, {})
            if not isinstance(bucket, dict):
                continue
            for value, payload in bucket.items():
                count = int(payload.get("count", 0))
                if count <= 0:
                    continue
                entries.append(
                    {
                        "key": f"{prefix}:{value}",
                        "text": f"{label}: {value} (累计 {count} 次)",
                    }
                )
        return entries
