from __future__ import annotations

import json
import os
import queue
import threading
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from memory.entity_parser import (
    best_entity_value,
    extract_entities_from_tasklike_items,
    extract_entities_from_text,
    extract_text_tokens,
    has_substantive_entities,
    merge_entities,
)
from memory.embedding_index import JsonEmbeddingCache, MemoryEmbedder
from memory.long_term import LongTermMemoryStore
from memory.orchestrator import MemoryOrchestrator


def _jaccard_score(left: Iterable[str], right: Iterable[str]) -> float:
    left_set = {str(item) for item in left if str(item or "").strip()}
    right_set = {str(item) for item in right if str(item or "").strip()}
    if not left_set or not right_set:
        return 0.0
    intersection = len(left_set & right_set)
    union = len(left_set | right_set)
    if union <= 0:
        return 0.0
    return intersection / union


def _anchor_snapshot_to_entities(anchor_snapshot: Dict[str, str]) -> Dict[str, List[str]]:
    route = str(anchor_snapshot.get("route") or "").strip()
    stations: List[str] = []
    if route and "-" in route:
        dep, arr = route.split("-", 1)
        stations.extend([dep, arr])

    query_object = str(anchor_snapshot.get("query_object") or anchor_snapshot.get("query_type") or "").strip()
    return {
        "trains": [str(anchor_snapshot.get("train") or "").strip()] if anchor_snapshot.get("train") else [],
        "emus": [str(anchor_snapshot.get("emu") or "").strip()] if anchor_snapshot.get("emu") else [],
        "routes": [route] if route else [],
        "dates": [str(anchor_snapshot.get("date") or "").strip()] if anchor_snapshot.get("date") else [],
        "stations": stations,
        "objects": [query_object] if query_object else [],
        "tokens": [],
    }


class EpisodicMemoryStore:
    def __init__(self, root_dir: str, embedder: Optional[MemoryEmbedder] = None):
        self.root_dir = root_dir
        os.makedirs(self.root_dir, exist_ok=True)
        self.embedder = embedder or MemoryEmbedder(enabled=False)
        self.vector_cache = JsonEmbeddingCache(
            os.path.join(self.root_dir, "vectors.json"),
            model_name=self.embedder.model_name,
        )

    def _path_for(self, conversation_id: int) -> str:
        return os.path.join(self.root_dir, f"{int(conversation_id):03d}.json")

    def _load(self, conversation_id: int) -> List[Dict[str, Any]]:
        path = self._path_for(conversation_id)
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, list):
                return data
        except Exception:
            pass
        return []

    def _save(self, conversation_id: int, snapshots: List[Dict[str, Any]]):
        path = self._path_for(conversation_id)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(snapshots[-120:], handle, ensure_ascii=False, indent=2)

    def record_turn(
        self,
        conversation_id: Optional[int],
        user_text: str,
        ai_text: str = "",
        tasks: Optional[List[Dict[str, Any]]] = None,
        facts: Optional[Dict[str, Any]] = None,
        anchor_snapshot: Optional[Dict[str, str]] = None,
    ):
        if conversation_id is None:
            return

        snapshots = self._load(conversation_id)
        text_entities = merge_entities(
            extract_entities_from_text(user_text),
            extract_entities_from_text(ai_text),
        )
        task_entities = extract_entities_from_tasklike_items(tasks or [])
        fact_entities = extract_entities_from_tasklike_items((facts or {}).get("queries", []))
        anchor_entities = _anchor_snapshot_to_entities(anchor_snapshot or {})
        entities = merge_entities(text_entities, task_entities, fact_entities, anchor_entities)
        if not has_substantive_entities(entities):
            return

        summary = user_text.strip()
        if ai_text.strip():
            summary += f" -> {ai_text.strip()[:120]}"

        snapshots.append(
            {
                "memory_id": f"{int(conversation_id):03d}:{len(snapshots) + 1}",
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "user_text": user_text,
                "ai_text": ai_text[:240],
                "summary": summary[:320],
                "entities": entities,
                "query_objects": entities.get("objects", []),
            }
        )
        self._save(conversation_id, snapshots)

    def retrieve(
        self,
        conversation_id: Optional[int],
        user_text: str,
        session=None,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        if conversation_id is None:
            return []

        snapshots = self._load(conversation_id)
        if not snapshots:
            return []

        query_entities = extract_entities_from_text(user_text)
        query_tokens = extract_text_tokens(user_text)
        anchor_snapshot = session.get_anchor_snapshot() if session else {}
        embedding_scores = self._embedding_scores(conversation_id, snapshots, user_text)

        ranked: List[Dict[str, Any]] = []
        for index, snapshot in enumerate(snapshots):
            entities = snapshot.get("entities", {})
            tokens = entities.get("tokens", [])

            score = 0.0
            score += 8.0 * _jaccard_score(query_entities.get("trains", []), entities.get("trains", []))
            score += 7.0 * _jaccard_score(query_entities.get("routes", []), entities.get("routes", []))
            score += 5.0 * _jaccard_score(query_entities.get("emus", []), entities.get("emus", []))
            score += 3.0 * _jaccard_score(query_entities.get("objects", []), entities.get("objects", []))
            score += 2.0 * _jaccard_score(query_entities.get("stations", []), entities.get("stations", []))
            score += 1.5 * _jaccard_score(query_tokens, tokens)

            if any(value and value in (entities.get("trains", []) + entities.get("routes", []) + entities.get("emus", [])) for value in anchor_snapshot.values()):
                score += 3.0

            memory_id = str(snapshot.get("memory_id") or f"{int(conversation_id):03d}:{index + 1}")
            embedding_score = embedding_scores.get(memory_id, 0.0)
            score += max(0.0, embedding_score) * 4.5
            score += max(0.0, 1.2 - (len(snapshots) - index - 1) * 0.08)

            if score <= 0.75 and embedding_score <= 0.35:
                continue

            ranked.append(
                {
                    "memory_type": "episodic",
                    "score": round(score, 3),
                    "embedding_score": round(embedding_score, 3),
                    "text": snapshot.get("summary", "")[:220],
                    "user_text": str(snapshot.get("user_text") or "")[:600],
                    "entities": entities,
                }
            )

        ranked.sort(key=lambda item: (-item["score"], item["text"]))
        return ranked[:limit]

    def _embedding_scores(self, conversation_id: int, snapshots: List[Dict[str, Any]], user_text: str) -> Dict[str, float]:
        if not self.embedder.enabled:
            return {}

        entries = self._build_embedding_entries(conversation_id, snapshots)
        return self.vector_cache.query(user_text, entries, self.embedder, block=False)

    def warm_cache(self, conversation_id: Optional[int], block: bool = True) -> int:
        if conversation_id is None or not self.embedder.enabled:
            return 0

        snapshots = self._load(conversation_id)
        if not snapshots:
            return 0

        entries = self._build_embedding_entries(conversation_id, snapshots)
        vectors = self.vector_cache.get_vectors(entries, self.embedder, block=block)
        return len(vectors)

    def _build_embedding_entries(self, conversation_id: int, snapshots: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        entries = []
        for index, snapshot in enumerate(snapshots):
            memory_id = str(snapshot.get("memory_id") or f"{int(conversation_id):03d}:{index + 1}")
            entries.append(
                {
                    "key": memory_id,
                    "text": self._embedding_text(snapshot),
                }
            )
        return entries

    @staticmethod
    def _embedding_text(snapshot: Dict[str, Any]) -> str:
        entities = snapshot.get("entities", {}) if isinstance(snapshot.get("entities"), dict) else {}
        entity_lines = []
        for key in ("trains", "routes", "dates", "objects", "stations", "emus"):
            values = entities.get(key, [])
            if isinstance(values, list) and values:
                entity_lines.append(f"{key}: {' | '.join(str(item) for item in values[:6])}")
        return "\n".join(
            part
            for part in (
                str(snapshot.get("user_text") or "").strip(),
                str(snapshot.get("ai_text") or "").strip(),
                str(snapshot.get("summary") or "").strip(),
                "\n".join(entity_lines).strip(),
            )
            if part
        )


class MemoryManager:
    def __init__(
        self,
        root_dir: str = "memory_store",
        enable_embedding: bool = False,
        embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    ):
        self.root_dir = root_dir
        os.makedirs(self.root_dir, exist_ok=True)
        self.embedder = MemoryEmbedder(enabled=enable_embedding, model_name=embedding_model)
        self.episodic = EpisodicMemoryStore(os.path.join(self.root_dir, "episodic"), embedder=self.embedder)
        self.long_term = LongTermMemoryStore(os.path.join(self.root_dir, "long_term"), embedder=self.embedder)
        self.orchestrator = MemoryOrchestrator(self.root_dir)
        self._index_queue = queue.Queue()
        self._index_worker_lock = threading.Lock()
        self._index_worker_started = False
        self._index_state_lock = threading.Lock()
        self._queued_index_sessions = set()
        self._active_index_sessions = set()
        self._dirty_index_sessions = set()
        self._event_callback = None

    def set_event_callback(self, callback):
        self._event_callback = callback
        if hasattr(self.orchestrator, "set_event_callback"):
            self.orchestrator.set_event_callback(callback)

    def _emit_event(self, state_name: str, detail: str):
        callback = self._event_callback
        if not callable(callback):
            return
        try:
            callback(state_name, detail)
        except Exception:
            pass

    def warmup_async(self):
        if hasattr(self.embedder, "warmup_async"):
            self.embedder.warmup_async()

    def build_recall_bundle(self, session, user_text: str) -> Dict[str, Any]:
        self._emit_event(
            "MEMORY_RECALLING",
            "memory rag recalling session, episodic and long-term memories",
        )
        self.warmup_async()
        session_items = self._build_session_items(session)
        episodic_items = self.episodic.retrieve(
            conversation_id=getattr(session, "session_id", None),
            user_text=user_text,
            session=session,
            limit=3,
        )
        long_term_items = self.long_term.retrieve(user_text=user_text, session=session, limit=6)

        if session_items:
            self._emit_event(
                "MEMORY_SESSION_HIT",
                f"session recall hit {len(session_items)} items",
            )
        if episodic_items:
            self._emit_event(
                "MEMORY_EPISODIC_HIT",
                f"episodic recall hit {len(episodic_items)} items",
            )
        if long_term_items:
            self._emit_event(
                "MEMORY_LONGTERM_HIT",
                f"long-term recall hit {len(long_term_items)} items",
            )

        context_package = self.orchestrator.build_context_package(
            session=session,
            user_text=user_text,
            session_items=session_items,
            episodic_items=episodic_items,
            long_term_items=long_term_items,
        )
        package_payload = context_package.to_dict()
        anchor_candidates = dict(package_payload.get("hard_anchors") or {})

        anchor_keys = ",".join(sorted(anchor_candidates.keys())) or "none"
        self._emit_event(
            "MEMORY_READY",
            "memory rag ready "
            f"session={len(session_items)} episodic={len(episodic_items)} "
            f"long_term={len(long_term_items)} anchors={anchor_keys}",
        )

        return {
            "session": session_items,
            "episodic": episodic_items,
            "long_term": long_term_items,
            "anchor_candidates": anchor_candidates,
            "hard_anchor_candidates": anchor_candidates,
            "memory_context_package": package_payload,
            "profile_index": package_payload.get("profile_index", []),
            "soft_context": package_payload.get("soft_context", []),
            "answer_context": package_payload.get("answer_context", []),
            "rejected": package_payload.get("rejected", []),
        }

    def record_turn(
        self,
        session,
        user_text: str,
        ai_text: str = "",
        tasks: Optional[List[Dict[str, Any]]] = None,
        facts: Optional[Dict[str, Any]] = None,
    ):
        anchor_snapshot = session.get_anchor_snapshot() if session else {}

        self.orchestrator.curate_turn(
            session=session,
            user_text=user_text,
            ai_text=ai_text,
            tasks=tasks,
            facts=facts,
        )

        self.episodic.record_turn(
            conversation_id=getattr(session, "session_id", None),
            user_text=user_text,
            ai_text=ai_text,
            tasks=tasks,
            facts=facts,
            anchor_snapshot=anchor_snapshot,
        )
        # The legacy count bucket remains read-only for compatibility. New
        # long-term writes go through the importance-scored profile index.
        self._enqueue_index_job(getattr(session, "session_id", None))

    def _build_session_items(self, session) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        if session is None:
            return items

        anchors = session.get_anchor_snapshot()
        if anchors:
            items.append(
                {
                    "memory_type": "session",
                    "score": 100.0,
                    "text": "当前会话锚点: "
                    + ", ".join(f"{key}={value}" for key, value in anchors.items()),
                }
            )

        recent_pool = session.get_recent_entity_pool()
        if recent_pool:
            summary_parts = []
            if recent_pool.get("trains"):
                summary_parts.append("trains=" + ",".join(recent_pool["trains"][:6]))
            if recent_pool.get("routes"):
                summary_parts.append("routes=" + ",".join(recent_pool["routes"][:4]))
            if recent_pool.get("objects"):
                summary_parts.append("objects=" + ",".join(recent_pool["objects"][:6]))
            if summary_parts:
                items.append(
                    {
                        "memory_type": "session",
                        "score": 92.0,
                        "text": "最近会话实体池: " + " | ".join(summary_parts),
                        "entities": recent_pool,
                    }
                )

        recent_messages = session.get_recent_messages(4)
        for message in recent_messages[-2:]:
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            role = message.get("role") or "message"
            items.append(
                {
                    "memory_type": "session",
                    "score": 80.0,
                    "text": f"最近{role}: {content[:120]}",
                }
            )
        return items[:3]

    def _ensure_index_worker(self):
        if not self.embedder.enabled:
            return

        with self._index_worker_lock:
            if self._index_worker_started:
                return
            self._index_worker_started = True

        threading.Thread(target=self._index_worker_loop, daemon=True).start()

    def _enqueue_index_job(self, conversation_id: Optional[int]):
        if not self.embedder.enabled:
            return
        self._ensure_index_worker()

        with self._index_state_lock:
            if conversation_id in self._queued_index_sessions:
                return
            if conversation_id in self._active_index_sessions:
                self._dirty_index_sessions.add(conversation_id)
                return
            self._queued_index_sessions.add(conversation_id)

        self._emit_event(
            "MEMORY_INDEX_QUEUED",
            f"queued background memory indexing for session={conversation_id}",
        )
        self._index_queue.put({"conversation_id": conversation_id})

    def _index_worker_loop(self):
        while True:
            job = self._index_queue.get()
            conversation_id = None
            requeue_after = False
            try:
                if isinstance(job, dict):
                    conversation_id = job.get("conversation_id")
                with self._index_state_lock:
                    self._queued_index_sessions.discard(conversation_id)
                    self._active_index_sessions.add(conversation_id)
                self._emit_event(
                    "MEMORY_INDEXING",
                    f"background memory indexing started for session={conversation_id}",
                )
                episodic_count = self.episodic.warm_cache(conversation_id, block=True)
                long_term_count = self.long_term.warm_cache(block=True)
                self._emit_event(
                    "MEMORY_INDEX_DONE",
                    "background memory indexing finished "
                    f"session={conversation_id} episodic_vectors={episodic_count} "
                    f"long_term_vectors={long_term_count}",
                )
            except Exception as exc:
                self._emit_event(
                    "MEMORY_INDEX_FAILED",
                    f"background memory indexing failed: {exc}",
                )
            finally:
                with self._index_state_lock:
                    self._active_index_sessions.discard(conversation_id)
                    if conversation_id in self._dirty_index_sessions and conversation_id not in self._queued_index_sessions:
                        self._dirty_index_sessions.discard(conversation_id)
                        self._queued_index_sessions.add(conversation_id)
                        requeue_after = True

                if requeue_after:
                    self._emit_event(
                        "MEMORY_INDEX_QUEUED",
                        f"queued background memory indexing for session={conversation_id}",
                    )
                    self._index_queue.put({"conversation_id": conversation_id})
                self._index_queue.task_done()

    def wait_for_indexing(self, timeout: float = 5.0) -> bool:
        if not self.embedder.enabled:
            return True

        import time

        deadline = time.time() + max(0.1, float(timeout))
        while time.time() < deadline:
            if self._index_queue.unfinished_tasks == 0:
                return True
            time.sleep(0.02)
        return self._index_queue.unfinished_tasks == 0
