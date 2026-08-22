import re
import unittest
from pathlib import Path

from agent.router import Router
from memory.session import SessionMemory


class DummyLLM:
    def __init__(self, mode="fast", response=None):
        self.mode = mode
        self.response = response or '{"action":"chat","params":{"message":"fallback railway knowledge answer"}}'
        self.generate_called = 0

    def set_mode(self, mode):
        self.mode = mode

    def get_mode(self):
        return self.mode

    def generate(self, messages):
        self.generate_called += 1
        return self.response


class DummyContextAgent:
    def __init__(self):
        self.calls = 0

    def prepare(self, user_text, session=None):
        self.calls += 1
        return {
            "intent_category": "unknown",
            "rewritten_user_text": str(user_text or "").strip(),
            "resolved_route": "",
            "resolved_train_numbers": [],
            "resolved_emu": "",
            "resolved_date": "",
            "resolved_station_mentions": [],
            "resolved_query_object": "",
            "confidence": 0,
            "reason": "pressure-test stub",
        }


def load_railfan_questions():
    path = Path(__file__).resolve().parents[1] / "火车迷50问.txt"
    text = path.read_text(encoding="utf-8")
    return re.findall(r"^\s*\d+\.\s*(.+?)\s*$", text, flags=re.MULTILINE)


class Railfan50PressureTest(unittest.TestCase):
    def make_router(self):
        memory = SessionMemory()
        router = Router(memory)
        router.set_mode("fast-plus")
        router.llm = DummyLLM(mode="fast")
        router.context_agent = DummyContextAgent()
        return router, memory

    def test_railfan_50_questions_remain_stable_offline(self):
        questions = load_railfan_questions()
        self.assertEqual(len(questions), 50)

        counts = {"chat": 0, "query": 0, "pending": 0}
        compact_llm_hits = []
        bad_actions = []
        generic_pending = []
        context_agent_hits = 0

        for idx, question in enumerate(questions, start=1):
            router, memory = self.make_router()
            tasks = router.route(question, memory)

            action = tasks[0].get("action") if tasks else None
            if action in counts:
                counts[action] += 1
            else:
                bad_actions.append((idx, question, tasks))

            if router.llm.generate_called:
                compact_llm_hits.append((idx, question, action))
            if router.context_agent.calls:
                context_agent_hits += router.context_agent.calls

            if action == "pending":
                pending_question = str(tasks[0].get("params", {}).get("question") or "")
                if any(fragment in pending_question for fragment in ("请补充一下", "我还需要你补充", "才能继续查询")):
                    generic_pending.append((idx, question, pending_question))

        print("\nRailfan50 distribution:", counts)
        print("Railfan50 compact-llm hits:", [item[0] for item in compact_llm_hits[:12]])
        print("Railfan50 context-agent hits:", context_agent_hits)

        self.assertFalse(bad_actions, f"unexpected actions: {bad_actions[:3]}")
        self.assertFalse(generic_pending, f"generic pending samples: {generic_pending[:3]}")
        self.assertGreater(counts["chat"], 0)
        self.assertGreater(counts["query"], 0)


if __name__ == "__main__":
    unittest.main()
