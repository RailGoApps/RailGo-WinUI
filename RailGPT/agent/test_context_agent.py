import unittest

from agent.context_agent import FastPlusContextAgent
from memory.session import SessionMemory


class DummyContextLLM:
    def __init__(self, raw=None, error=None):
        self.raw = raw or "{}"
        self.error = error
        self.calls = 0
        self.last_messages = None
        self.last_timeout = None
        self.last_max_retries = None

    def generate(self, messages, timeout=None, max_retries=None):
        self.calls += 1
        self.last_messages = messages
        self.last_timeout = timeout
        self.last_max_retries = max_retries
        if self.error:
            raise self.error
        return self.raw


class FastPlusContextAgentTest(unittest.TestCase):
    def test_timeout_falls_back_to_heuristic_train_overview(self):
        session = SessionMemory()
        session.update_anchor(train="G20", date="2026-03-27", query_type="path_detail", query_object="path_detail")

        agent = FastPlusContextAgent(llm=DummyContextLLM(error=TimeoutError("slow")))
        agent.set_mode_profile("fast-go")
        result = agent.prepare("I WANNA KNOW EVERYTHING ABOUT G20", session=session)

        self.assertEqual(result["intent_category"], "train_overview")
        self.assertIn("G20", result["resolved_train_numbers"])
        self.assertEqual(result["resolved_date"], "2026-03-27")
        self.assertGreaterEqual(result["confidence"], 80)

    def test_prepare_uses_cache_for_same_context(self):
        session = SessionMemory()
        session.update_anchor(train="G20", date="2026-03-27", query_type="path_detail", query_object="path_detail")

        llm = DummyContextLLM(
            raw=(
                '{"intent_category":"train_path","rewritten_user_text":"query G20",'
                '"resolved_route":"","resolved_train_numbers":["G20"],'
                '"resolved_emu":"","resolved_date":"2026-03-27",'
                '"resolved_station_mentions":[],"resolved_query_object":"path_detail",'
                '"confidence":91,"reason":"ok"}'
            )
        )
        agent = FastPlusContextAgent(llm=llm)

        first = agent.prepare("G20 route?", session=session)
        second = agent.prepare("G20 route?", session=session)

        self.assertEqual(first["intent_category"], "train_path")
        self.assertEqual(second["intent_category"], "train_path")
        self.assertEqual(llm.calls, 1)

    def test_prepare_disables_retries_for_context_agent_call(self):
        session = SessionMemory()
        session.update_anchor(train="G20", date="2026-03-27", query_type="path_detail", query_object="path_detail")

        llm = DummyContextLLM(
            raw=(
                '{"intent_category":"train_path","rewritten_user_text":"query G20",'
                '"resolved_route":"","resolved_train_numbers":["G20"],'
                '"resolved_emu":"","resolved_date":"2026-03-27",'
                '"resolved_station_mentions":[],"resolved_query_object":"path_detail",'
                '"confidence":91,"reason":"ok"}'
            )
        )
        agent = FastPlusContextAgent(llm=llm)

        agent.prepare("G20 route?", session=session)

        self.assertEqual(llm.last_timeout, agent.request_timeout_s)
        self.assertEqual(llm.last_max_retries, 0)

    def test_prompt_tells_context_agent_to_preserve_latest_user_explicit_date(self):
        session = SessionMemory()
        session.update_anchor(route="福州-南京南", date="2026-04-27", query_type="left_ticket_s2s", query_object="left_ticket_s2s")

        llm = DummyContextLLM(
            raw=(
                '{"intent_category":"route_ticket",'
                '"rewritten_user_text":"请查询福州-南京南在2026-05-05的余票情况",'
                '"resolved_route":"福州-南京南","resolved_train_numbers":[],'
                '"resolved_emu":"","resolved_date":"2026-05-05",'
                '"resolved_station_mentions":[],"resolved_query_object":"left_ticket_s2s",'
                '"confidence":70,"reason":"ok"}'
            )
        )
        agent = FastPlusContextAgent(llm=llm)

        result = agent.prepare("福州到南京南2026-05-05还有哪些车有余票？", session=session)
        prompt_text = "\n".join(message.get("content", "") for message in llm.last_messages)

        self.assertEqual(result["resolved_date"], "2026-05-05")
        self.assertEqual(result["reason"], "ok")
        self.assertIn("Latest user turn to normalize, verbatim", prompt_text)
        self.assertIn("福州到南京南2026-05-05还有哪些车有余票？", prompt_text)
        self.assertIn("Never replace an explicit user date with today", prompt_text)

    def test_heuristic_fallback_preserves_user_text_instead_of_injecting_anchor_date(self):
        session = SessionMemory()
        session.update_anchor(route="福州-南京南", date="2026-04-27", query_type="left_ticket_s2s", query_object="left_ticket_s2s")

        agent = FastPlusContextAgent(llm=DummyContextLLM(error=TimeoutError("slow")))
        result = agent.prepare("福州到南京南2026-05-05还有哪些车有余票？", session=session)

        self.assertEqual(result["intent_category"], "route_ticket")
        self.assertEqual(result["resolved_route"], "福州-南京南")
        self.assertEqual(result["resolved_date"], "")
        self.assertIn("2026-05-05", result["rewritten_user_text"])
        self.assertNotIn("今天", result["rewritten_user_text"])
        self.assertNotIn("2026-04-27", result["rewritten_user_text"])

    def test_timeout_falls_back_to_general_rail_knowledge_heuristic(self):
        session = SessionMemory()

        agent = FastPlusContextAgent(llm=DummyContextLLM(error=TimeoutError("slow")))
        result = agent.prepare("什么是CTCS-3？", session=session)

        self.assertEqual(result["intent_category"], "general_rail")
        self.assertEqual(result["resolved_query_object"], "general_rail")
        self.assertGreaterEqual(result["confidence"], 80)


if __name__ == "__main__":
    unittest.main()
