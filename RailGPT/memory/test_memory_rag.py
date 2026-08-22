import json
import os
import tempfile
import threading
import unittest

import numpy as np

from memory.episodic import EpisodicMemoryStore, MemoryManager
from memory.long_term import LongTermMemoryStore
from memory.session import SessionMemory


class FakeEmbedder:
    enabled = True
    model_name = "fake-memory-rag"

    def embed_texts(self, texts, block=True):
        vectors = []
        for text in texts:
            value = str(text or "")
            vector = np.zeros(3, dtype=float)
            if "G257" in value or "route" in value or "线路" in value:
                vector[0] += 1.0
            if "南京南" in value or "徐州东" in value or "benchmark" in value or "标杆" in value:
                vector[1] += 1.0
            if "余票" in value or "有票" in value:
                vector[2] += 1.0
            if not vector.any():
                vector += 0.1
            vector /= np.linalg.norm(vector)
            vectors.append(vector)
        return np.asarray(vectors, dtype=float)


class ColdStartEmbedder:
    enabled = True
    model_name = "cold-start-memory-rag"

    def __init__(self):
        self.calls = []

    def embed_texts(self, texts, block=True):
        self.calls.append({"texts": list(texts), "block": block})
        if not block:
            return None
        vectors = []
        for _ in texts:
            vectors.append(np.asarray([1.0, 0.0, 0.0], dtype=float))
        return np.asarray(vectors, dtype=float)


class MemoryRAGTest(unittest.TestCase):
    def make_temp_dir(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        return temp_dir.name

    def test_episodic_store_exposes_embedding_score(self):
        root_dir = self.make_temp_dir()
        store = EpisodicMemoryStore(root_dir, embedder=FakeEmbedder())
        store.record_turn(
            conversation_id=1,
            user_text="G257 沿着什么高铁线路运行？",
            ai_text="G257 沿京沪高铁和合福高铁运行。",
            anchor_snapshot={"train": "G257", "date": "2026-03-24", "query_type": "path_detail"},
        )

        store.warm_cache(1, block=True)
        results = store.retrieve(1, "这趟列车沿着什么高铁线路运行呢", limit=1)

        self.assertTrue(results)
        self.assertGreater(results[0].get("embedding_score", 0.0), 0.5)

    def test_long_term_store_uses_embedding_score(self):
        root_dir = self.make_temp_dir()
        store = LongTermMemoryStore(root_dir, embedder=FakeEmbedder())
        store.record_turn(
            {
                "routes": ["南京南-徐州东"],
                "stations": ["南京南", "徐州东"],
                "objects": ["s2s_benchmark"],
                "trains": [],
                "emus": [],
                "dates": [],
                "tokens": [],
            }
        )
        store.record_turn(
            {
                "routes": ["南京南-徐州东"],
                "stations": ["南京南", "徐州东"],
                "objects": ["s2s_benchmark"],
                "trains": [],
                "emus": [],
                "dates": [],
                "tokens": [],
            }
        )

        store.warm_cache(block=True)
        results = store.retrieve("这条线还有标杆车吗", limit=3)

        self.assertTrue(results)
        route_items = [item for item in results if item.get("field") == "route"]
        self.assertTrue(route_items)
        self.assertGreater(route_items[0].get("embedding_score", 0.0), 0.1)

    def test_cold_start_embedder_never_blocks_rule_recall(self):
        root_dir = self.make_temp_dir()
        embedder = ColdStartEmbedder()
        store = EpisodicMemoryStore(root_dir, embedder=embedder)
        store.record_turn(
            conversation_id=7,
            user_text="G257 沿着什么高铁线路运行？",
            ai_text="G257 沿京沪高铁和合福高铁运行。",
            anchor_snapshot={"train": "G257", "date": "2026-03-24", "query_type": "path_detail"},
        )

        results = store.retrieve(7, "这趟列车沿着什么高铁线路运行呢", limit=1)

        self.assertTrue(results)
        self.assertEqual(results[0].get("embedding_score", 0.0), 0.0)
        self.assertTrue(any(call.get("block") is False for call in embedder.calls))

    def test_background_indexing_keeps_one_off_turn_out_of_long_term_profile(self):
        root_dir = self.make_temp_dir()
        manager = MemoryManager(root_dir=root_dir, enable_embedding=True, embedding_model="fake-memory-rag")
        fake_embedder = FakeEmbedder()
        manager.embedder = fake_embedder
        manager.episodic.embedder = fake_embedder
        manager.long_term.embedder = fake_embedder
        manager.episodic.vector_cache.model_name = fake_embedder.model_name
        manager.long_term.vector_cache.model_name = fake_embedder.model_name

        session = SessionMemory()
        session.set_session_id(12)
        session.update_anchor(route="南京南-徐州东", date="2026-03-25", query_type="s2s_benchmark")

        manager.record_turn(
            session=session,
            user_text="南京南到徐州东最快标杆车",
            ai_text="最快标杆车已经给出。",
            tasks=[
                {
                    "action": "query",
                    "params": {
                        "domain": "railway",
                        "object": "s2s_benchmark",
                        "id": "南京南-徐州东",
                        "date": "2026-03-25",
                    },
                }
            ],
            facts={
                "queries": [
                    {
                        "domain": "railway",
                        "object": "s2s_benchmark",
                        "id": "南京南-徐州东",
                        "date": "2026-03-25",
                    }
                ]
            },
        )

        self.assertTrue(manager.wait_for_indexing(timeout=2.0))

        episodic_vectors_path = os.path.join(root_dir, "episodic", "vectors.json")
        long_term_vectors_path = os.path.join(root_dir, "long_term", "vectors.json")

        with open(episodic_vectors_path, "r", encoding="utf-8") as handle:
            episodic_vectors = json.load(handle)
        self.assertTrue(episodic_vectors.get("items"))
        self.assertFalse(os.path.exists(long_term_vectors_path))
        self.assertEqual(manager.orchestrator.profile_index.data.get("entries"), {})

    def test_memory_manager_emits_recall_and_index_events(self):
        root_dir = self.make_temp_dir()
        manager = MemoryManager(root_dir=root_dir, enable_embedding=True, embedding_model="fake-memory-rag")
        fake_embedder = FakeEmbedder()
        manager.embedder = fake_embedder
        manager.episodic.embedder = fake_embedder
        manager.long_term.embedder = fake_embedder
        manager.episodic.vector_cache.model_name = fake_embedder.model_name
        manager.long_term.vector_cache.model_name = fake_embedder.model_name

        events = []
        manager.set_event_callback(lambda state, detail: events.append((state, detail)))

        session = SessionMemory()
        session.set_session_id(21)
        session.update_anchor(route="南京南-徐州东", date="2026-03-25", query_type="s2s_benchmark")

        manager.record_turn(
            session=session,
            user_text="南京南到徐州东最快标杆车",
            ai_text="最快标杆车已经给出。",
            tasks=[
                {
                    "action": "query",
                    "params": {
                        "domain": "railway",
                        "object": "s2s_benchmark",
                        "id": "南京南-徐州东",
                        "date": "2026-03-25",
                    },
                }
            ],
            facts={
                "queries": [
                    {
                        "domain": "railway",
                        "object": "s2s_benchmark",
                        "id": "南京南-徐州东",
                        "date": "2026-03-25",
                    }
                ]
            },
        )
        self.assertTrue(manager.wait_for_indexing(timeout=2.0))

        recall_bundle = manager.build_recall_bundle(session, "这条线还有标杆车吗")

        self.assertTrue(recall_bundle["session"])
        states = [item[0] for item in events]
        self.assertIn("MEMORY_INDEX_QUEUED", states)
        self.assertIn("MEMORY_INDEXING", states)
        self.assertIn("MEMORY_INDEX_DONE", states)
        self.assertIn("MEMORY_RECALLING", states)
        self.assertIn("MEMORY_READY", states)

    def test_memory_manager_dedupes_background_index_jobs_per_session(self):
        root_dir = self.make_temp_dir()
        manager = MemoryManager(root_dir=root_dir, enable_embedding=True, embedding_model="fake-memory-rag")
        fake_embedder = FakeEmbedder()
        manager.embedder = fake_embedder
        manager.episodic.embedder = fake_embedder
        manager.long_term.embedder = fake_embedder
        manager.episodic.vector_cache.model_name = fake_embedder.model_name
        manager.long_term.vector_cache.model_name = fake_embedder.model_name

        events = []
        manager.set_event_callback(lambda state, detail: events.append((state, detail)))

        start_event = threading.Event()
        release_event = threading.Event()
        calls = {"episodic": 0, "long_term": 0}

        def slow_episodic(conversation_id, block=True):
            calls["episodic"] += 1
            start_event.set()
            release_event.wait(timeout=1.0)
            return 1

        def slow_long_term(block=True):
            calls["long_term"] += 1
            release_event.wait(timeout=1.0)
            return 1

        manager.episodic.warm_cache = slow_episodic
        manager.long_term.warm_cache = slow_long_term

        session = SessionMemory()
        session.set_session_id(33)
        session.update_anchor(route="南京南-徐州东", date="2026-03-25", query_type="s2s_benchmark")

        for idx in range(3):
            manager.record_turn(
                session=session,
                user_text="南京南到徐州东最快标杆车",
                ai_text=f"第{idx + 1}次回答",
                tasks=[
                    {
                        "action": "query",
                        "params": {
                            "domain": "railway",
                            "object": "s2s_benchmark",
                            "id": "南京南-徐州东",
                            "date": "2026-03-25",
                        },
                    }
                ],
                facts={
                    "queries": [
                        {
                            "domain": "railway",
                            "object": "s2s_benchmark",
                            "id": "南京南-徐州东",
                            "date": "2026-03-25",
                        }
                    ]
                },
            )
            if idx == 0:
                self.assertTrue(start_event.wait(timeout=1.0))

        release_event.set()
        self.assertTrue(manager.wait_for_indexing(timeout=2.0))

        states = [item[0] for item in events]
        self.assertEqual(states.count("MEMORY_INDEXING"), 2)
        self.assertEqual(states.count("MEMORY_INDEX_DONE"), 2)
        self.assertEqual(states.count("MEMORY_INDEX_QUEUED"), 2)
        self.assertEqual(calls["episodic"], 2)
        self.assertEqual(calls["long_term"], 2)


if __name__ == "__main__":
    unittest.main()
