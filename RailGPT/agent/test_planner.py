import unittest

from agent.planner import Planner


class PlannerDateFillTest(unittest.TestCase):
    def test_build_plan_from_request_fills_missing_date_from_existing_facts(self):
        planner = Planner()
        facts = {
            "queries": [
                {
                    "domain": "railway",
                    "object": "s2s_benchmark",
                    "id": "南京南-徐州东",
                    "date": "2026-03-24",
                }
            ],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }
        extra_request = {
            "missing": [
                {
                    "domain": "railway",
                    "object": "s2s_benchmark",
                    "id": "南京南-徐州东",
                }
            ]
        }

        plan = planner.build_plan_from_request(extra_request, facts)

        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["action"], "query")
        self.assertEqual(plan[0]["params"]["date"], "2026-03-24")


if __name__ == "__main__":
    unittest.main()
