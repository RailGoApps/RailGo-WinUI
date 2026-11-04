import queue
import unittest

from agent.app import RailwayAgentApp
from agent.psw import AgentState
from memory.session import SessionMemory


class DummyAnswerGen:
    def __init__(self, fast=True):
        self._fast = fast
        self.mode_profile = "fast-go" if fast else "deep"
        self.stream_calls = 0
        self.pending_stream_calls = 0
        self.pending_chunks = ()

    def is_fast_mode(self):
        return self._fast

    def set_mode_profile(self, mode):
        self.mode_profile = mode
        self._fast = mode != "deep"

    def build_messages(self, **kwargs):
        return [{"role": "system", "content": "chat"}]

    def stream_final(self, messages):
        self.stream_calls += 1
        return iter(("A", "B"))

    def stream_pending_question(self, **kwargs):
        self.pending_stream_calls += 1
        return iter(self.pending_chunks)

    def should_use_fast_direct_final(self, **kwargs):
        return False

    def force_finalize(self, **kwargs):
        return "best effort final answer"


class FailingStreamAnswerGen(DummyAnswerGen):
    def stream_final(self, messages):
        self.stream_calls += 1

        def _iter():
            yield "partial"
            raise RuntimeError("stream transport interrupted")

        return _iter()


class FailingPendingStreamAnswerGen(DummyAnswerGen):
    def stream_pending_question(self, **kwargs):
        self.pending_stream_calls += 1

        def _iter():
            raise RuntimeError("pending stream unavailable")
            yield ""

        return _iter()


class DummyModeTarget:
    def __init__(self):
        self.modes = []

    def set_mode(self, mode):
        self.modes.append(mode)


class DummyPSW:
    def __init__(self):
        self.events = []

    def set_state(self, state, detail=""):
        self.events.append((state, detail))


class DummyFastAssets:
    def __init__(self, context_bundle=None, rag_context="", presentation_plan=None):
        self.context_bundle = context_bundle or {"mode": "fast-compact", "source_stats": {"merged_candidate_count": 1}}
        self.rag_context = rag_context
        self.presentation_plan = presentation_plan or {}


class AppFastAssetsGateTest(unittest.TestCase):
    def make_app(self, fast=True):
        app = RailwayAgentApp.__new__(RailwayAgentApp)
        app.answer_gen = DummyAnswerGen(fast=fast)
        app._record_turn_memory = lambda *args, **kwargs: None
        app.psw = None
        app.router = DummyModeTarget()
        app.thinking_engine = type("Thinking", (), {"llm": DummyModeTarget()})()
        return app

    def test_skip_fast_assets_when_only_placeholder_queries_exist(self):
        app = self.make_app(fast=True)
        facts = {
            "queries": [{"type": "query_empty", "key": "railway:s2s:bad"}],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }
        self.assertFalse(app._should_prepare_fast_assets(facts))

    def test_prepare_fast_assets_when_reliable_query_exists(self):
        app = self.make_app(fast=True)
        facts = {
            "queries": [{"object": "s2s_benchmark", "pretty": "ok"}],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }
        self.assertTrue(app._should_prepare_fast_assets(facts))

    def test_chat_facts_store_route_tags_without_internal_router_prompt(self):
        app = self.make_app(fast=True)
        facts = app._build_chat_facts([
            {
                "action": "chat",
                "params": {
                    "message": "Dedicated contextual social chat route: respond warmly and do not restart slot filling."
                },
            }
        ])

        meta = facts["meta"]
        self.assertEqual(meta["chat_messages"], [])
        self.assertEqual(meta["chat_route_tags"], ["contextual_social"])

    def test_finish_with_pending_humanizes_generic_slot_prompt(self):
        app = self.make_app(fast=True)
        session = SessionMemory()

        events = app._finish_with_pending(
            session=session,
            user_text="帮我查余票",
            question="我还需要你补充一下信息才能继续",
            pending_params={"slot": ["dep", "arr"]},
        )

        self.assertEqual(events[0]["type"], "pending")
        self.assertIn("出发站", events[0]["text"])
        self.assertIn("到达站", events[0]["text"])
        self.assertTrue(session.in_followup())
        self.assertEqual(session.followup_slots["slot"], ["dep", "arr"])

    def test_finish_with_pending_streams_llm_generated_clarification(self):
        app = self.make_app(fast=True)
        app.answer_gen.pending_chunks = ("Need ", "origin and destination.")
        session = SessionMemory()

        events = list(app._finish_with_pending(
            session=session,
            user_text="help me continue",
            question="please provide missing information",
            pending_params={"slot": ["dep", "arr"]},
        ))

        pending_events = [event for event in events if event["type"] == "pending"]
        self.assertEqual([event["text"] for event in pending_events], ["Need ", "origin and destination."])
        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["text"], "Need origin and destination.")
        self.assertEqual(app.answer_gen.pending_stream_calls, 1)

    def test_finish_with_pending_falls_back_when_pending_stream_fails(self):
        app = RailwayAgentApp.__new__(RailwayAgentApp)
        app.answer_gen = FailingPendingStreamAnswerGen(fast=True)
        app._record_turn_memory = lambda *args, **kwargs: None
        app.psw = None
        app.router = DummyModeTarget()
        app.thinking_engine = type("Thinking", (), {"llm": DummyModeTarget()})()
        session = SessionMemory()

        events = list(app._finish_with_pending(
            session=session,
            user_text="help me continue",
            question="please provide missing information",
            pending_params={"slot": ["dep", "arr"]},
        ))

        pending_text = "".join(event["text"] for event in events if event["type"] == "pending")
        self.assertTrue(pending_text)
        self.assertEqual(events[-1]["text"], pending_text)
        self.assertEqual(app.answer_gen.pending_stream_calls, 1)

    def test_suspend_followup_for_chat_and_restore(self):
        app = self.make_app(fast=True)
        session = SessionMemory()
        session.enter_followup(question="请补充出发站", slot=["dep"])

        snapshot = app._suspend_followup_for_chat(
            session,
            [{"action": "chat", "params": {"message": "chat"}}],
        )

        self.assertIsNotNone(snapshot)
        self.assertFalse(session.in_followup())

        app._restore_followup(session, snapshot)
        self.assertTrue(session.in_followup())
        self.assertEqual(session.followup_slots["slot"], ["dep"])

    def test_on_memory_event_forwards_to_psw(self):
        app = self.make_app(fast=True)
        app.psw = DummyPSW()

        app._on_memory_event("MEMORY_READY", "memory rag ready session=2 episodic=1 long_term=0")

        self.assertEqual(app.psw.events[0][0], AgentState.MEMORY_READY)
        self.assertIn("session=2", app.psw.events[0][1])

    def test_set_mode_uses_fast_plus_thinking_profile(self):
        app = self.make_app(fast=True)

        app.set_mode("fast-plus")

        self.assertEqual(app.answer_gen.mode_profile, "fast-plus")
        self.assertEqual(app.router.modes[-1], "fast-plus")
        self.assertEqual(app.thinking_engine.llm.modes[-1], "fast-plus")

    def test_pure_chat_route_goes_directly_to_final_chat_without_planner(self):
        app = RailwayAgentApp.__new__(RailwayAgentApp)
        app.answer_gen = DummyAnswerGen(fast=True)
        app._record_turn_memory = lambda *args, **kwargs: None
        app.memory_manager = None
        app.fast_coordinator = None
        app.max_rounds = 1
        app.psw = DummyPSW()
        app._thinking_queue = queue.Queue()
        app.router = type(
            "Router",
            (),
            {"route": lambda self, user_text, session: [{"action": "chat", "params": {"message": "Dedicated chat route"}}]},
        )()
        app.planner = type("Planner", (), {"build_plan": lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("planner should not be called"))})()
        app.executor = type("Executor", (), {"dialog_seen_queries": set()})()
        app.thinking_engine = type("Thinking", (), {"start_user_thinking": lambda *args, **kwargs: None, "llm": DummyModeTarget()})()

        session = SessionMemory()
        events = list(app._run_core_loop("what is this", session))

        self.assertEqual(events[0], {"type": "token", "text": "A"})
        self.assertEqual(events[1], {"type": "token", "text": "B"})
        self.assertTrue(any(event["type"] == "token" for event in events[:-1]))
        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["text"], "AB")
        self.assertEqual(app.answer_gen.stream_calls, 1)

    def test_pure_chat_route_can_stream_static_direct_reply_without_llm(self):
        app = RailwayAgentApp.__new__(RailwayAgentApp)
        app.answer_gen = DummyAnswerGen(fast=True)
        app._record_turn_memory = lambda *args, **kwargs: None
        app.memory_manager = None
        app.fast_coordinator = None
        app.max_rounds = 1
        app.psw = DummyPSW()
        app._thinking_queue = queue.Queue()
        app.router = type(
            "Router",
            (),
            {
                "route": lambda self, user_text, session: [
                    {
                        "action": "chat",
                        "params": {
                            "message": "Dedicated directional speed easter-egg route",
                            "direct_reply": "南北一般更快。哈基米南北绿豆。",
                        },
                    }
                ]
            },
        )()
        app.planner = type("Planner", (), {"build_plan": lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("planner should not be called"))})()
        app.executor = type("Executor", (), {"dialog_seen_queries": set()})()
        app.thinking_engine = type("Thinking", (), {"start_user_thinking": lambda *args, **kwargs: None, "llm": DummyModeTarget()})()

        session = SessionMemory()
        events = list(app._run_core_loop("东西方向的列车更快还是南北方向的更快", session))

        self.assertTrue(any(event["type"] == "token" for event in events[:-1]))
        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["text"], "南北一般更快。哈基米南北绿豆。")
        self.assertEqual(app.answer_gen.stream_calls, 0)

    def test_streaming_exception_is_not_serialized_into_final_message(self):
        app = RailwayAgentApp.__new__(RailwayAgentApp)
        app.answer_gen = FailingStreamAnswerGen(fast=True)
        app._record_turn_memory = lambda *args, **kwargs: None
        app.memory_manager = None
        app.fast_coordinator = None
        app.max_rounds = 1
        app.psw = DummyPSW()
        app._thinking_queue = queue.Queue()
        app.router = type(
            "Router",
            (),
            {"route": lambda self, user_text, session: [{"action": "chat", "params": {"message": "Dedicated chat route"}}]},
        )()
        app.planner = type("Planner", (), {"build_plan": lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("planner should not be called"))})()
        app.executor = type("Executor", (), {"dialog_seen_queries": set()})()
        app.thinking_engine = type("Thinking", (), {"start_user_thinking": lambda *args, **kwargs: None, "llm": DummyModeTarget()})()

        session = SessionMemory()
        with self.assertRaisesRegex(RuntimeError, "stream transport interrupted"):
            list(app._run_core_loop("what is this", session))

    def test_fast_loop_guard_forces_finalize_after_repeated_need_more_facts_without_progress(self):
        class LoopingAnswerGen(DummyAnswerGen):
            def __init__(self):
                super().__init__(fast=True)
                self.structured_calls = 0
                self.force_finalize_calls = 0

            def generate_structured(self, **kwargs):
                self.structured_calls += 1
                return {
                    "type": "need_more_facts",
                    "extra_request": {
                        "missing": [
                            {
                                "domain": "railway",
                                "object": "train",
                                "id": "G20",
                            }
                        ]
                    },
                }

            def force_finalize(self, **kwargs):
                self.force_finalize_calls += 1
                return "best effort final answer"

        class LoopingExecutor:
            def __init__(self):
                self.dialog_seen_queries = set()
                self.calls = 0

            def execute(self, plan):
                self.calls += 1
                if self.calls == 1:
                    return {
                        "queries": [
                            {
                                "object": "left_ticket_s2s",
                                "id": "NANJINGNAN-XUZHOUDONG",
                                "date": "2026-03-31",
                                "pretty": "G20 available",
                            }
                        ],
                        "analysis": [],
                        "comparisons": [],
                        "meta": {"errors": [], "warnings": [], "chat_messages": []},
                    }
                return {
                    "queries": [],
                    "analysis": [],
                    "comparisons": [],
                    "meta": {"errors": [], "warnings": [], "chat_messages": []},
                }

        app = RailwayAgentApp.__new__(RailwayAgentApp)
        app.answer_gen = LoopingAnswerGen()
        app._record_turn_memory = lambda *args, **kwargs: None
        app.memory_manager = None
        app.fast_coordinator = type("FastCoordinator", (), {"prepare": lambda *args, **kwargs: DummyFastAssets()})()
        app.max_rounds = 5
        app.psw = DummyPSW()
        app._thinking_queue = queue.Queue()
        app.router = type(
            "Router",
            (),
            {
                "route": lambda self, user_text, session: [
                    {
                        "action": "query",
                        "params": {
                            "domain": "railway",
                            "object": "left_ticket_s2s",
                            "id": "NANJINGNAN-XUZHOUDONG",
                            "date": "2026-03-31",
                        },
                    }
                ],
                "recover_fast_tasks": lambda *args, **kwargs: None,
            },
        )()
        app.planner = type(
            "Planner",
            (),
            {
                "build_plan": lambda self, tasks: tasks,
                "build_plan_from_request": lambda self, extra_request, facts: [
                    {
                        "action": "query",
                        "params": {
                            "domain": "railway",
                            "object": "train",
                            "id": "G20",
                        },
                    }
                ],
            },
        )()
        app.executor = LoopingExecutor()
        app.thinking_engine = type("Thinking", (), {"start_user_thinking": lambda *args, **kwargs: None, "llm": DummyModeTarget()})()

        session = SessionMemory()
        events = list(app._run_core_loop("Need tickets from NANJINGNAN to XUZHOUDONG on 2026-03-31 and prefer G20", session))

        self.assertTrue(any(event["type"] == "token" for event in events[:-1]))
        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["text"], "best effort final answer")
        self.assertEqual(app.answer_gen.force_finalize_calls, 1)
        self.assertLessEqual(app.answer_gen.structured_calls, 2)
        self.assertTrue(any(state == AgentState.SKIP and "need_more_facts" in detail for state, detail in app.psw.events))

    def test_fast_loop_guard_stops_preferred_ticket_query_from_spinning_multiple_rounds(self):
        class LoopingAnswerGen(DummyAnswerGen):
            def __init__(self):
                super().__init__(fast=True)
                self.structured_calls = 0
                self.force_finalize_calls = 0

            def generate_structured(self, **kwargs):
                self.structured_calls += 1
                return {
                    "type": "need_more_facts",
                    "extra_request": {
                        "missing": [
                            {
                                "domain": "railway",
                                "object": "train",
                                "id": "G20",
                            }
                        ]
                    },
                }

            def force_finalize(self, **kwargs):
                self.force_finalize_calls += 1
                return "我先基于当前余票和偏好给你一个尽量可靠的推荐。"

        class PreferredTicketExecutor:
            def __init__(self):
                self.dialog_seen_queries = set()
                self.calls = 0

            def execute(self, plan):
                self.calls += 1
                if self.calls == 1:
                    return {
                        "queries": [
                            {
                                "object": "left_ticket_s2s",
                                "id": "NANJINGNAN-XUZHOUDONG",
                                "date": "2026-03-31",
                                "pretty": "G20 二等座有票\nG42 二等座有票\nG80 二等座有票",
                            },
                            {
                                "object": "train",
                                "id": "G20",
                                "pretty": "G20 南京南 15:02 开, 徐州东 16:11 到",
                            },
                        ],
                        "analysis": [],
                        "comparisons": [],
                        "meta": {"errors": [], "warnings": [], "chat_messages": []},
                    }
                return {
                    "queries": [],
                    "analysis": [],
                    "comparisons": [],
                    "meta": {"errors": [], "warnings": [], "chat_messages": []},
                }

        app = RailwayAgentApp.__new__(RailwayAgentApp)
        app.answer_gen = LoopingAnswerGen()
        app._record_turn_memory = lambda *args, **kwargs: None
        app.memory_manager = None
        app.fast_coordinator = type("FastCoordinator", (), {"prepare": lambda *args, **kwargs: DummyFastAssets()})()
        app.max_rounds = 5
        app.psw = DummyPSW()
        app._thinking_queue = queue.Queue()
        app.router = type(
            "Router",
            (),
            {
                "route": lambda self, user_text, session: [
                    {
                        "action": "query",
                        "params": {
                            "domain": "railway",
                            "object": "left_ticket_s2s",
                            "id": "NANJINGNAN-XUZHOUDONG",
                            "date": "2026-03-31",
                        },
                    },
                    {
                        "action": "query",
                        "params": {
                            "domain": "railway",
                            "object": "train",
                            "id": "G20",
                        },
                    },
                ],
                "recover_fast_tasks": lambda *args, **kwargs: None,
            },
        )()
        app.planner = type(
            "Planner",
            (),
            {
                "build_plan": lambda self, tasks: tasks,
                "build_plan_from_request": lambda self, extra_request, facts: [
                    {
                        "action": "query",
                        "params": {
                            "domain": "railway",
                            "object": "train",
                            "id": "G20",
                        },
                    }
                ],
            },
        )()
        app.executor = PreferredTicketExecutor()
        app.thinking_engine = type("Thinking", (), {"start_user_thinking": lambda *args, **kwargs: None, "llm": DummyModeTarget()})()

        session = SessionMemory()
        events = list(app._run_core_loop("我想知道从南京南到徐州东，3.31还有余票吗？我比较喜欢G20", session))

        self.assertTrue(any(event["type"] == "token" for event in events[:-1]))
        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["text"], "我先基于当前余票和偏好给你一个尽量可靠的推荐。")
        self.assertEqual(app.answer_gen.force_finalize_calls, 1)
        self.assertLessEqual(app.answer_gen.structured_calls, 2)
        self.assertTrue(any(state == AgentState.SKIP and "need_more_facts" in detail for state, detail in app.psw.events))

    def test_fast_empty_query_guard_forces_finalize_without_extra_round(self):
        class EmptyLoopAnswerGen(DummyAnswerGen):
            def __init__(self):
                super().__init__(fast=True)
                self.structured_calls = 0
                self.force_finalize_calls = 0

            def generate_structured(self, **kwargs):
                self.structured_calls += 1
                return {
                    "type": "need_more_facts",
                    "extra_request": {
                        "missing": [
                            {
                                "domain": "railway",
                                "object": "station_to_station_mini",
                                "id": "八达岭长城-南京",
                                "date": "2026-04-02",
                            }
                        ]
                    },
                }

            def should_force_finalize_on_empty_queries(self, facts, extra_request=None):
                return True

            def force_finalize(self, **kwargs):
                self.force_finalize_calls += 1
                return "我已经按八达岭长城-南京 / 2026-04-02 查询过车次或标杆车结果，但当前没有查到可用的匹配结果。"

        class EmptyOnlyExecutor:
            def __init__(self):
                self.dialog_seen_queries = set()
                self.calls = 0

            def execute(self, plan):
                self.calls += 1
                return {
                    "queries": [
                        {
                            "type": "query_empty",
                            "domain": "railway",
                            "object": "s2s_benchmark",
                            "id": "八达岭长城-南京",
                            "date": "2026-04-02",
                        }
                    ],
                    "analysis": [],
                    "comparisons": [],
                    "meta": {"errors": [], "warnings": [], "chat_messages": []},
                }

        app = RailwayAgentApp.__new__(RailwayAgentApp)
        app.answer_gen = EmptyLoopAnswerGen()
        app._record_turn_memory = lambda *args, **kwargs: None
        app.memory_manager = None
        app.fast_coordinator = type("FastCoordinator", (), {"prepare": lambda *args, **kwargs: DummyFastAssets()})()
        app.max_rounds = 5
        app.psw = DummyPSW()
        app._thinking_queue = queue.Queue()
        app.router = type(
            "Router",
            (),
            {
                "route": lambda self, user_text, session: [
                    {
                        "action": "query",
                        "params": {
                            "domain": "railway",
                            "object": "s2s_benchmark",
                            "id": "八达岭长城-南京",
                            "date": "2026-04-02",
                        },
                    }
                ],
                "recover_fast_tasks": lambda *args, **kwargs: None,
            },
        )()
        app.planner = type(
            "Planner",
            (),
            {
                "build_plan": lambda self, tasks: tasks,
                "build_plan_from_request": lambda *args, **kwargs: (_ for _ in ()).throw(
                    AssertionError("empty-query guard should stop before extra planning round")
                ),
            },
        )()
        app.executor = EmptyOnlyExecutor()
        app.thinking_engine = type("Thinking", (), {"start_user_thinking": lambda *args, **kwargs: None, "llm": DummyModeTarget()})()

        session = SessionMemory()
        events = list(app._run_core_loop("八达岭长城到南京今天有什么标杆车", session))

        self.assertTrue(any(event["type"] == "token" for event in events[:-1]))
        self.assertEqual(events[-1]["type"], "final")
        self.assertIn("没有查到可用的匹配结果", events[-1]["text"])
        self.assertEqual(app.answer_gen.structured_calls, 1)
        self.assertEqual(app.answer_gen.force_finalize_calls, 1)
        self.assertEqual(app.executor.calls, 1)
        self.assertTrue(any(state == AgentState.SKIP and "empty-result guard" in detail for state, detail in app.psw.events))


if __name__ == "__main__":
    unittest.main()
