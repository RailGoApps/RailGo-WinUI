import unittest
from unittest.mock import patch

from agent.app import RailwayAgentApp
from agent.executor import Executor


class _FastAnswerGenStub:
    def is_fast_mode(self):
        return True


class ExecutorSmartEMUAnalysisTest(unittest.TestCase):
    def test_execute_smartemu_analysis_returns_positive_query_evidence(self):
        fake_results = [
            {
                "train": "G7",
                "inference": {
                    "candidate": "CR400BFS",
                    "confidence": "HIGH",
                    "prob_top3": [("CR400BFS", 0.74), ("CR400AFBS", 0.19)],
                    "recent5": ["CR400BFS", "CR400BFS", "CR400BFS"],
                    "stability": "very_stable_streak",
                    "warning": None,
                    "records_used": 18,
                },
            },
            {
                "train": "G20",
                "inference": {
                    "candidate": "CR400AFBS",
                    "confidence": "MEDIUM",
                    "prob_top3": [("CR400AFBS", 0.53), ("CR400BFA", 0.31)],
                    "recent5": ["CR400AFBS", "CR400BFA", "CR400AFBS"],
                    "stability": "stable_recent5",
                    "warning": None,
                    "records_used": 21,
                },
            },
            {
                "train": "G33",
                "inference": {
                    "candidate": "CR400AF",
                    "confidence": "LOW",
                    "prob_top3": [("CR400AF", 0.48), ("CR400AFBS", 0.22)],
                    "recent5": ["CR400AF", "CR400AF", "CR400AFBS"],
                    "stability": "unstable",
                    "warning": None,
                    "records_used": 16,
                },
            },
        ]

        with patch("agent.executor.SmartEMUAnalyzer") as analyzer_cls:
            analyzer_cls.return_value.execute.return_value = fake_results

            executor = Executor(psw=None)
            facts = executor.execute(
                [
                    {
                        "action": "query",
                        "params": {
                            "domain": "railway",
                            "object": "smartemu_analysis",
                            "id": "G7,G20,G33",
                        },
                    }
                ]
            )

        self.assertEqual(len(facts["queries"]), 1)
        item = facts["queries"][0]
        self.assertEqual(item["object"], "smartemu_analysis")
        self.assertEqual(item["id"], "G7,G20,G33")
        self.assertEqual(item["results"], fake_results)
        self.assertIn("智能动车使用分析", item["pretty"])
        self.assertIn("G7", item["pretty"])
        self.assertIn("G20", item["pretty"])
        self.assertIn("G33", item["pretty"])
        self.assertTrue(item.get("fast_views"))
        self.assertTrue(item.get("fast_candidates"))

    def test_app_prepares_fast_assets_for_smartemu_analysis_pretty_evidence(self):
        app = RailwayAgentApp.__new__(RailwayAgentApp)
        app.answer_gen = _FastAnswerGenStub()

        facts = {
            "queries": [
                {
                    "object": "smartemu_analysis",
                    "pretty": "智能动车使用分析（概率工具）\n1. G7\n   - 最可能担当族型：CR400BFS",
                }
            ],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }

        self.assertTrue(app._should_prepare_fast_assets(facts))
