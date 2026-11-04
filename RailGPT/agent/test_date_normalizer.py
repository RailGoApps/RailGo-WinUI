import unittest

from agent.date_normalizer import DateNormalizerAgent
from memory.session import SessionMemory


class DummyLLM:
    def __init__(self, raw="{}", error=None):
        self.raw = raw
        self.error = error
        self.calls = 0

    def generate(self, messages, timeout=None, max_retries=None):
        self.calls += 1
        if self.error:
            raise self.error
        return self.raw


class DateNormalizerAgentTest(unittest.TestCase):
    def test_latest_user_explicit_date_does_not_need_llm(self):
        llm = DummyLLM()
        agent = DateNormalizerAgent(llm=llm)

        result = agent.normalize(
            latest_user_text="福州到南京南5.5号还有哪些车有余票？",
            current_date="2026-05-02",
            mode="fast-go",
        )

        self.assertEqual(result["normalized_date"], "2026-05-05")
        self.assertEqual(result["date_source"], "latest_user")
        self.assertEqual(llm.calls, 0)

    def test_fast_go_parses_chinese_month_and_day_without_llm(self):
        llm = DummyLLM()
        agent = DateNormalizerAgent(llm=llm)

        result = agent.normalize(
            latest_user_text="五月五号从福州到南京南还有余票吗？",
            current_date="2026-04-28",
            mode="fast-go",
        )

        self.assertEqual(result["normalized_date"], "2026-05-05")
        self.assertEqual(result["date_source"], "latest_user")
        self.assertEqual(result["date_span"], "五月五号")
        self.assertEqual(llm.calls, 0)

    def test_fast_plus_explicit_date_is_resolved_by_llm(self):
        llm = DummyLLM(
            raw=(
                '{"has_date":true,"normalized_date":"2026-05-05",'
                '"date_source":"latest_user","date_span":"5.5号",'
                '"is_contextual_date":false,"confidence":96,"reason":"explicit user date"}'
            )
        )
        agent = DateNormalizerAgent(llm=llm)

        result = agent.normalize(
            latest_user_text="福州到南京南5.5号还有哪些车有余票？",
            current_date="2026-05-02",
            mode="fast-plus",
        )

        self.assertEqual(result["normalized_date"], "2026-05-05")
        self.assertEqual(result["date_source"], "latest_user")
        self.assertEqual(llm.calls, 1)

    def test_fast_plus_does_not_regex_fallback_when_llm_fails(self):
        llm = DummyLLM(error=TimeoutError("date llm down"))
        agent = DateNormalizerAgent(llm=llm)

        result = agent.normalize(
            latest_user_text="福州到南京南5.5号还有哪些车有余票？",
            current_date="2026-05-02",
            mode="fast-plus",
        )

        self.assertFalse(result["has_date"])
        self.assertEqual(result["normalized_date"], "")
        self.assertEqual(llm.calls, 1)

    def test_contextual_that_day_can_reuse_session_date_without_guessing_today(self):
        session = SessionMemory()
        session.update_anchor(date="2026-05-05", route="福州-南京南")
        agent = DateNormalizerAgent(llm=DummyLLM())

        result = agent.normalize(
            latest_user_text="那天还有票吗？",
            session=session,
            current_date="2026-05-02",
        )

        self.assertEqual(result["normalized_date"], "2026-05-05")
        self.assertEqual(result["date_source"], "conversation_context")
        self.assertTrue(result["is_contextual_date"])

    def test_next_weekday_uses_beijing_current_date(self):
        agent = DateNormalizerAgent(llm=DummyLLM())

        result = agent.normalize(
            latest_user_text="下周一南京南到徐州东还有票吗？",
            current_date="2026-03-24",
        )

        self.assertEqual(result["normalized_date"], "2026-03-30")
        self.assertEqual(result["date_source"], "relative_user")


if __name__ == "__main__":
    unittest.main()
