import json
import unittest

from agent.coach_media_resolver import CoachMediaResolverAgent


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload

    def set_mode(self, _mode):
        return None

    def generate(self, _messages, **_kwargs):
        return json.dumps(self.payload, ensure_ascii=False)


CATALOG = [
    {"kind": "whole_train", "selector": "default", "label": "整列总图"},
    {"kind": "coach", "selector": "08", "label": "08车 二等座"},
    {"kind": "interior", "selector": "商务座#鱼骨式", "label": "商务座#鱼骨式"},
]


class CoachMediaResolverTest(unittest.TestCase):
    def test_llm_can_select_exact_real_target(self):
        resolver = CoachMediaResolverAgent(FakeLLM({
            "presentation_mode": "coach", "selector": "08", "coach_number": "08",
            "seat_type": "", "confidence": 98, "reason": "explicit",
        }))
        result = resolver.resolve("给我看G1的8号车厢图", CATALOG)
        self.assertEqual(result["presentation_mode"], "coach")
        self.assertEqual(result["selector"], "08")

    def test_invented_selector_is_rejected_and_fallback_is_safe(self):
        resolver = CoachMediaResolverAgent(FakeLLM({
            "presentation_mode": "coach", "selector": "99", "confidence": 99,
        }))
        result = resolver.resolve("给我看G1的8号车厢图", CATALOG)
        self.assertEqual(result["selector"], "08")

    def test_vague_interior_request_clarifies(self):
        resolver = CoachMediaResolverAgent(FakeLLM({
            "presentation_mode": "clarify", "selector": "", "confidence": 95,
        }))
        self.assertEqual(resolver.resolve("看看内部图", CATALOG)["presentation_mode"], "clarify")


if __name__ == "__main__":
    unittest.main()
