import unittest

from agent.fast_coordinator import FastCoordinator


class FakeLLM:
    def get_mode(self):
        return "fast"


class FakeRAG:
    def __init__(self):
        self.retrieve_calls = 0

    def retrieve(self, user_text, facts=None, context_bundle=None, top_k=4):
        self.retrieve_calls += 1
        return {
            "docs": [
                {
                    "title": "南京南车站档案",
                    "score": 0.92,
                    "reasons": ["entity=station:南京南"],
                    "snippet": "南京南电报码为 NKH。",
                }
            ]
        }

    def build_prompt_context_from_result(self, result):
        return "Knowledge-Augmented Railway Context\n[RAG-1] 南京南车站档案"


class FakeAnswerGenerator:
    def __init__(self):
        self.llm = FakeLLM()
        self.knowledge_rag = FakeRAG()
        self.context_calls = 0

    def is_fast_mode(self):
        return True

    def build_context_bundle(self, user_text, facts):
        self.context_calls += 1
        return {
            "format": "fast_fact_context_v1",
            "candidates": [{"candidate_key": "train:G84", "label": "G84", "score": 99}],
            "observations": [],
            "retained_evidence": [],
            "source_stats": {"merged_candidate_count": 1, "raw_fallback_count": 0},
        }

    def _strip_fast_only_fields(self, value):
        return value


class FastCoordinatorTest(unittest.TestCase):
    def test_prepare_builds_parallel_assets_and_caches(self):
        answer_generator = FakeAnswerGenerator()
        coordinator = FastCoordinator(answer_generator, cache_size=8)
        facts = {
            "queries": [
                {"domain": "railway", "object": "s2s_benchmark", "id": "南京南-徐州东"}
            ],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }

        first = coordinator.prepare("南京南到徐州东最快的车", facts)
        second = coordinator.prepare("南京南到徐州东最快的车", facts)

        self.assertEqual(answer_generator.context_calls, 1)
        self.assertEqual(answer_generator.knowledge_rag.retrieve_calls, 0)
        self.assertFalse(first.stats["cache_hit"])
        self.assertTrue(second.stats["cache_hit"])
        self.assertEqual(second.rag_context, first.rag_context)
        self.assertEqual(first.rag_context, "")
        self.assertEqual(first.presentation_plan["answer_shape"], "ranked_trains")
        self.assertIn("ranking", first.presentation_plan["sections"])
        self.assertEqual(first.context_bundle["format"], "fast_fact_context_v1")

    def test_prepare_keeps_rag_for_static_lookup_queries(self):
        answer_generator = FakeAnswerGenerator()
        coordinator = FastCoordinator(answer_generator, cache_size=8)
        facts = {
            "queries": [
                {"domain": "railway", "object": "telecode", "id": "南京南"}
            ],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }

        result = coordinator.prepare("南京南电报码是什么", facts)

        self.assertEqual(answer_generator.context_calls, 1)
        self.assertEqual(answer_generator.knowledge_rag.retrieve_calls, 1)
        self.assertIn("Knowledge-Augmented Railway Context", result.rag_context)
        self.assertEqual(result.presentation_plan["answer_shape"], "lookup_card")


if __name__ == "__main__":
    unittest.main()
