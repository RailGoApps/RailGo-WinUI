import ast
import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from agent.router import Router
from memory.session import SessionMemory


NANJING_SOUTH = "\u5357\u4eac\u5357"
XUZHOU_EAST = "\u5f90\u5dde\u4e1c"
FUZHOU = "\u798f\u5dde"
SHENZHEN_NORTH = "\u6df1\u5733\u5317"


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 3, 24, 12, 0, 0)


class DummyLLM:
    def __init__(self, mode="fast", response=None):
        self.mode = mode
        self.generate_called = 0
        self.semantic_generate_called = 0
        self.response = response or '{"action":"chat","params":{"message":"fallback"}}'
        self.last_messages = None

    def set_mode(self, mode):
        self.mode = mode

    def get_mode(self):
        return self.mode

    def generate(self, messages, timeout=None, max_retries=None):
        system_text = "\n".join(
            str(item.get("content") or "")
            for item in (messages or [])
            if isinstance(item, dict) and item.get("role") == "system"
        )
        if "RailGPT Semantic Router Council" in system_text:
            self.semantic_generate_called += 1
        else:
            self.generate_called += 1
        self.last_messages = messages
        return self.response


class DummyContextAgent:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def prepare(self, user_text, session=None):
        self.calls += 1
        return self.result


class FastRouterTest(unittest.TestCase):
    def make_router(self, response=None):
        memory = SessionMemory()
        router = Router(memory)
        router.llm = DummyLLM(mode="fast", response=response)
        return router, memory

    def test_semantic_council_receives_verified_active_topic_frame(self):
        router, memory = self.make_router()
        memory.add_user_message(f"{NANJING_SOUTH}站出发大屏")
        memory.update_from_tasks(
            [{"action": "query", "params": {"domain": "railway", "object": "station_board"}}]
        )
        memory.update_from_facts(
            {
                "queries": [
                    {
                        "object": "station_board",
                        "grounded_slots": {"station": NANJING_SOUTH, "direction": "departure"},
                    }
                ]
            }
        )
        memory.add_ai_message(f"{NANJING_SOUTH}站出发大屏快照已查询。")
        user_text = "现在车站运行怎么样"
        context = router._build_fast_route_context(user_text, session=memory, context_agent_result={})

        messages = router._build_semantic_router_council_messages(context)
        prompt = "\n".join(str(message.get("content") or "") for message in messages)

        self.assertIn("active_topic_frame", prompt)
        self.assertIn(NANJING_SOUTH, prompt)
        self.assertIn("station_board", prompt)

    def test_verified_station_board_topic_routes_implicit_status_followup(self):
        vote = {
            "agent": "tool_intent_agent",
            "intent": "station_board_current",
            "preferred_action": "query",
            "confidence": 96,
            "reason": "the active station-board topic supplies the omitted station",
            "required_object": "station_board",
            "query_id": "",
            "query_date": "",
            "grounded_slots": {"station": NANJING_SOUTH, "direction": "departure"},
        }
        router, memory = self.make_router(
            response=json.dumps({"votes": [vote], "consensus": vote, "conflict": False}, ensure_ascii=False)
        )
        memory.add_user_message(f"{NANJING_SOUTH}站出发大屏")
        memory.update_from_tasks(
            [{"action": "query", "params": {"domain": "railway", "object": "station_board"}}]
        )
        memory.update_from_facts(
            {"queries": [{"object": "station_board", "grounded_slots": {"station": NANJING_SOUTH, "direction": "departure"}}]}
        )
        memory.add_ai_message(f"{NANJING_SOUTH}站出发大屏快照已查询。")

        tasks = router.route("现在车站运行怎么样", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "station_board")
        self.assertEqual(tasks[0]["params"]["id"], f"{NANJING_SOUTH}|departure")

    def test_bureau_followup_uses_active_train_not_stale_station_board(self):
        vote = {
            "agent": "tool_intent_agent",
            "intent": "train_path",
            "preferred_action": "query",
            "confidence": 97,
            "reason": "the active G680 train subject supplies the omitted train for its operating bureau",
            "required_object": "path_detail",
            "query_id": "G680",
            "query_date": "",
            "grounded_slots": {"train": "G680"},
        }
        router, memory = self.make_router(
            response=json.dumps({"votes": [vote], "consensus": vote, "conflict": False}, ensure_ascii=False)
        )

        # An older station-board topic is deliberately left in the dialogue.
        memory.add_user_message(f"{FUZHOU}站出发大屏")
        memory.update_from_tasks(
            [{"action": "query", "params": {"domain": "railway", "object": "station_board"}}]
        )
        memory.update_from_facts(
            {"queries": [{"object": "station_board", "grounded_slots": {"station": FUZHOU, "direction": "departure"}}]}
        )
        memory.add_ai_message(f"{FUZHOU}站出发大屏快照已查询。")

        memory.add_user_message("G680今天有没有晚点？")
        memory.update_from_tasks(
            [{"action": "query", "params": {"domain": "railway", "object": "train_delay", "id": "G680"}}]
        )
        memory.update_from_facts(
            {"queries": [{"object": "train_delay", "query_id": "G680", "grounded_slots": {"train": "G680"}}]}
        )
        memory.add_ai_message("G680当前运行状态已查询。")

        tasks = router.route("分析这辆车服务的城市群，看看这辆车是什么路局的？", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "path_detail")
        self.assertEqual(tasks[0]["params"]["id"], "G680")
        self.assertEqual(memory.get_active_topic_frame()["subject"]["train"], "G680")

    @staticmethod
    def semantic_chat_response(intent="scenery_line_inference"):
        vote = {
            "agent": "chat_knowledge_agent",
            "intent": intent,
            "preferred_action": "chat",
            "confidence": 96,
            "reason": "the user asks for a clue-based railway inference, not a missing route slot",
            "required_object": "",
            "query_id": "",
            "query_date": "",
        }
        return json.dumps(
            {"votes": [vote], "consensus": vote, "conflict": False},
            ensure_ascii=False,
        )

    def test_fast_router_hits_benchmark_route_without_llm(self):
        router, memory = self.make_router()

        tasks = router.route(f"{NANJING_SOUTH}\u5230{XUZHOU_EAST}\u6700\u5feb\u7684\u8f66", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "s2s_benchmark")
        self.assertEqual(tasks[0]["params"]["id"], f"{NANJING_SOUTH}-{XUZHOU_EAST}")
        self.assertEqual(router.llm.generate_called, 0)
        self.assertEqual(router.llm.semantic_generate_called, 1)

    def test_fast_router_strips_colloquial_route_tail(self):
        router, memory = self.make_router()

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route(
                f"{NANJING_SOUTH}\u5230{XUZHOU_EAST}\u6709\u4ec0\u4e48\u6700\u5feb\u7684\u8f66",
                memory,
            )

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "s2s_benchmark")
        self.assertEqual(tasks[0]["params"]["id"], f"{NANJING_SOUTH}-{XUZHOU_EAST}")
        self.assertEqual(tasks[0]["params"]["date"], "2026-03-24")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_strips_bare_fastest_tail(self):
        router, memory = self.make_router()

        tasks = router.route(f"{NANJING_SOUTH}\u5230{XUZHOU_EAST}\u6700\u5feb\u7684", memory)

        self.assertEqual(tasks[0]["params"]["id"], f"{NANJING_SOUTH}-{XUZHOU_EAST}")
        self.assertEqual(tasks[0]["params"]["object"], "s2s_benchmark")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_hits_telecode_without_llm(self):
        router, memory = self.make_router()

        tasks = router.route(f"{NANJING_SOUTH}\u7684\u7535\u62a5\u7801\u662f\u4ec0\u4e48", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "telecode")
        self.assertEqual(tasks[0]["params"]["id"], NANJING_SOUTH)
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_handles_arrow_route_without_llm(self):
        router, memory = self.make_router()

        tasks = router.route(f"{FUZHOU}\u2192{SHENZHEN_NORTH}\u6700\u5feb\u600e\u4e48\u5750", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "s2s_benchmark")
        self.assertEqual(tasks[0]["params"]["id"], f"{FUZHOU}-{SHENZHEN_NORTH}")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_handles_open_route_phrase_without_llm(self):
        router, memory = self.make_router()

        tasks = router.route(f"\u4ece{FUZHOU}\u5f00\u5f80{SHENZHEN_NORTH}\u6709\u54ea\u4e9b\u9ad8\u94c1", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "station_to_station_mini")
        self.assertEqual(tasks[0]["params"]["id"], f"{FUZHOU}-{SHENZHEN_NORTH}")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_handles_direct_train_listing_without_llm(self):
        router, memory = self.make_router()

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route(f"{FUZHOU}\u5230\u5317\u4eac\u5357\u6709\u54ea\u4e9b\u76f4\u8fbe", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "station_to_station_mini")
        self.assertEqual(tasks[0]["params"]["id"], f"{FUZHOU}-\u5317\u4eac\u5357")
        self.assertEqual(tasks[0]["params"]["date"], "2026-03-24")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_handles_identity_chat_without_llm(self):
        router, memory = self.make_router()

        tasks = router.route("\u4f60\u662f\uff1f", memory)

        self.assertEqual(tasks[0]["action"], "chat")
        self.assertEqual(router.llm.generate_called, 0)

    def test_memory_profile_question_uses_semantic_chat_not_od_pending(self):
        router, memory = self.make_router(
            response=self.semantic_chat_response(intent="memory_profile_chat")
        )
        memory.set_memory_recall(
            {
                "memory_context_package": {
                    "schema_version": 2,
                    "hard_anchors": {},
                    "profile_index": [
                        {
                            "category": "train",
                            "value": "G813",
                            "classification": "recurring_interest",
                            "mention_count": 3,
                            "allowed_usage": "soft_profile_only",
                        }
                    ],
                }
            }
        )

        tasks = router.route("猜猜我最喜欢的车是什么？最喜欢的车次又是什么？", memory)

        self.assertEqual(tasks[0]["action"], "chat")
        self.assertNotIn("出发站", tasks[0]["params"]["message"])
        self.assertEqual(router.get_last_intent_envelope()["intent_family"], "memory_profile_chat")

    def test_semantic_council_accepts_fractional_confidence_and_user_profile_target(self):
        router, memory = self.make_router()
        context = router._build_fast_route_context(
            "猜猜我最喜欢的车次",
            session=memory,
            context_agent_result={},
        )
        raw = json.dumps(
            {
                "votes": [
                    {
                        "agent": "chat_knowledge_agent",
                        "intent": "social_chat_personal_preference",
                        "preferred_action": "chat",
                        "confidence": 0.97,
                        "reason": "guess the user's favorite train",
                        "required_object": "",
                        "profile_target": "user",
                    }
                ],
                "consensus": {
                    "intent": "personal_preference_guess",
                    "preferred_action": "chat",
                    "confidence": 0.97,
                    "reason": "user profile question",
                    "required_object": "",
                    "profile_target": "user",
                },
                "conflict": False,
            },
            ensure_ascii=False,
        )

        parsed = router._parse_semantic_router_council(raw, context)

        self.assertEqual(parsed["consensus"]["confidence"], 97)
        self.assertEqual(parsed["consensus"]["intent"], "memory_profile_chat")

    def test_semantic_council_calibrates_unanimous_zero_placeholders(self):
        router, memory = self.make_router()
        context = router._build_fast_route_context(
            "check tickets from Xiamen to Nanjing South",
            session=memory,
            context_agent_result={},
        )
        votes = []
        for agent in ("continuation_agent", "tool_intent_agent", "chat_knowledge_agent"):
            votes.append(
                {
                    "agent": agent,
                    "intent": "left_ticket_s2s",
                    "preferred_action": "query",
                    "confidence": 0,
                    "reason": "A concrete ticket query is required.",
                    "required_object": "left_ticket_s2s",
                    "grounded_slots": {
                        "dep": "Xiamen",
                        "arr": "Nanjing South",
                        "date": "2026-05-05",
                    },
                }
            )
        raw = json.dumps(
            {
                "votes": votes,
                "consensus": {
                    "intent": "left_ticket_s2s",
                    "preferred_action": "query",
                    "confidence": 0,
                    "reason": "All agents agree on the ticket capability.",
                    "required_object": "left_ticket_s2s",
                    "grounded_slots": {
                        "dep": "Xiamen",
                        "arr": "Nanjing South",
                        "date": "2026-05-05",
                    },
                },
                "conflict": False,
            }
        )

        parsed = router._parse_semantic_router_council(raw, context)

        self.assertEqual(parsed["consensus"]["confidence"], 82)

    def test_profile_consensus_is_not_overridden_by_continuation_vote(self):
        router, memory = self.make_router()
        context = {
            "semantic_votes": [
                {
                    "agent": "continuation_agent",
                    "intent": "new_topic_intro",
                    "preferred_action": "chat",
                    "confidence": 95,
                    "profile_target": "none",
                },
                {
                    "agent": "chat_knowledge_agent",
                    "intent": "memory_profile_chat",
                    "preferred_action": "chat",
                    "confidence": 97,
                    "profile_target": "user",
                },
            ],
            "semantic_consensus": {
                "intent": "memory_profile_chat",
                "preferred_action": "chat",
                "confidence": 97,
                "profile_target": "user",
            },
            "semantic_continuation": {
                "intent": "new_topic_intro",
                "preferred_action": "chat",
                "confidence": 95,
            },
            "agent_context_package": {"context_fingerprint": "profile-test"},
        }

        tasks = router._resolve_semantic_router_council_tasks(context, session=memory)

        self.assertEqual(tasks[0]["action"], "chat")
        self.assertIn("soft profile index", tasks[0]["params"]["message"])
        self.assertEqual(router.last_intent_envelope.intent_family, "memory_profile_chat")

    def test_semantic_vote_repairs_capability_misplaced_in_query_id(self):
        router, _memory = self.make_router()
        normalized = router._normalize_semantic_vote({
            "agent": "tool_intent_agent",
            "intent": "query",
            "preferred_action": "query",
            "confidence": 98,
            "required_object": "",
            "query_id": "train_overview",
        })

        self.assertEqual(normalized["required_object"], "train_overview")
        self.assertEqual(normalized["query_id"], "")

    def test_fast_router_explicit_train_overview_in_english_stays_on_train_tools(self):
        router, memory = self.make_router()

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("I WANNA KNOW EVERYTHING ABOUT G20", memory)

        self.assertTrue(
            any(
                task.get("action") == "query"
                and task.get("params", {}).get("object") == "path_detail"
                and task.get("params", {}).get("id") == "G20"
                for task in tasks
            )
        )
        self.assertTrue(
            any(
                task.get("action") == "query"
                and task.get("params", {}).get("object") in {"train", "path_detail"}
                and task.get("params", {}).get("id") == "G20"
                for task in tasks
            )
        )
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_explicit_train_overview_in_chinese_stays_on_train_tools(self):
        router, memory = self.make_router()

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("\u4ecb\u7ecd\u4e00\u4e0b G20", memory)

        self.assertTrue(
            any(
                task.get("action") == "query"
                and task.get("params", {}).get("object") == "path_detail"
                and task.get("params", {}).get("id") == "G20"
                for task in tasks
            )
        )
        self.assertTrue(
            any(
                task.get("action") == "query"
                and task.get("params", {}).get("object") in {"train", "path_detail"}
                and task.get("params", {}).get("id") == "G20"
                for task in tasks
            )
        )
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_uses_compact_llm_arbiter_before_legacy_prompt(self):
        router, memory = self.make_router(
            response=(
                '{"action":"query","params":{"domain":"railway","object":"s2s_benchmark",'
                f'"id":"{FUZHOU}-{SHENZHEN_NORTH}","date":"2026-03-24"}}'
            )
        )

        tasks = router._route_with_fast_llm(
            "\u9ebb\u70e6\u89c4\u5212\u798f\u5dde\u6df1\u5733\u5317\u9996\u9009\u65b9\u6848",
            memory,
        )

        self.assertEqual(router.llm.generate_called, 1)
        self.assertIn("Fast Intent Arbiter", router.llm.last_messages[0]["content"])
        self.assertIsInstance(tasks, list)

    def test_fast_router_defaults_missing_date_to_today_after_compact_llm(self):
        router, memory = self.make_router(
            response='{"action":"query","params":{"domain":"railway","object":"s2s_benchmark","id":"%s-%s"}}'
            % (FUZHOU, SHENZHEN_NORTH)
        )

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router._route_with_fast_llm(
                f"{FUZHOU}\u5230{SHENZHEN_NORTH}\u6700\u5feb",
                memory,
            )

        self.assertEqual(tasks[0]["params"]["date"], "2026-03-24")

    def test_fast_router_humanizes_generic_pending_with_slots(self):
        router, _memory = self.make_router()

        tasks = router._safe_parse_tasks(
            '{"action":"pending","params":{"question":"我还需要你补充一下信息才能继续😊","slot":["dep","arr"]}}'
        )

        self.assertEqual(tasks[0]["action"], "pending")
        self.assertIn("\u51fa\u53d1\u7ad9", tasks[0]["params"]["question"])
        self.assertIn("\u5230\u8fbe\u7ad9", tasks[0]["params"]["question"])
        self.assertEqual(tasks[0]["params"]["slot"], ["dep", "arr"])

    def test_safe_parse_failure_never_exposes_internal_router_error(self):
        router, _memory = self.make_router()

        tasks = router._safe_parse_tasks("this is not JSON")

        self.assertEqual(tasks[0]["action"], "chat")
        message = tasks[0]["params"]["message"].lower()
        self.assertNotIn("router", message)
        self.assertNotIn("json", message)
        self.assertIn("continue naturally", message)

    def test_invalid_route_like_fast_hit_falls_back_to_llm_pending(self):
        router, memory = self.make_router(
            response='{"action":"pending","params":{"question":"请确认终点站"}}'
        )

        tasks = router.route(f"{NANJING_SOUTH}\u5230\u706b\u661f\u7ad9\u6700\u5feb\u7684", memory)

        self.assertEqual(tasks[0]["action"], "pending")
        self.assertEqual(router.llm.generate_called, 0)

    def test_route_guard_repairs_llm_route_result_before_return(self):
        router, _memory = self.make_router()

        tasks = router._repair_fast_tasks(
            [{
                "action": "query",
                "params": {
                    "domain": "railway",
                    "object": "s2s_benchmark",
                    "id": f"{NANJING_SOUTH}-{XUZHOU_EAST}\u6700\u5feb\u7684",
                    "date": "2026-03-24",
                },
            }],
            strict_invalid=False,
        )

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["id"], f"{NANJING_SOUTH}-{XUZHOU_EAST}")

    def test_recover_fast_tasks_repairs_failed_route_query(self):
        router, _memory = self.make_router()
        prior_tasks = [
            {
                "action": "query",
                "params": {
                    "domain": "railway",
                    "object": "s2s_benchmark",
                    "id": f"{NANJING_SOUTH}-{XUZHOU_EAST}\u6700\u5feb\u7684",
                    "date": "2026-03-24",
                },
            }
        ]
        facts = {
            "queries": [
                {
                    "type": "query_empty",
                    "key": f"railway:s2s_benchmark:{NANJING_SOUTH}-{XUZHOU_EAST}\u6700\u5feb\u7684:2026-03-24",
                }
            ],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }

        repaired = router.recover_fast_tasks(
            user_text=f"{NANJING_SOUTH}\u5230{XUZHOU_EAST}\u6700\u5feb\u7684",
            facts=facts,
            prior_tasks=prior_tasks,
        )

        self.assertEqual(repaired[0]["params"]["id"], f"{NANJING_SOUTH}-{XUZHOU_EAST}")

    def test_fast_router_parses_this_week_sunday_ticket_date(self):
        router, memory = self.make_router()

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route(f"\u8fd9\u5468\u5929\u4ece{NANJING_SOUTH}\u5230{XUZHOU_EAST}\u8fd8\u6709\u4f59\u7968\u5417", memory)

        self.assertEqual(tasks[0]["params"]["object"], "left_ticket_s2s")
        self.assertEqual(tasks[0]["params"]["id"], f"{NANJING_SOUTH}-{XUZHOU_EAST}")
        self.assertEqual(tasks[0]["params"]["date"], "2026-03-29")

    def test_fast_router_parses_next_monday_ticket_date(self):
        router, memory = self.make_router()

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route(f"\u4e0b\u5468\u4e00{NANJING_SOUTH}\u5230{XUZHOU_EAST}\u4f59\u7968", memory)

        self.assertEqual(tasks[0]["params"]["object"], "left_ticket_s2s")
        self.assertEqual(tasks[0]["params"]["date"], "2026-03-30")

    def test_fast_router_parses_bare_weekday_date(self):
        router, memory = self.make_router()

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route(f"\u5468\u4e94{NANJING_SOUTH}\u5230{XUZHOU_EAST}\u4f59\u7968", memory)

        self.assertEqual(tasks[0]["params"]["object"], "left_ticket_s2s")
        self.assertEqual(tasks[0]["params"]["date"], "2026-03-27")

    def test_fast_router_reuses_session_train_anchor_for_contextual_path_question(self):
        router, memory = self.make_router()
        memory.update_anchor(train="G257", date="2026-03-24", query_type="path_detail")

        tasks = router.route("\u8fd9\u8d9f\u5217\u8f66\u662f\u6cbf\u7740\u4ec0\u4e48\u9ad8\u94c1\u7ebf\u8def\u8fd0\u884c\u5462\uff1f", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "path_detail")
        self.assertEqual(tasks[0]["params"]["id"], "G257")
        self.assertEqual(tasks[0]["params"]["date"], "2026-03-24")

    def test_fast_plus_contextual_this_car_route_question_reuses_recent_train_anchor(self):
        router, memory = self.make_router()
        router.set_mode("fast-plus")
        router.llm.response = (
            '{"has_date":true,"normalized_date":"2026-04-03",'
            '"date_source":"conversation_context","date_span":"context",'
            '"is_contextual_date":true,"confidence":90,"reason":"context date"}'
        )
        router.context_agent = DummyContextAgent(
            {
                "intent_category": "unknown",
                "rewritten_user_text": "\u8fd9\u8f86\u8f66\u7684\u5177\u4f53\u8def\u7ebf\u662f\u4ec0\u4e48",
                "resolved_route": "",
                "resolved_train_numbers": [],
                "resolved_emu": "",
                "resolved_date": "",
                "resolved_station_mentions": [],
                "resolved_query_object": "",
                "confidence": 0,
                "reason": "should be bypassed because local train-path expert is sufficient",
            }
        )
        memory.update_anchor(
            train="G20",
            date="2026-04-03",
            query_type="train",
            query_object="train",
            source="manual",
        )

        tasks = router.route("\uff01\uff01\uff01\u8fd9\u8f86\u8f66\u7684\u5177\u4f53\u8def\u7ebf\u662f\u4ec0\u4e48\uff1f", memory)

        self.assertEqual(router.context_agent.calls, 0)
        self.assertEqual(router.llm.generate_called, 1)
        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "path_detail")
        self.assertEqual(tasks[0]["params"]["id"], "G20")
        self.assertEqual(tasks[0]["params"]["date"], "2026-04-03")

    def test_fast_router_multi_train_stop_difference_question_prefers_train_tools(self):
        router, memory = self.make_router()

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("G73和G71同样从北京到贵阳，为什么G73更快？它们停站有什么不同？", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "path_detail")
        self.assertEqual(tasks[0]["params"]["id"], "G73")
        self.assertEqual(tasks[0]["params"]["date"], "2026-03-24")
        self.assertEqual(tasks[1]["action"], "query")
        self.assertEqual(tasks[1]["params"]["object"], "path_detail")
        self.assertEqual(tasks[1]["params"]["id"], "G71")
        self.assertEqual(tasks[1]["params"]["date"], "2026-03-24")
        self.assertEqual(len(tasks), 2)
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_directional_speed_comparison_stays_on_rail_knowledge_chat_without_llm(self):
        router, memory = self.make_router(
            response='{"action":"pending","params":{"question":"bad"}}'
        )

        tasks = router.route("东西方向的列车更快还是南北方向的更快", memory)

        self.assertEqual(tasks[0]["action"], "chat")
        self.assertIn("哈基米南北绿豆", tasks[0]["params"].get("message", ""))
        self.assertNotIn("direct_reply", tasks[0]["params"])
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_affirmative_reply_reuses_recent_context_as_contextual_chat(self):
        router, memory = self.make_router(
            response='{"action":"pending","params":{"question":"bad"}}'
        )
        memory.update_from_tasks(
            [
                {
                    "action": "query",
                    "params": {
                        "domain": "railway",
                        "object": "path_detail",
                        "id": "G73",
                        "date": "2026-03-24",
                    },
                },
                {
                    "action": "query",
                    "params": {
                        "domain": "railway",
                        "object": "path_detail",
                        "id": "G71",
                        "date": "2026-03-24",
                    },
                },
            ]
        )
        memory.add_ai_message("我已经说明 G73 和 G71 实际是北京到青岛。你是想继续比较这两趟车吗？还是想改查北京到贵阳的其他车次？")

        tasks = router.route("是的！", memory)

        self.assertEqual(tasks[0]["action"], "chat")
        self.assertIn("context", tasks[0]["params"]["message"].lower())
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_contextual_evidence_followup_reuses_recent_trains_for_path_detail(self):
        router, memory = self.make_router()
        memory.update_from_tasks(
            [
                {
                    "action": "query",
                    "params": {
                        "domain": "railway",
                        "object": "path_detail",
                        "id": "G73",
                        "date": "2026-03-24",
                    },
                },
                {
                    "action": "query",
                    "params": {
                        "domain": "railway",
                        "object": "path_detail",
                        "id": "G71",
                        "date": "2026-03-24",
                    },
                },
            ]
        )
        memory.add_ai_message("我刚才对比了 G73 和 G71 的经停与走向。")
        memory.update_anchor(
            train="G73",
            route="北京-贵阳",
            dep="北京",
            arr="贵阳",
            date="2026-03-24",
            query_type="path_detail",
            query_object="path_detail",
            source="manual",
        )

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("你怎么知道是京广高铁！！！！？？？", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "path_detail")
        self.assertEqual(tasks[0]["params"]["id"], "G73")
        self.assertEqual(tasks[0]["params"]["date"], "2026-03-24")
        self.assertEqual(tasks[1]["action"], "query")
        self.assertEqual(tasks[1]["params"]["object"], "path_detail")
        self.assertEqual(tasks[1]["params"]["id"], "G71")
        self.assertEqual(tasks[1]["params"]["date"], "2026-03-24")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_verifies_user_named_train_collection_before_chat_fallback(self):
        router, memory = self.make_router()
        memory.add_user_message("我最喜欢的车是 G20，还有 G3089、G1654 和 G1677 系列。")
        memory.add_ai_message("我刚才概述了这些车的路线，但这段说明还没有经过工具核验。")

        tasks = router.route("你确定它们的路线是这样的？？？你最好查证一下！", memory)

        self.assertEqual(router.llm.generate_called, 0)
        self.assertEqual(router.llm.semantic_generate_called, 0)
        self.assertEqual([task["params"]["object"] for task in tasks], ["path_detail"] * 4)
        self.assertEqual([task["params"]["id"] for task in tasks], ["G20", "G3089", "G1654", "G1677"])

    def test_fast_router_terminal_verification_preserves_multiple_explicit_trains(self):
        router, memory = self.make_router()

        tasks = router.route("你确定 G20 和 G3089 的路线是这个？", memory)

        self.assertEqual(router.llm.generate_called, 0)
        self.assertEqual([task["params"]["object"] for task in tasks], ["path_detail", "path_detail"])
        self.assertEqual([task["params"]["id"] for task in tasks], ["G20", "G3089"])

    def test_fast_router_hits_stopcheck_for_train_and_station(self):
        router, memory = self.make_router()

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("G87今天停不停南京南", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "path_stopcheck")
        self.assertEqual(tasks[0]["params"]["id"], "G87|南京南")
        self.assertEqual(tasks[0]["params"]["date"], "2026-03-24")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_hits_stopcheck_for_multiple_trains(self):
        router, memory = self.make_router()

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("G87,G89哪些停南京南", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "path_stopcheck")
        self.assertEqual(tasks[0]["params"]["id"], "G87,G89|南京南")
        self.assertEqual(tasks[0]["params"]["date"], "2026-03-24")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_plus_partial_route_followup_uses_memory_completion_without_context_agent(self):
        router, memory = self.make_router()
        router.set_mode("fast-plus")
        router.context_agent = DummyContextAgent(
            {
                "intent_category": "unknown",
                "rewritten_user_text": "我从南京南出发",
                "resolved_route": "",
                "resolved_train_numbers": [],
                "resolved_emu": "",
                "resolved_date": "",
                "resolved_station_mentions": [],
                "resolved_query_object": "",
                "confidence": 0,
                "reason": "context agent timeout/failure: Request timed out.",
            }
        )

        memory.add_user_message("我要去上海，帮我查一下车！！！")
        memory.add_ai_message("请再告诉我你想查的关键条件，比如出发站、到达站或车次号，我就可以继续。")
        memory.enter_followup(
            question="请再告诉我你想查的关键条件，比如出发站、到达站或车次号，我就可以继续。",
            slot=["dep", "arr"],
        )

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("我从南京南出发", memory)

        self.assertEqual(router.context_agent.calls, 1)
        self.assertEqual(router.llm.generate_called, 0)
        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "station_to_station_mini")
        self.assertEqual(tasks[0]["params"]["id"], "南京南-上海")
        self.assertEqual(tasks[0]["params"]["date"], "2026-03-24")

    def test_fast_plus_first_turn_partial_arrival_bypasses_context_agent_and_returns_natural_pending(self):
        router, memory = self.make_router()
        router.set_mode("fast-plus")
        router.context_agent = DummyContextAgent(
            {
                "intent_category": "route_listing",
                "rewritten_user_text": "请查询今天去上海的车次",
                "resolved_route": "",
                "resolved_train_numbers": [],
                "resolved_emu": "",
                "resolved_date": "2026-03-24",
                "resolved_station_mentions": ["上海"],
                "resolved_query_object": "station_to_station_mini",
                "confidence": 92,
                "reason": "should not be used on weak first-turn partial route",
            }
        )

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("我要去上海，帮我查一下车！！！", memory)

        self.assertEqual(router.context_agent.calls, 0)
        self.assertEqual(router.llm.generate_called, 0)
        self.assertEqual(tasks[0]["action"], "pending")
        self.assertEqual(tasks[0]["params"]["slot"], ["dep"])
        self.assertIn("上海", tasks[0]["params"]["question"])
        self.assertIn("从哪里出发", tasks[0]["params"]["question"])

    def test_fast_go_first_turn_partial_departure_bypasses_context_agent_and_returns_natural_pending(self):
        router, memory = self.make_router()
        router.set_mode("fast-go")
        router.context_agent = DummyContextAgent(
            {
                "intent_category": "route_listing",
                "rewritten_user_text": "请查询南京南出发的车次",
                "resolved_route": "",
                "resolved_train_numbers": [],
                "resolved_emu": "",
                "resolved_date": "2026-03-24",
                "resolved_station_mentions": ["南京南"],
                "resolved_query_object": "station_to_station_mini",
                "confidence": 92,
                "reason": "should not be used on weak first-turn partial route",
            }
        )

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("我从南京南出发", memory)

        self.assertEqual(router.context_agent.calls, 0)
        self.assertEqual(router.llm.generate_called, 0)
        self.assertEqual(tasks[0]["action"], "pending")
        self.assertEqual(tasks[0]["params"]["slot"], ["arr"])
        self.assertIn("南京南", tasks[0]["params"]["question"])
        self.assertIn("到哪一站", tasks[0]["params"]["question"])

    def test_pending_slot_inference_uses_partial_station_reply(self):
        router, memory = self.make_router(
            response='{"action":"pending","params":{"question":"请补充一下"}}'
        )
        router.set_mode("fast-go")

        memory.add_user_message("我要去上海，帮我查一下车！！！")

        tasks = router.route("我要去上海，帮我查一下车！！！", memory)

        self.assertEqual(tasks[0]["action"], "pending")
        self.assertEqual(tasks[0]["params"]["slot"], ["dep"])
        self.assertIn("从哪里出发", tasks[0]["params"]["question"])

    def test_fast_router_partial_route_followup_uses_recent_memory_to_complete_query(self):
        router, memory = self.make_router()
        router.set_mode("fast-go")
        memory.add_user_message("我要去上海，帮我查一下车！！！")
        memory.add_ai_message("如果你是要去上海，再告诉我从哪里出发，我就继续帮你查。")
        memory.enter_followup(
            question="如果你是要去上海，再告诉我从哪里出发，我就继续帮你查。",
            slot=["dep"],
            context={"arr": "上海"},
        )

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("我从南京南出发", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "station_to_station_mini")
        self.assertEqual(tasks[0]["params"]["id"], "南京南-上海")
        self.assertEqual(tasks[0]["params"]["date"], "2026-03-24")
        self.assertEqual(router.llm.generate_called, 0)

    def test_partial_route_does_not_reuse_old_route_anchor_for_new_single_sided_query(self):
        router, memory = self.make_router()
        router.set_mode("fast-go")
        memory.update_anchor(
            route="北京-上海",
            dep="北京",
            arr="上海",
            date="2026-03-24",
            query_type="s2s_benchmark",
            query_object="s2s_benchmark",
            source="manual",
        )

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("我要去上海！！！请问有什么车？", memory)

        self.assertEqual(tasks[0]["action"], "pending")
        self.assertEqual(tasks[0]["params"]["slot"], ["dep"])
        self.assertIn("上海", tasks[0]["params"]["question"])
        self.assertIn("从哪里出发", tasks[0]["params"]["question"])
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_plus_partial_route_does_not_reuse_old_route_anchor_for_new_single_sided_query(self):
        router, memory = self.make_router()
        router.set_mode("fast-plus")
        router.context_agent = DummyContextAgent(
            {
                "intent_category": "route_listing",
                "rewritten_user_text": "请查询北京到上海今天有哪些车",
                "resolved_route": "北京-上海",
                "resolved_train_numbers": [],
                "resolved_emu": "",
                "resolved_date": "2026-03-24",
                "resolved_station_mentions": ["北京", "上海"],
                "resolved_query_object": "station_to_station_mini",
                "confidence": 90,
                "reason": "should be bypassed for single-sided new route query",
            }
        )
        memory.update_anchor(
            route="北京-上海",
            dep="北京",
            arr="上海",
            date="2026-03-24",
            query_type="station_to_station_mini",
            query_object="station_to_station_mini",
            source="manual",
        )

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("我要去上海！！！有什么车？", memory)

        self.assertEqual(router.context_agent.calls, 1)
        self.assertEqual(tasks[0]["action"], "pending")
        self.assertEqual(tasks[0]["params"]["slot"], ["dep"])
        self.assertIn("上海", tasks[0]["params"]["question"])
        self.assertIn("从哪里出发", tasks[0]["params"]["question"])
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_stopcheck_without_station_returns_specific_pending(self):
        router, memory = self.make_router()

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("G87今天停不停", memory)

        self.assertEqual(tasks[0]["action"], "pending")
        self.assertIn("车站", tasks[0]["params"]["question"])
        self.assertEqual(tasks[0]["params"]["slot"], ["station_name"])
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_hits_train_terminal_query_without_llm(self):
        router, memory = self.make_router()

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("G88从哪里开去哪里的？", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "path_detail")
        self.assertEqual(tasks[0]["params"]["id"], "G88")
        self.assertEqual(tasks[0]["params"]["date"], "2026-03-24")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_hits_train_terminal_query_with_origin_destination_phrase(self):
        router, memory = self.make_router()

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("G88始发终到是哪里", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "path_detail")
        self.assertEqual(tasks[0]["params"]["id"], "G88")
        self.assertEqual(tasks[0]["params"]["date"], "2026-03-24")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_train_terminal_without_train_returns_specific_pending(self):
        router, memory = self.make_router()

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("从哪里开去哪里的？", memory)

        self.assertEqual(tasks[0]["action"], "pending")
        self.assertIn("车次", tasks[0]["params"]["question"])
        self.assertEqual(tasks[0]["params"]["slot"], ["train_no"])
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_contextual_train_collection_followup_uses_chat(self):
        router, memory = self.make_router()
        memory.update_from_tasks(
            [
                {
                    "action": "query",
                    "params": {
                        "domain": "railway",
                        "object": "s2s_benchmark",
                        "id": f"{NANJING_SOUTH}-{XUZHOU_EAST}",
                        "date": "2026-03-24",
                    },
                }
            ]
        )
        memory.update_from_facts(
            {
                "queries": [
                    {
                        "domain": "railway",
                        "object": "s2s_benchmark",
                        "id": f"{NANJING_SOUTH}-{XUZHOU_EAST}",
                        "date": "2026-03-24",
                        "pretty": "G84 20:15→21:21\nG94 09:56→11:02\nG66 07:18→08:25",
                    }
                ]
            }
        )

        tasks = router.route("这些车次都有什么特点呢？", memory)

        self.assertEqual(tasks[0]["action"], "chat")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_contextual_social_reply_uses_chat(self):
        router, memory = self.make_router()
        memory.update_from_tasks(
            [
                {
                    "action": "query",
                    "params": {
                        "domain": "railway",
                        "object": "station",
                        "id": NANJING_SOUTH,
                        "date": "2026-03-24",
                    },
                }
            ]
        )
        memory.add_ai_message(f"{NANJING_SOUTH}是一个规模很大的高铁枢纽。")

        tasks = router.route("这么厉害的吗？", memory)

        self.assertEqual(tasks[0]["action"], "chat")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_contextual_social_reply_still_uses_chat_in_followup_mode(self):
        router, memory = self.make_router()
        router.set_mode("fast-go")
        memory.update_from_tasks(
            [
                {
                    "action": "query",
                    "params": {
                        "domain": "railway",
                        "object": "station",
                        "id": NANJING_SOUTH,
                        "date": "2026-03-24",
                    },
                }
            ]
        )
        memory.add_ai_message(f"{NANJING_SOUTH}是一个规模很大的高铁枢纽。")
        memory.enter_followup(question="请补充出发站", slot=["dep"])

        tasks = router.route("这么厉害的吗？", memory)

        self.assertEqual(tasks[0]["action"], "chat")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_contextual_laughter_reply_uses_chat(self):
        router, memory = self.make_router()
        memory.update_from_tasks(
            [
                {
                    "action": "query",
                    "params": {
                        "domain": "railway",
                        "object": "path_detail",
                        "id": "G6742",
                        "date": "2026-03-24",
                    },
                }
            ]
        )
        memory.add_ai_message("G6742 \u4e0d\u662f\u4eac\u6d25\u57ce\u9645\uff0c\u8fd0\u884c\u533a\u95f4\u662f\u77f3\u5bb6\u5e84\u5230\u5317\u4eac\u897f\u3002")

        tasks = router.route("\u54c8\u54c8\u54c8\u54c8\u54c8\u54c8\u54c8\u54c8\u54c8\u54c8\u54c8\u54c8\u54c8\u54c8\u54c8\u54c8\u54c8\u54c8", memory)

        self.assertEqual(tasks[0]["action"], "chat")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_generic_what_is_this_uses_chat_not_pending(self):
        router, memory = self.make_router()

        tasks = router.route("这是什么", memory)

        self.assertEqual(tasks[0]["action"], "chat")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_creative_continuation_routes_to_contextual_chat(self):
        router, memory = self.make_router(response=self.semantic_chat_response(intent="creative_transform"))
        memory.update_from_tasks(
            [
                {
                    "action": "query",
                    "params": {
                        "domain": "railway",
                        "object": "train",
                        "id": "G1677",
                        "date": "2026-03-24",
                    },
                }
            ]
        )
        memory.add_ai_message("G1677 是一趟很有故事感的列车，我刚才已经介绍过它的始发终到、车型和旅途氛围。")

        tasks = router.route("请你根据这些信息写一篇散文或者小说", memory)

        self.assertEqual(tasks[0]["action"], "chat")
        self.assertIn("continuation", tasks[0]["params"]["message"].lower())
        self.assertEqual(router.llm.semantic_generate_called, 1)

    def test_scenery_line_inference_uses_semantic_chat_not_partial_route_pending(self):
        router, memory = self.make_router(response=self.semantic_chat_response())

        tasks = router.route("列车外面有高山，马上到达上饶，白墙黑瓦，请你推测我在什么高铁线路", memory)

        self.assertEqual(tasks[0]["action"], "chat")
        self.assertEqual(router.llm.semantic_generate_called, 1)

    def test_new_scenery_inference_does_not_reuse_stale_train_anchor(self):
        router, memory = self.make_router(response=self.semantic_chat_response())
        memory.update_from_tasks(
            [{"action": "query", "params": {"domain": "railway", "object": "path_detail", "id": "G52", "date": "2026-07-15"}}]
        )
        memory.add_ai_message("前一轮已经介绍完G52和G55，现在等待用户的新问题。")

        context = router._build_fast_route_context(
            "列车外面有高山，马上到达上饶，白墙黑瓦，请你推测我在什么高铁线路",
            session=memory,
        )
        tasks = router.route("列车外面有高山，马上到达上饶，白墙黑瓦，请你推测我在什么高铁线路", memory)

        self.assertEqual(context["train_numbers"], [])
        self.assertEqual(tasks[0]["action"], "chat")
        self.assertNotIn("G52", tasks[0]["params"].get("message", ""))

    def test_plain_single_destination_query_still_returns_pending(self):
        router, memory = self.make_router()

        tasks = router.route("我想去上饶", memory)

        self.assertEqual(tasks[0]["action"], "pending")
        self.assertIn("dep", tasks[0]["params"]["slot"])

    def test_recent_answer_meta_questions_stay_in_chat(self):
        samples = (
            "为了回答这个问题，你进行了什么思考？请展示你的思考链",
            "系统是否返回冗余信息？",
        )
        for text in samples:
            with self.subTest(text=text):
                router, memory = self.make_router()
                memory.add_ai_message("刚才已经根据工具结果回答了G1的运行路线。")

                tasks = router.route(text, memory)

                self.assertEqual(tasks[0]["action"], "chat")

    def test_next_assignment_probability_starts_from_train_evidence_not_path(self):
        router, memory = self.make_router()
        memory.update_from_tasks(
            [{"action": "query", "params": {"domain": "railway", "object": "train", "id": "G813"}}]
        )
        memory.add_ai_message("G813近期由多组CR400BF-A动车组轮换担当。")

        tasks = router.route("如果一列车在某一天担当了G813，它的下一班车最大概率是什么？", memory)

        self.assertEqual(tasks, [{"action": "query", "params": {"domain": "railway", "object": "train", "id": "G813"}}])

    def test_fast_router_expansion_followup_routes_to_contextual_chat(self):
        router, memory = self.make_router(response=self.semantic_chat_response(intent="creative_expand"))
        memory.add_ai_message("上一条回答里我已经围绕 G1677 写了一小段故事，现在上下文里已经有完整正文。")

        tasks = router.route("再长一点！", memory)

        self.assertEqual(tasks[0]["action"], "chat")
        self.assertIn("continue", tasks[0]["params"]["message"].lower())
        self.assertEqual(router.llm.semantic_generate_called, 1)

    def test_fast_router_general_rail_knowledge_questions_prefer_chat(self):
        samples = (
            "什么是CTCS-3？",
            "高铁天窗是怎么安排的？",
            "火车迷常说的“刷绿”是什么意思？",
            "G7和G20使用的动车组是什么关系？",
        )

        for sample in samples:
            with self.subTest(sample=sample):
                router, memory = self.make_router()
                tasks = router.route(sample, memory)
                self.assertEqual(tasks[0]["action"], "chat")

    def test_fast_go_tunnel_pressure_question_prefers_chat_without_context_agent(self):
        router, memory = self.make_router()
        router.set_mode("fast-go")
        router.context_agent = DummyContextAgent(
            {
                "intent_category": "route_listing",
                "rewritten_user_text": "\u8bf7\u67e5\u8be2\u4eca\u5929\u4ece\u5317\u4eac\u5230\u4e0a\u6d77\u6709\u54ea\u4e9b\u8f66",
                "resolved_route": "\u5317\u4eac-\u4e0a\u6d77",
                "resolved_train_numbers": [],
                "resolved_emu": "",
                "resolved_date": "2026-03-24",
                "resolved_station_mentions": ["\u5317\u4eac", "\u4e0a\u6d77"],
                "resolved_query_object": "station_to_station_mini",
                "confidence": 90,
                "reason": "should be bypassed for first-turn railway knowledge question",
            }
        )

        tasks = router.route(
            "\u9ad8\u94c1\u5217\u8f66\u5728\u7a7f\u8fc7\u96a7\u9053\u65f6\uff0c\u8033\u6735\u6709\u660e\u663e\u538b\u8feb\u611f\u3002\u8f66\u53a2\u6c14\u538b\u8c03\u8282\u7cfb\u7edf\u4e0d\u80fd\u5b8c\u5168\u6d88\u9664\u8fd9\u79cd\u4e0d\u9002\u5417\uff1f",
            memory,
        )

        self.assertEqual(router.context_agent.calls, 0)
        self.assertEqual(router.llm.generate_called, 0)
        self.assertEqual(tasks[0]["action"], "chat")

    def test_fast_go_turnout_standard_question_prefers_chat_without_context_agent(self):
        router, memory = self.make_router()
        router.set_mode("fast-go")
        router.context_agent = DummyContextAgent(
            {
                "intent_category": "route_listing",
                "rewritten_user_text": "请查询合肥南到上饶今天有哪些车",
                "resolved_route": "合肥南-上饶",
                "resolved_train_numbers": [],
                "resolved_emu": "",
                "resolved_date": "2026-03-24",
                "resolved_station_mentions": ["合肥南", "上饶"],
                "resolved_query_object": "station_to_station_mini",
                "confidence": 90,
                "reason": "should be bypassed for engineering knowledge question",
            }
        )

        tasks = router.route(
            "合福高铁的120道岔的标准是什么？我看每次合福高铁转沪昆高铁这里都不需要减速过道岔",
            memory,
        )

        self.assertEqual(router.context_agent.calls, 0)
        self.assertEqual(router.llm.generate_called, 0)
        self.assertEqual(tasks[0]["action"], "chat")

    def test_followup_chat_still_uses_fast_chat_expert(self):
        router, memory = self.make_router()
        router.set_mode("fast-go")
        memory.enter_followup(question="\u8bf7\u8865\u5145\u51fa\u53d1\u7ad9", slot=["dep"])

        tasks = router.route("\u4f60\u662f\u8c01", memory)

        self.assertEqual(tasks[0]["action"], "chat")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_reuses_fact_route_anchor_even_after_ai_mentions_other_route(self):
        router, memory = self.make_router()
        memory.update_from_tasks(
            [
                {
                    "action": "query",
                    "params": {
                        "domain": "railway",
                        "object": "s2s_benchmark",
                        "id": f"{NANJING_SOUTH}-{XUZHOU_EAST}",
                        "date": "2026-03-24",
                    },
                }
            ]
        )
        memory.update_from_facts(
            {
                "queries": [
                    {
                        "domain": "railway",
                        "object": "s2s_benchmark",
                        "id": f"{NANJING_SOUTH}-{XUZHOU_EAST}",
                        "date": "2026-03-24",
                    }
                ]
            }
        )
        memory.add_ai_message("\u53ef\u53c2\u8003 G1808\uff08\u4e0a\u6d77\u5357 -> \u5f90\u5dde\u4e1c\uff09\u7684\u8fd0\u884c\u4fe1\u606f\u3002")

        tasks = router.route("\u8fd9\u6761\u7ebf\u8fd8\u6709\u6807\u6746\u8f66\u5417", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "s2s_benchmark")
        self.assertEqual(tasks[0]["params"]["id"], f"{NANJING_SOUTH}-{XUZHOU_EAST}")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_reuses_previous_route_query_object_for_new_route_followup(self):
        router, memory = self.make_router()
        memory.update_anchor(
            route="北京-上海",
            dep="北京",
            arr="上海",
            date="2026-03-25",
            query_type="s2s_benchmark",
            query_object="s2s_benchmark",
            source="manual",
        )

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("那从北京南到上海呢？", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "s2s_benchmark")
        self.assertEqual(tasks[0]["params"]["id"], "北京南-上海")
        self.assertEqual(tasks[0]["params"]["date"], "2026-03-25")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_reuses_route_anchor_for_short_benchmark_followup(self):
        router, memory = self.make_router()
        memory.update_anchor(
            route="北京南-上海",
            dep="北京南",
            arr="上海",
            date="2026-03-25",
            query_type="s2s_benchmark",
            query_object="s2s_benchmark",
            source="manual",
        )

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("有什么标杆车？", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "s2s_benchmark")
        self.assertEqual(tasks[0]["params"]["id"], "北京南-上海")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_reuses_route_anchor_for_short_compare_followup(self):
        router, memory = self.make_router()
        memory.update_anchor(
            route="南京南-福州",
            dep="南京南",
            arr="福州",
            date="2026-07-15",
            query_type="s2s_benchmark",
            query_object="s2s_benchmark",
            source="manual",
        )

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("好的，请你对比", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "s2s_benchmark")
        self.assertEqual(tasks[0]["params"]["id"], "南京南-福州")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_does_not_treat_emu_family_as_full_emu_id(self):
        router, _memory = self.make_router()

        self.assertIsNone(router._extract_emu_id("CR400AFBS"))
        self.assertIsNone(router._extract_emu_id("CR400AF-BS"))
        self.assertEqual(router._extract_emu_preferences("CR400AFBS"), ["CR400AFBS"])
        self.assertEqual(router._extract_emu_preferences("CR400AF-BS"), ["CR400AFBS"])
        self.assertEqual(router._extract_emu_preferences("AFZ"), ["CR400AFZ"])
        self.assertEqual(router._extract_emu_preferences("bfz"), ["CR400BFZ"])

    def test_fast_router_prefers_route_level_tools_for_benchmark_with_model_preference(self):
        router, memory = self.make_router()

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route(
                "我喜欢北京局的CR400AFBS动车组，请问从南京南到徐州东有没有这种车型的标杆车？能不能给我推荐一二？",
                memory,
            )

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "s2s_benchmark")
        self.assertEqual(tasks[0]["params"]["id"], f"{NANJING_SOUTH}-{XUZHOU_EAST}")
        self.assertEqual(tasks[0]["params"]["date"], "2026-03-24")
        self.assertTrue(any(item["params"]["object"] == "station_to_station_mini" for item in tasks))
        self.assertTrue(
            any(
                item["params"]["object"] == "s2s_bureau_filter"
                and item["params"]["id"] == f"{NANJING_SOUTH}-{XUZHOU_EAST}|北京局"
                for item in tasks
            )
        )
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_route_listing_with_bare_smart_emu_alias_stays_on_s2s_tools(self):
        router, memory = self.make_router()

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("明天上海虹桥到北京南，有哪些车是AFZ（智能动车组）担当的？", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "station_to_station_future")
        self.assertEqual(tasks[0]["params"]["id"], "上海虹桥-北京南")
        self.assertEqual(tasks[0]["params"]["date"], "2026-03-25")
        self.assertTrue(
            any(
                item["params"]["object"] == "station_to_station_mini"
                and item["params"]["id"] == "上海虹桥-北京南"
                and item["params"]["date"] == "2026-03-25"
                for item in tasks
            )
        )
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_route_listing_with_generic_smart_emu_intent_adds_route_assignment_context(self):
        router, memory = self.make_router()

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("明天上海虹桥到北京南，有哪些车是智能动车组担当的？", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "station_to_station_future")
        self.assertEqual(tasks[0]["params"]["id"], "上海虹桥-北京南")
        self.assertTrue(
            any(
                item["params"]["object"] == "station_to_station_mini"
                and item["params"]["id"] == "上海虹桥-北京南"
                for item in tasks
            )
        )
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_keeps_full_emu_query_for_explicit_emu_id(self):
        router, memory = self.make_router()

        tasks = router.route("CR400AFZ2333最近跑什么交路？", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "emu")
        self.assertEqual(tasks[0]["params"]["id"], "CR400AFZ2333")
        self.assertEqual(router.llm.generate_called, 0)


    def test_fast_router_contextual_smartemu_followup_reuses_recent_trains(self):
        router, memory = self.make_router()
        memory.update_from_tasks(
            [
                {
                    "action": "query",
                    "params": {
                        "domain": "railway",
                        "object": "s2s_benchmark",
                        "id": f"{NANJING_SOUTH}-{XUZHOU_EAST}",
                        "date": "2026-03-31",
                    },
                }
            ]
        )
        memory.update_from_facts(
            {
                "queries": [
                    {
                        "domain": "railway",
                        "object": "left_ticket_s2s",
                        "id": f"{NANJING_SOUTH}-{XUZHOU_EAST}",
                        "date": "2026-03-31",
                        "pretty": "G20 15:02->16:11\nG80 15:10->16:19\nG42 15:06->16:15",
                    }
                ]
            }
        )
        memory.add_ai_message("我刚才推荐 G20、G80、G42 这几班。")

        tasks = router.route("你推荐的这几班车里智能动车的使用情况如何？", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "smartemu_analysis")
        self.assertEqual(tasks[0]["params"]["id"], "G20,G80,G42")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_ticket_query_with_preferred_train_stays_route_level_and_adds_train_context(self):
        router, memory = self.make_router()

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("我想知道从南京南到徐州东，3.31还有余票吗？我比较喜欢G20", memory)

        self.assertTrue(
            any(
                task.get("action") == "query"
                and task.get("params", {}).get("object") == "left_ticket_s2s"
                and task.get("params", {}).get("id") == f"{NANJING_SOUTH}-{XUZHOU_EAST}"
                and task.get("params", {}).get("date") == "2026-03-31"
                for task in tasks
            )
        )
        self.assertTrue(
            any(
                task.get("action") == "query"
                and task.get("params", {}).get("object") in {"train", "path_detail"}
                and task.get("params", {}).get("id") == "G20"
                for task in tasks
            )
        )
        self.assertEqual(router.llm.generate_called, 0)

    def test_multi_train_emu_usage_analysis_prefers_smartemu_tool(self):
        router, memory = self.make_router()

        tasks = router.route("请分析G7-G20-G33的动车组使用情况", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "smartemu_analysis")
        self.assertEqual(tasks[0]["params"]["id"], "G7,G20,G33")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_single_train_assignment_prefers_train_tool(self):
        router, memory = self.make_router()

        tasks = router.route("G1次今天用什么车底？是长编组还是短编组？是智能动车组吗？", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "train")
        self.assertEqual(tasks[0]["params"]["id"], "G1")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_expands_train_range_for_batch_assignment_analysis(self):
        router, memory = self.make_router()

        tasks = router.route("能不能用你的数据告诉我，全国高铁里，车次号最靓的一组，比如G1到G10，现在都是什么车型在跑？", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "smartemu_analysis")
        self.assertEqual(
            tasks[0]["params"]["id"],
            "G1,G2,G3,G4,G5,G6,G7,G8,G9,G10",
        )
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_contextual_social_reply_routes_to_chat_without_pending(self):
        router, memory = self.make_router()
        memory.add_ai_message("南京南站是华东很重要的铁路枢纽，车次密度也很高。")

        tasks = router.route("这么厉害的吗？", memory)

        self.assertEqual(tasks[0]["action"], "chat")
        self.assertIn("contextual social chat", tasks[0]["params"]["message"].lower())
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_contextual_feature_followup_routes_to_chat(self):
        router, memory = self.make_router()
        memory.update_from_facts(
            {
                "queries": [
                    {
                        "domain": "railway",
                        "object": "left_ticket_s2s",
                        "id": f"{NANJING_SOUTH}-{XUZHOU_EAST}",
                        "date": "2026-03-31",
                        "pretty": "G20 15:02->16:11\nG80 15:10->16:19\nG42 15:06->16:15",
                    }
                ]
            }
        )
        memory.add_ai_message("这几班车都比较快，其中 G20、G80、G42 是我刚才重点推荐的。")

        tasks = router.route("这些车次都有什么特点呢？", memory)

        self.assertEqual(tasks[0]["action"], "chat")
        self.assertIn("contextual chat", tasks[0]["params"]["message"].lower())
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_contextual_reasoning_followup_routes_to_chat(self):
        router, memory = self.make_router()
        memory.update_from_tasks(
            [
                {
                    "action": "query",
                    "params": {
                        "domain": "railway",
                        "object": "path_detail",
                        "id": "G73",
                        "date": "2026-03-24",
                    },
                },
                {
                    "action": "query",
                    "params": {
                        "domain": "railway",
                        "object": "path_detail",
                        "id": "G71",
                        "date": "2026-03-24",
                    },
                },
            ]
        )
        memory.add_ai_message(
            "\u6211\u521a\u624d\u5df2\u7ecf\u5bf9\u6bd4\u8fc7 G73 \u548c G71 \u7684\u505c\u7ad9\u548c\u8fd0\u884c\u533a\u95f4\u4e86\u3002"
        )

        tasks = router.route(
            "\u554a\uff01\uff01\uff01\u8fd9\u662f\u56e0\u4e3a\u4eac\u5c40\u7684\u8def\u6743\u66f4\u9ad8\u5417\uff1f\u611f\u89c9\u8fd9\u4e24\u73ed\u8f66\u90fd\u662f\u6807\u6746\u8f66",
            memory,
        )

        self.assertEqual(tasks[0]["action"], "chat")
        self.assertIn("contextual chat", tasks[0]["params"]["message"].lower())
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_plus_skips_context_agent_when_route_anchor_is_already_sufficient(self):
        router, memory = self.make_router()
        router.set_mode("fast-plus")
        router.llm.response = (
            '{"has_date":true,"normalized_date":"2026-03-25",'
            '"date_source":"conversation_context","date_span":"context",'
            '"is_contextual_date":true,"confidence":90,"reason":"context date"}'
        )
        memory.update_anchor(
            route="北京南-上海虹桥",
            dep="北京南",
            arr="上海虹桥",
            date="2026-03-25",
            query_type="s2s_benchmark",
            query_object="s2s_benchmark",
            source="manual",
        )
        router.context_agent = DummyContextAgent(
            {
                "intent_category": "route_benchmark",
                "rewritten_user_text": "请查询 2026-03-25 北京南到上海虹桥有什么标杆车",
                "resolved_route": "北京南-上海虹桥",
                "resolved_train_numbers": [],
                "resolved_emu": "",
                "resolved_date": "2026-03-25",
                "resolved_station_mentions": ["北京南", "上海虹桥"],
                "resolved_query_object": "s2s_benchmark",
                "confidence": 95,
                "reason": "reuse previous route benchmark intent",
            }
        )

        tasks = router.route("有什么标杆车？", memory)

        self.assertEqual(router.context_agent.calls, 0)
        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "s2s_benchmark")
        self.assertEqual(tasks[0]["params"]["id"], "北京南-上海虹桥")
        self.assertEqual(tasks[0]["params"]["date"], "2026-03-25")
        self.assertEqual(router.llm.generate_called, 1)

    def test_fast_plus_explicit_route_bypasses_context_agent_and_queries_directly(self):
        router, memory = self.make_router()
        router.set_mode("fast-plus")
        router.context_agent = DummyContextAgent(
            {
                "intent_category": "route_benchmark",
                "rewritten_user_text": "请查询 2026-03-25 北京南到上海虹桥有什么标杆车",
                "resolved_route": "北京南-上海虹桥",
                "resolved_train_numbers": [],
                "resolved_emu": "",
                "resolved_date": "2026-03-25",
                "resolved_station_mentions": ["北京南", "上海虹桥"],
                "resolved_query_object": "s2s_benchmark",
                "confidence": 95,
                "reason": "reuse previous route benchmark intent",
            }
        )

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route(f"{NANJING_SOUTH}到{XUZHOU_EAST}最快的车", memory)

        self.assertEqual(router.context_agent.calls, 0)
        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "s2s_benchmark")
        self.assertEqual(tasks[0]["params"]["id"], f"{NANJING_SOUTH}-{XUZHOU_EAST}")
        self.assertEqual(tasks[0]["params"]["date"], "2026-03-24")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_go_followup_can_still_use_context_agent_when_local_resolution_is_incomplete(self):
        router, memory = self.make_router()
        router.set_mode("fast-go")
        router.context_agent = DummyContextAgent(
            {
                "intent_category": "route_ticket",
                "rewritten_user_text": "请查询南京南到上海今天的余票情况",
                "resolved_route": "南京南-上海",
                "resolved_train_numbers": [],
                "resolved_emu": "",
                "resolved_date": "2026-03-24",
                "resolved_station_mentions": ["南京南", "上海"],
                "resolved_query_object": "left_ticket_s2s",
                "confidence": 93,
                "reason": "resolve follow-up ticket intent",
            }
        )

        memory.update_anchor(
            route="南京南-上海",
            dep="南京南",
            arr="上海",
            date="2026-03-24",
            query_type="station_to_station_mini",
            query_object="station_to_station_mini",
            source="manual",
        )
        memory.enter_followup(
            question="你还想继续查哪一部分？",
            slot=["focus"],
            context={"route": "南京南-上海"},
        )

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("余票呢？", memory)

        self.assertEqual(router.context_agent.calls, 1)
        self.assertEqual(router.llm.generate_called, 0)
        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "left_ticket_s2s")
        self.assertEqual(tasks[0]["params"]["id"], "南京南-上海")
        self.assertEqual(tasks[0]["params"]["date"], "2026-03-24")

    def test_context_agent_rewritten_explicit_date_beats_stale_resolved_date(self):
        router, memory = self.make_router()
        context = router._build_fast_route_context(
            "福州到南京南2026-05-05还有哪些车有余票？",
            session=memory,
            context_agent_result={
                "intent_category": "route_ticket",
                "rewritten_user_text": "福州到南京南2026-05-05还有哪些车有余票？",
                "resolved_route": "福州-南京南",
                "resolved_train_numbers": [],
                "resolved_emu": "",
                "resolved_date": "2026-04-27",
                "resolved_station_mentions": ["福州", "南京南"],
                "resolved_query_object": "left_ticket_s2s",
                "confidence": 84,
                "reason": "heuristic route ticket follow-up",
            },
        )

        self.assertEqual(context["query_date"], "2026-05-05")
        self.assertEqual(context["route"], "福州-南京南")

    def test_context_agent_bad_today_rewrite_cannot_override_raw_explicit_date(self):
        router, memory = self.make_router()
        with patch("agent.router.datetime", FixedDateTime):
            context = router._build_fast_route_context(
                "福州到南京南2026-05-05还有哪些车有余票？",
                session=memory,
                context_agent_result={
                    "intent_category": "route_ticket",
                    "rewritten_user_text": "请查询福州-南京南在今天的余票情况。",
                    "resolved_route": "福州-南京南",
                    "resolved_train_numbers": [],
                    "resolved_emu": "",
                    "resolved_date": "2026-03-24",
                    "resolved_station_mentions": ["福州", "南京南"],
                    "resolved_query_object": "left_ticket_s2s",
                    "confidence": 84,
                    "reason": "bad rewrite",
                },
            )

        self.assertEqual(context["query_date"], "2026-05-05")
        self.assertEqual(context["query_date_source"], "explicit_user_raw")

    def test_fast_router_parses_short_dot_date_with_suffix_for_left_ticket(self):
        router, memory = self.make_router()

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("福州到南京南5.5号还有哪些车有余票？", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "left_ticket_s2s")
        self.assertEqual(tasks[0]["params"]["id"], "福州-南京南")
        self.assertEqual(tasks[0]["params"]["date"], "2026-05-05")

    def test_fast_plus_explicit_date_comes_from_date_normalizer_llm(self):
        router, memory = self.make_router(
            response=(
                '{"has_date":true,"normalized_date":"2026-05-05",'
                '"date_source":"latest_user","date_span":"5.5号",'
                '"is_contextual_date":false,"confidence":96,"reason":"explicit user date"}'
            )
        )
        router.set_mode("fast-plus")

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("福州到南京南5.5号还有哪些车有余票？", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "left_ticket_s2s")
        self.assertEqual(tasks[0]["params"]["id"], "福州-南京南")
        self.assertEqual(tasks[0]["params"]["date"], "2026-05-05")
        self.assertEqual(router.llm.generate_called, 1)

    def test_fast_plus_context_agent_can_rewrite_assignment_followup(self):
        router, memory = self.make_router()
        router.set_mode("fast-plus")
        router.context_agent = DummyContextAgent(
            {
                "intent_category": "train_assignment",
                "rewritten_user_text": "请查询推荐的 G20,G80 这几班车里面智能动车的使用情况",
                "resolved_route": f"{NANJING_SOUTH}-{XUZHOU_EAST}",
                "resolved_train_numbers": ["G20", "G80"],
                "resolved_emu": "",
                "resolved_date": "2026-03-31",
                "resolved_station_mentions": [NANJING_SOUTH, XUZHOU_EAST],
                "resolved_query_object": "train",
                "confidence": 93,
                "reason": "resolve recommended train follow-up",
            }
        )
        memory.update_from_facts(
            {
                "queries": [
                    {
                        "domain": "railway",
                        "object": "left_ticket_s2s",
                        "id": f"{NANJING_SOUTH}-{XUZHOU_EAST}",
                        "date": "2026-03-31",
                        "pretty": "G20 15:02->16:11\nG80 15:10->16:19",
                    }
                ]
            }
        )
        memory.add_ai_message("我刚才推荐 G20、G80 这几班。")

        tasks = router.route("你推荐的这几班车里智能动车的使用情况如何？", memory)

        self.assertEqual(router.context_agent.calls, 0)
        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "smartemu_analysis")
        self.assertIn("G20", tasks[0]["params"]["id"])
        self.assertIn("G80", tasks[0]["params"]["id"])

    def test_fast_router_runtime_status_question_opens_live_tool_arbitration(self):
        router, memory = self.make_router()

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("查一下今天（或历史上某天）G2次列车的实时位置，以及它晚点了多久？", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "path_detail")
        self.assertEqual(tasks[0]["params"]["id"], "G2")
        self.assertEqual(router.llm.generate_called, 0)
        self.assertEqual(router.llm.semantic_generate_called, 1)

    def test_fast_router_explicit_train_city_line_check_prefers_path_detail(self):
        router, memory = self.make_router()

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("\u4f60\u5e2e\u6211\u67e5\u4e00\u4e0b\u8fd9\u4e2a\u662f\u4e0d\u662f\u4eac\u6d25\u57ce\u9645\u7684\u8f66\u6b21G6742", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "path_detail")
        self.assertEqual(tasks[0]["params"]["id"], "G6742")
        self.assertEqual(tasks[0]["params"]["date"], "2026-03-24")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_stop_history_question_prefers_stopcheck_tool(self):
        router, memory = self.make_router()

        tasks = router.route("G1次最早是不是只停南京南一站？它是什么时候开始加停济南西、天津南的？", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "path_stopcheck")
        self.assertEqual(tasks[0]["params"]["id"], "G1|南京南,济南西,天津南")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_ticket_validation_for_explicit_train_prefers_ticket_tools_when_route_known(self):
        router, memory = self.make_router()
        memory.update_anchor(
            route="上海虹桥-北京南",
            dep="上海虹桥",
            arr="北京南",
            date="2026-03-24",
            query_type="left_ticket_s2s",
            query_object="left_ticket_s2s",
            source="manual",
        )

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("你这些数据准不准啊？请用12306实时余票数据验证你刚才说的G1次今天商务座已售罄。", memory)

        self.assertTrue(
            any(
                task.get("action") == "query"
                and task.get("params", {}).get("object") == "left_ticket_s2s"
                and task.get("params", {}).get("id") == "上海虹桥-北京南"
                for task in tasks
            )
        )
        self.assertTrue(
            any(
                task.get("action") == "query"
                and task.get("params", {}).get("object") == "path_detail"
                and task.get("params", {}).get("id") == "G1"
                for task in tasks
            )
        )
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_station_transfer_question_prefers_chat_not_pending(self):
        router, memory = self.make_router()

        tasks = router.route("从南京南站换乘，同一个站台面，半小时内能接上哪些方向的车？给我一个最快的接续方案。", memory)

        self.assertEqual(tasks[0]["action"], "chat")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_station_photography_question_prefers_chat_not_pending(self):
        router, memory = self.make_router()

        tasks = router.route("我想在南京南站拍车（拍摄列车），哪个站台能看到最多不同方向、不同车型的列车？", memory)

        self.assertEqual(tasks[0]["action"], "chat")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_broad_railfan_and_future_questions_prefer_chat(self):
        samples = (
            "我想坐“豹子号”车次，除了G666，还有哪些D/K/Z/T字头的666次列车？",
            "如果京沪二线全线通车，会对现有京沪高铁的标杆车运行图产生什么影响？",
            "二等座的“B塞”和“B智”到底有什么区别？真的是定员从80涨到95吗？座椅间距差了多少？",
            "传说中的“金凤凰”、“海豚”、“带鱼”这些外号，分别对应的是哪些车型？",
            "如果未来所有车都换成CR450，京沪高铁的运行时间有可能压缩到4小时以内吗？",
            "假设要开行“京沪夜间动卧”，你觉得现有的运行图该怎么调整才能给它腾出“天窗”？",
            "从技术上讲，有没有可能在不停车的情况下，完成动车组从“重联”到“解编”的操作？",
            "你认为未来AI调度系统（比如你）的终极形态，能够实现“无人化”的列车运行和故障处理吗？",
        )

        for sample in samples:
            with self.subTest(sample=sample):
                router, memory = self.make_router()
                tasks = router.route(sample, memory)
                self.assertEqual(tasks[0]["action"], "chat")
                self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_additional_real_railfan_questions_prefer_chat(self):
        samples = (
            "同样是350时速，为什么有的车只跑250？是线路原因还是调度原因？",
            "CRH380B和CR400系列，乘坐体验最明显的三个区别是什么？",
            "车迷怎么一眼认出车次，除了看水牌还有什么办法？",
            "京沪高铁哪一天、哪个时段最不容易晚点？",
        )

        for sample in samples:
            with self.subTest(sample=sample):
                router, memory = self.make_router()
                tasks = router.route(sample, memory)
                self.assertEqual(tasks[0]["action"], "chat")

    def test_fast_router_explicit_train_history_questions_prefer_chat_not_pending(self):
        samples = (
            "G10次是京沪高铁上唯一用16节长编组运行的标杆车吗？",
            "G1次最早是不是只停南京南一站？它是什么时候开始加停济南西/天津南的？",
            "你这些数据准不准啊？请用12306实时余票数据验证你刚才说的G1次今天商务座已售罄。",
        )

        for sample in samples:
            with self.subTest(sample=sample):
                router, memory = self.make_router()
                tasks = router.route(sample, memory)
                self.assertNotEqual(tasks[0]["action"], "pending")
                self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_affirmative_followup_without_missing_slots_reuses_route_context(self):
        router, memory = self.make_router()
        router.set_mode("fast-go")
        memory.update_anchor(
            route="上海虹桥-北京南",
            dep="上海虹桥",
            arr="北京南",
            date="2026-03-25",
            query_type="smartemu_analysis",
            query_object="smartemu_analysis",
            source="manual",
        )
        memory.enter_followup(
            question="如果你需要，我现在就可以为你查询明天上海虹桥到北京南的全天车次列表。",
            slot=[],
            context={"route": "上海虹桥-北京南", "date": "2026-03-25"},
        )

        with patch("agent.router.datetime", FixedDateTime):
            tasks = router.route("ok", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "station_to_station_future")
        self.assertEqual(tasks[0]["params"]["id"], "上海虹桥-北京南")
        self.assertEqual(tasks[0]["params"]["date"], "2026-03-25")
        self.assertEqual(router.llm.generate_called, 0)


    def test_fast_router_line_station_affiliation_question_prefers_chat(self):
        router, memory = self.make_router()

        tasks = router.route("北京哪个火车站是京广高铁的车站", memory)

        self.assertEqual(tasks[0]["action"], "chat")
        self.assertEqual(router.llm.generate_called, 0)

    def test_fast_router_semantic_pending_is_rechecked_by_fast_llm(self):
        router, memory = self.make_router(
            response='{"action":"chat","params":{"message":"chat fallback"}}'
        )
        router.set_mode("fast-go")
        router._try_fast_route = lambda *args, **kwargs: [
            {
                "action": "pending",
                "params": {
                    "question": "请补充一下",
                    "slot": [],
                    "context": {},
                },
            }
        ]

        tasks = router.route("北京哪个火车站是京广高铁的车站", memory)

        self.assertEqual(router.llm.generate_called, 1)
        self.assertEqual(tasks[0]["action"], "chat")

    def test_fast_router_short_contextual_explanation_followup_is_rechecked_by_fast_llm(self):
        router, memory = self.make_router(
            response='{"action":"chat","params":{"message":"contextual explanation"}}'
        )
        router.set_mode("fast-go")
        memory.update_from_tasks(
            [
                {
                    "action": "query",
                    "params": {
                        "domain": "railway",
                        "object": "station_to_station_mini",
                        "id": "北京南-天津",
                        "date": "2026-03-24",
                    },
                }
            ]
        )
        memory.add_ai_message("北京南到天津今天有很多班次，全天发车很密。")
        router._try_fast_route = lambda *args, **kwargs: [
            {
                "action": "pending",
                "params": {
                    "question": "请补充一下",
                    "slot": [],
                    "context": {},
                },
            }
        ]

        tasks = router.route("所以为啥会有这么多车", memory)

        self.assertEqual(router.llm.generate_called, 1)
        self.assertEqual(tasks[0]["action"], "chat")

    def test_router_class_has_no_duplicate_helper_definitions(self):
        router_path = Path("agent/router.py")
        tree = ast.parse(router_path.read_text(encoding="utf-8"), filename=str(router_path))
        router_class = next(
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Router"
        )
        seen = {}
        duplicates = {}
        for item in router_class.body:
            if not isinstance(item, ast.FunctionDef):
                continue
            if item.name in seen:
                duplicates.setdefault(item.name, [seen[item.name]]).append(item.lineno)
            else:
                seen[item.name] = item.lineno
        self.assertEqual(duplicates, {})


if __name__ == "__main__":
    unittest.main()
