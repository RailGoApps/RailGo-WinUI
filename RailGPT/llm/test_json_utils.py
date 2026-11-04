import json
import unittest

from llm.json_utils import loads_llm_json


class LLMJsonUtilsTest(unittest.TestCase):
    def test_loads_plain_json(self):
        self.assertEqual(loads_llm_json('{"intent":"chat"}'), {"intent": "chat"})

    def test_loads_markdown_fenced_json(self):
        raw = '```json\n{"intent":"memory_profile_chat"}\n```'
        self.assertEqual(
            loads_llm_json(raw),
            {"intent": "memory_profile_chat"},
        )

    def test_loads_json_surrounded_by_short_explanation(self):
        raw = 'Result follows:\n{"confidence":0.97}\nEnd.'
        self.assertEqual(loads_llm_json(raw), {"confidence": 0.97})

    def test_invalid_payload_still_raises(self):
        with self.assertRaises(json.JSONDecodeError):
            loads_llm_json("not structured output")


if __name__ == "__main__":
    unittest.main()
