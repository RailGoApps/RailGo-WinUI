import json
import unittest

from agent.fast_mode import FastFactCompressor


class FakeLLM:
    def __init__(self):
        self.generate_calls = 0

    def generate(self, messages):
        self.generate_calls += 1
        text = messages[-1]["content"]

        if "BROKEN_JSON" in text:
            return "not-json"

        candidates = []
        observations = []

        if "G87" in text:
            candidates.append(
                {
                    "candidate_key": "train:G87",
                    "candidate_type": "train",
                    "label": "G87",
                    "score": 95,
                    "why": "Fast flagship candidate in this lane.",
                    "supporting_points": ["Appears as a strong candidate."],
                    "fact_ids": ["query_000"],
                    "attributes": {"train_no": "G87"},
                }
            )

        if "G89" in text:
            candidates.append(
                {
                    "candidate_key": "train:G89",
                    "candidate_type": "train",
                    "label": "G89",
                    "score": 82,
                    "why": "Secondary candidate in this lane.",
                    "supporting_points": ["Still worth keeping."],
                    "fact_ids": ["query_001"],
                    "attributes": {"train_no": "G89"},
                }
            )

        if "maintenance" in text.lower():
            observations.append(
                {
                    "kind": "warning",
                    "content": "Tool output mentions a maintenance window.",
                    "fact_ids": ["warning_000"],
                }
            )

        if candidates or observations:
            return json.dumps(
                {
                    "is_relevant": True,
                    "candidates": candidates,
                    "observations": observations,
                },
                ensure_ascii=False,
            )

        return json.dumps(
            {
                "is_relevant": False,
                "candidates": [],
                "observations": [],
            },
            ensure_ascii=False,
        )


class FastFactCompressorTest(unittest.TestCase):
    def make_compressor(self, **kwargs):
        return FastFactCompressor(
            llm_factory=lambda: FakeLLM(),
            **kwargs,
        )

    def test_chat_route_messages_are_not_fact_units(self):
        compressor = self.make_compressor()
        facts = {
            "queries": [],
            "analysis": [],
            "comparisons": [],
            "meta": {
                "warnings": [],
                "errors": [],
                "chat_messages": ["Dedicated contextual chat route: internal prompt text"],
                "chat_route_tags": ["contextual_followup"],
            },
        }

        units = compressor._build_units(facts)

        self.assertEqual(units, [])

    def test_parallel_candidate_merge_keeps_multiple_candidates(self):
        compressor = self.make_compressor(max_workers=2, max_chunk_chars=400)
        facts = {
            "queries": [
                {"domain": "railway", "object": "train", "id": "G87", "pretty": "Candidate G87"},
                {"domain": "railway", "object": "train", "id": "G89", "pretty": "Candidate G89"},
            ],
            "analysis": [],
            "comparisons": [],
            "meta": {
                "warnings": [{"warning": "maintenance window"}],
                "errors": [],
                "chat_messages": [],
            },
        }

        result = compressor.compress("帮我找最快的车", facts)

        self.assertEqual(result["format"], "fast_fact_context_v1")
        self.assertEqual(result["source_stats"]["merged_candidate_count"], 2)
        self.assertEqual(result["candidates"][0]["candidate_key"], "train:G87")
        self.assertEqual(result["candidates"][1]["candidate_key"], "train:G89")
        self.assertTrue(
            any(obs["kind"] == "warning" for obs in result["observations"])
        )

    def test_large_fact_is_split_into_multiple_chunks(self):
        compressor = self.make_compressor(max_workers=2, max_chunk_chars=90)
        large_body = "\n".join(
            [
                "G87 benchmark candidate line one",
                "extra context line two",
                "extra context line three",
                "extra context line four",
                "extra context line five",
            ]
        )
        facts = {
            "queries": [
                {"domain": "railway", "object": "station_to_station_mini", "id": "南京南-福州", "pretty": large_body}
            ],
            "analysis": [],
            "comparisons": [],
            "meta": {"warnings": [], "errors": [], "chat_messages": []},
        }

        result = compressor.compress("这条线最快的候选有哪些", facts)

        self.assertGreater(result["source_stats"]["fact_chunk_count"], 1)
        self.assertTrue(
            any(item["candidate_key"] == "train:G87" for item in result["candidates"])
        )

    def test_raw_fallback_is_kept_when_chunk_parse_fails(self):
        compressor = self.make_compressor(max_workers=1, max_chunk_chars=300)
        facts = {
            "queries": [
                {"domain": "railway", "object": "train", "id": "BROKEN_JSON", "pretty": "BROKEN_JSON"}
            ],
            "analysis": [],
            "comparisons": [],
            "meta": {"warnings": [], "errors": [], "chat_messages": []},
        }

        result = compressor.compress("测试失败回退", facts)

        self.assertEqual(result["source_stats"]["raw_fallback_count"], 1)
        self.assertEqual(len(result["raw_fallbacks"]), 1)
        self.assertIn("BROKEN_JSON", result["raw_fallbacks"][0]["text"])

    def test_fast_views_replace_full_fact_serialization(self):
        compressor = self.make_compressor(max_workers=2, max_chunk_chars=240)
        facts = {
            "queries": [
                {
                    "domain": "railway",
                    "object": "s2s_benchmark",
                    "id": "南京南-徐州东",
                    "pretty": "BROKEN_JSON should be ignored once fast_views exist",
                    "fast_views": [
                        {
                            "view_id": "overview",
                            "view_type": "s2s_overview",
                            "priority": 120,
                            "text": "Top candidate summary mentions G87",
                        },
                        {
                            "view_id": "batch_01",
                            "view_type": "s2s_candidate_batch",
                            "priority": 100,
                            "text": "Secondary candidate summary mentions G89",
                        },
                    ],
                }
            ],
            "analysis": [],
            "comparisons": [],
            "meta": {"warnings": [], "errors": [], "chat_messages": []},
        }

        result = compressor.compress("帮我找最快的候选", facts)

        self.assertEqual(result["source_stats"]["fact_chunk_count"], 2)
        self.assertEqual(result["source_stats"]["raw_fallback_count"], 0)
        self.assertEqual(result["source_stats"]["retained_evidence_count"], 2)
        self.assertTrue(
            any(item["candidate_key"] == "train:G87" for item in result["candidates"])
        )
        self.assertTrue(
            any(item["candidate_key"] == "train:G89" for item in result["candidates"])
        )

    def test_seed_candidates_are_merged_into_fast_context(self):
        class EmptyLLM:
            def generate(self, messages):
                return json.dumps(
                    {"is_relevant": False, "candidates": [], "observations": []},
                    ensure_ascii=False,
                )

        compressor = FastFactCompressor(llm_factory=lambda: EmptyLLM(), max_workers=1, max_chunk_chars=240)
        facts = {
            "queries": [
                {
                    "domain": "railway",
                    "object": "s2s_benchmark",
                    "id": "南京南-徐州东",
                    "pretty": "small pretty",
                    "fast_candidates": [
                        {
                            "candidate_key": "train:G87",
                            "candidate_type": "train",
                            "label": "G87",
                            "score": 93,
                            "why": "Deterministic seed",
                            "supporting_points": ["duration=2h39m"],
                            "attributes": {"train_no": "G87"},
                        }
                    ],
                }
            ],
            "analysis": [],
            "comparisons": [],
            "meta": {"warnings": [], "errors": [], "chat_messages": []},
        }

        result = compressor.compress("帮我找最快的车", facts)

        self.assertEqual(result["source_stats"]["seed_candidate_count"], 1)
        self.assertTrue(
            any(item["candidate_key"] == "train:G87" for item in result["candidates"])
        )

    def test_cache_hit_skips_repeated_lane_generation(self):
        llm = FakeLLM()
        compressor = FastFactCompressor(llm_factory=lambda: llm, max_workers=1, max_chunk_chars=240)
        facts = {
            "queries": [
                {
                    "domain": "railway",
                    "object": "train",
                    "id": "G87",
                    "pretty": "Candidate G87",
                }
            ],
            "analysis": [],
            "comparisons": [],
            "meta": {"warnings": [], "errors": [], "chat_messages": []},
        }

        first = compressor.compress("测试缓存", facts)
        second = compressor.compress("测试缓存", facts)

        self.assertFalse(first["source_stats"]["cache_hit"])
        self.assertTrue(second["source_stats"]["cache_hit"])
        self.assertEqual(llm.generate_calls, 1)

    def test_deterministic_fast_views_skip_lane_llm(self):
        llm = FakeLLM()
        compressor = FastFactCompressor(llm_factory=lambda: llm, max_workers=2, max_chunk_chars=240)
        facts = {
            "queries": [
                {
                    "domain": "railway",
                    "object": "s2s_benchmark",
                    "id": "福州-深圳北",
                    "fast_views": [
                        {
                            "view_id": "overview",
                            "view_type": "s2s_overview",
                            "priority": 120,
                            "text": "OBJECT: s2s_benchmark\nQUERY_ID: 福州-深圳北\nVIEW_TYPE: s2s_overview\nVIEW_ID: overview\nTOP1 G87 duration 4h12m",
                        },
                        {
                            "view_id": "batch_01",
                            "view_type": "s2s_candidate_batch",
                            "priority": 100,
                            "text": "OBJECT: s2s_benchmark\nQUERY_ID: 福州-深圳北\nVIEW_TYPE: s2s_candidate_batch\nVIEW_ID: batch_01\n- G87\n- G89",
                        },
                    ],
                    "fast_candidates": [
                        {
                            "candidate_key": "train:G87",
                            "candidate_type": "train",
                            "label": "G87",
                            "score": 95,
                            "why": "Deterministic top candidate.",
                            "supporting_points": ["duration=4h12m"],
                            "attributes": {"train_no": "G87", "route": "福州-深圳北"},
                        }
                    ],
                }
            ],
            "analysis": [],
            "comparisons": [],
            "meta": {"warnings": [], "errors": [], "chat_messages": []},
        }

        result = compressor.compress("福州到深圳北最快怎么坐", facts)

        self.assertEqual(llm.generate_calls, 0)
        self.assertTrue(result["source_stats"]["deterministic_only"])
        self.assertEqual(result["strategy"], "deterministic_fast_views_seed_merge")
        self.assertTrue(result["observations"])
        self.assertEqual(result["candidates"][0]["candidate_key"], "train:G87")

    def test_live_v2_views_still_use_lane_compression(self):
        llm = FakeLLM()
        compressor = FastFactCompressor(llm_factory=lambda: llm, max_workers=2, max_chunk_chars=240)
        facts = {
            "queries": [
                {
                    "domain": "railway",
                    "object": "train_delay",
                    "id": "G1",
                    "fast_views": [
                        {
                            "view_id": "overview",
                            "view_type": "train_delay_overview",
                            "priority": 130,
                            "text": "OBJECT: train_delay\nQUERY_ID: G1\nVIEW_TYPE: train_delay_overview\nSTATION_COUNT: 5",
                        },
                        {
                            "view_id": "delay_batch_01",
                            "view_type": "train_delay_station_batch",
                            "priority": 115,
                            "text": "OBJECT: train_delay\nQUERY_ID: G1\nVIEW_TYPE: train_delay_station_batch\n- station=北京南 status=正点",
                        },
                    ],
                }
            ],
            "analysis": [],
            "comparisons": [],
            "meta": {"warnings": [], "errors": [], "chat_messages": []},
        }

        result = compressor.compress("G1现在正点吗", facts)

        self.assertGreater(llm.generate_calls, 0)
        self.assertFalse(result["source_stats"]["deterministic_only"])
        self.assertEqual(result["strategy"], "fast_views_pack_to_6_lanes_parallel_extract_then_merge")


if __name__ == "__main__":
    unittest.main()
