import json
import unittest
from datetime import datetime
from unittest.mock import patch

from agent.answer_generator import AnswerGenerator
from agent.app import RailwayAgentApp
from agent.executor import EXECUTOR_IMPLEMENTED_OBJECTS
from agent.capabilities import (
    TOOL_CAPABILITY_REGISTRY,
    build_intent_envelope,
    build_missing_slot_contract,
    capability_catalog_for_mode,
    executable_capability_objects,
    get_capability,
    missing_required_evidence,
    resolve_query_id,
    routable_capability_objects,
)
from agent.router import Router
from memory.session import SessionMemory


class _FallbackLLM:
    def __init__(self, response="not-json", raises=False):
        self.response = response
        self.raises = raises
        self.mode = "fast"

    def get_mode(self):
        return self.mode

    def set_mode(self, mode):
        self.mode = mode

    def generate(self, _messages, timeout=None, max_retries=None):
        if self.raises:
            raise TimeoutError("semantic council timeout")
        return self.response

    def stream_generate(self, _messages):
        yield "ok"


class _FixedCurrentDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 7, 15, 12, 0, 0)


class _RecordingExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, plan):
        objects = [step["params"]["object"] for step in plan]
        self.calls.append(objects)
        queries = []
        for step in plan:
            params = step["params"]
            object_name = params["object"]
            evidence = [{"stationName": "徐州东"}] if object_name == "train_delay" else [{"station": "徐州东"}]
            queries.append({
                "object": object_name,
                "id": params["id"],
                "evidence": evidence,
            })
        return {
            "queries": queries,
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }


class CapabilityContractTest(unittest.TestCase):
    def test_registry_is_the_shared_capability_source(self):
        self.assertEqual(TOOL_CAPABILITY_REGISTRY.get("train_delay"), get_capability("train_delay"))
        self.assertIn("left_ticket_s2s", TOOL_CAPABILITY_REGISTRY.objects())

    def test_delay_requires_train_but_not_od(self):
        capability = get_capability("train_delay")
        self.assertEqual(capability.required_slots, ("train",))
        self.assertIn("dep", capability.optional_slots)
        self.assertIn("arr", capability.optional_slots)
        self.assertEqual(capability.required_evidence, ("train_delay",))

    def test_ticket_requires_od_and_date(self):
        capability = get_capability("left_ticket_s2s")
        self.assertEqual(capability.required_slots, ("dep", "arr", "date"))

    def test_every_required_slot_has_capability_aware_clarification_metadata(self):
        for object_name in TOOL_CAPABILITY_REGISTRY.objects():
            capability = get_capability(object_name)
            contract = build_missing_slot_contract(
                object_name,
                capability.required_slots,
                {},
            )
            with self.subTest(object_name=object_name):
                self.assertEqual(contract["capability"], object_name)
                self.assertEqual(contract["missing_slots"], list(capability.required_slots))
                self.assertEqual(len(contract["questions"]), len(capability.required_slots))
                self.assertTrue(all(item["guidance"] for item in contract["questions"]))
                self.assertTrue(all(item["fallback_question"] for item in contract["questions"]))

    def test_every_declared_default_is_schema_visible_and_never_required(self):
        for object_name in TOOL_CAPABILITY_REGISTRY.objects():
            capability = get_capability(object_name)
            manifest = capability.mcp_manifest("l1")
            for slot, default in capability.slot_defaults:
                with self.subTest(object_name=object_name, slot=slot):
                    self.assertIn(slot, capability.optional_slots)
                    self.assertNotIn(slot, capability.required_slots)
                    self.assertEqual(
                        manifest["inputSchema"]["properties"][slot]["default"],
                        default,
                    )

    def test_operational_capabilities_declare_only_their_own_defaults(self):
        board = get_capability("station_board")
        access = get_capability("train_station_access")
        ticket = get_capability("left_ticket_s2s")

        self.assertEqual(board.required_slots, ("station",))
        self.assertEqual(dict(board.slot_defaults), {"direction": "departure"})
        self.assertEqual(access.required_slots, ("train", "station"))
        self.assertEqual(
            dict(access.slot_defaults),
            {"date": "today", "direction": "departure"},
        )
        self.assertEqual(dict(ticket.slot_defaults), {})

    def test_required_ticket_date_rejects_router_clock_default(self):
        envelope = build_intent_envelope(
            "left_ticket_s2s",
            {
                "dep": "南京南",
                "arr": "上海",
                "query_date": "2026-07-16",
                "query_date_source": "default_today",
            },
        )
        self.assertEqual(envelope.missing_slots, ["date"])

    def test_user_date_satisfies_required_ticket_date(self):
        envelope = build_intent_envelope(
            "left_ticket_s2s",
            {
                "dep": "南京南",
                "arr": "上海",
                "query_date": "2026-07-17",
                "query_date_source": "date_normalizer:latest_user",
            },
        )
        self.assertEqual(envelope.missing_slots, [])

    def test_every_capability_has_a_complete_mcp_style_selection_contract(self):
        for object_name in TOOL_CAPABILITY_REGISTRY.objects():
            capability = get_capability(object_name)
            with self.subTest(object_name=object_name):
                self.assertIn(capability.kind, {"tool", "workflow"})
                self.assertTrue(capability.id_format)
                self.assertTrue(capability.choose_when)
                self.assertTrue(capability.avoid_when)
                manifest = capability.mcp_manifest("l1")
                self.assertEqual(manifest["name"], object_name)
                self.assertEqual(manifest["inputSchema"]["type"], "object")
                self.assertEqual(
                    manifest["inputSchema"]["required"],
                    list(capability.required_slots),
                )

    def test_workflow_steps_reference_only_executable_capabilities(self):
        executable = executable_capability_objects()
        for object_name in TOOL_CAPABILITY_REGISTRY.workflow_objects():
            capability = get_capability(object_name)
            with self.subTest(object_name=object_name):
                self.assertTrue(set(capability.workflow).issubset(executable))
                self.assertEqual(len(capability.workflow), len(capability.workflow_inputs))
                self.assertEqual(len(capability.workflow), len(capability.workflow_conditions))

    def test_registry_tool_manifests_match_executor_dispatch_coverage(self):
        registered_tools = {
            name
            for name in TOOL_CAPABILITY_REGISTRY.objects()
            if get_capability(name).kind == "tool"
        }
        self.assertEqual(registered_tools, EXECUTOR_IMPLEMENTED_OBJECTS)

    def test_fast_modes_compile_different_views_from_the_same_registry(self):
        fast_go = capability_catalog_for_mode("fast-go")
        fast_plus = capability_catalog_for_mode("fast-plus")
        self.assertIn("level=l0", fast_go)
        self.assertIn("level=l1", fast_plus)
        self.assertIn("train_overview [workflow]", fast_go)
        self.assertIn("examples=G3次列车有什么特色", fast_plus)
        self.assertGreater(len(fast_plus), len(fast_go))

    def test_temporarily_disabled_visual_capabilities_are_not_discoverable_or_executable(self):
        catalog = capability_catalog_for_mode("fast-plus")
        for object_name in ("coach_layout", "train_route_map"):
            with self.subTest(object_name=object_name):
                self.assertNotIn(object_name, routable_capability_objects())
                self.assertNotIn(object_name, executable_capability_objects())
                self.assertNotIn(object_name, catalog)

    def test_stopcheck_query_id_cannot_drop_explicit_user_stations(self):
        query_id = resolve_query_id(
            "path_stopcheck",
            {
                "train_numbers": ["G1"],
                "station_mentions": ["南京南", "济南西", "天津南"],
            },
            suggested_id="G1|南京南,济南西",
        )
        self.assertEqual(query_id, "G1|南京南,济南西,天津南")

    def test_semantic_council_uses_l0_for_fast_go_and_l1_for_fast_plus(self):
        router = Router(SessionMemory())
        context = {
            "raw_text": "G3有什么特色",
            "text": "G3有什么特色",
            "agent_context_package": {},
            "explicit_train_numbers": ["G3"],
        }
        router.set_mode("fast-go")
        fast_go_prompt = router._build_semantic_router_council_messages(context)[0]["content"]
        router.set_mode("fast-plus")
        fast_plus_prompt = router._build_semantic_router_council_messages(context)[0]["content"]
        self.assertIn("level=l0", fast_go_prompt)
        self.assertIn("level=l1", fast_plus_prompt)
        self.assertIn("examples=G3次列车有什么特色", fast_plus_prompt)

    def test_train_overview_is_a_parallel_composite_capability(self):
        capability = get_capability("train_overview")
        self.assertEqual(capability.required_slots, ("train",))
        self.assertEqual(capability.required_evidence, ("path_detail", "train"))
        self.assertEqual(capability.workflow, ("path_detail", "train"))
        self.assertEqual(capability.execution_strategy, "parallel")

        envelope = build_intent_envelope(
            "train_overview",
            {"train_numbers": ["G3"], "query_date": "2026-07-16"},
            confidence=98,
        )
        self.assertEqual(envelope.missing_slots, [])
        self.assertEqual(envelope.workflow, ["path_detail", "train"])
        self.assertEqual(envelope.execution_strategy, "parallel")

    def test_route_train_benchmark_uses_tool_rating_and_supporting_evidence(self):
        capability = get_capability("route_train_benchmark")
        self.assertEqual(capability.required_slots, ("train", "dep", "arr"))
        self.assertEqual(
            capability.required_evidence,
            ("s2s_benchmark", "path_detail", "train"),
        )
        self.assertEqual(capability.execution_strategy, "parallel")

    def test_path_evidence_cannot_satisfy_delay(self):
        envelope = build_intent_envelope(
            "train_delay",
            {"train_numbers": ["G813"], "raw_text": "G813今天晚点吗"},
            confidence=98,
        )
        facts = {"queries": [{"object": "path_detail", "id": "G813", "evidence": [{}]}]}
        self.assertEqual(missing_required_evidence(envelope, facts), ["train_delay"])

    def test_empty_delay_record_is_not_positive_evidence(self):
        envelope = build_intent_envelope(
            "train_delay",
            {"train_numbers": ["G813"], "raw_text": "G813今天晚点吗"},
            confidence=98,
        )
        facts = {"queries": [{"object": "train_delay", "id": "G813", "evidence": []}]}
        self.assertEqual(missing_required_evidence(envelope, facts), ["train_delay"])


class DelayRoutingRegressionTest(unittest.TestCase):
    def make_router(self, llm=None):
        memory = SessionMemory()
        router = Router(memory)
        router.llm = llm or _FallbackLLM()
        return router, memory

    def test_train_only_delay_builds_path_then_live_delay_workflow(self):
        router, memory = self.make_router()
        tasks = router.route("G813今天有没有晚点？", memory)
        self.assertEqual(
            [task["params"]["object"] for task in tasks],
            ["path_detail", "train_delay"],
        )
        envelope = router.get_last_intent_envelope()
        self.assertEqual(envelope["selected_capability"], "train_delay")
        self.assertEqual(envelope["workflow"], ["path_detail", "train_delay"])
        self.assertEqual(envelope["missing_slots"], [])

    def test_semantic_council_routes_train_features_to_overview_workflow(self):
        vote = {
            "agent": "tool_intent_agent",
            "intent": "train_overview",
            "preferred_action": "query",
            "confidence": 98,
            "reason": "the user asks for a combined profile of one explicit train",
            "required_object": "train_overview",
            "query_id": "G3",
            "query_date": "2026-07-16",
        }
        response = json.dumps(
            {"votes": [vote], "consensus": vote, "conflict": False},
            ensure_ascii=False,
        )
        router, memory = self.make_router(_FallbackLLM(response=response))
        tasks = router.route("G3次列车有什么特色！？", memory)

        self.assertEqual(
            [task["params"]["object"] for task in tasks],
            ["path_detail", "train"],
        )
        envelope = router.get_last_intent_envelope()
        self.assertEqual(envelope["selected_capability"], "train_overview")
        self.assertEqual(envelope["required_evidence"], ["path_detail", "train"])
        self.assertEqual(envelope["execution_strategy"], "parallel")

    def test_semantic_council_routes_named_train_benchmark_verification_to_three_evidence_sources(self):
        vote = {
            "agent": "tool_intent_agent",
            "intent": "route_train_benchmark",
            "preferred_action": "query",
            "confidence": 98,
            "reason": "benchmark status must come from the route rating tool",
            "required_object": "route_train_benchmark",
            "query_id": "G3089",
            "query_date": "2026-07-16",
        }
        response = json.dumps(
            {"votes": [vote], "consensus": vote, "conflict": False},
            ensure_ascii=False,
        )
        router, memory = self.make_router(_FallbackLLM(response=response))
        tasks = router.route("G3089是不是南京南到福州的标杆车？", memory)

        self.assertEqual(
            [task["params"]["object"] for task in tasks],
            ["s2s_benchmark", "path_detail", "train"],
        )
        self.assertEqual(tasks[0]["params"]["id"], "南京南-福州")
        self.assertEqual(tasks[1]["params"]["id"], "G3089")
        envelope = router.get_last_intent_envelope()
        self.assertEqual(envelope["selected_capability"], "route_train_benchmark")
        self.assertEqual(
            envelope["required_evidence"],
            ["s2s_benchmark", "path_detail", "train"],
        )

    def test_semantic_city_od_expansion_is_bounded_and_manifest_driven(self):
        vote = {
            "agent": "tool_intent_agent",
            "intent": "route_listing",
            "preferred_action": "query",
            "confidence": 96,
            "reason": "ordinary OD listing with city-level endpoints",
            "required_object": "station_to_station_mini",
            "query_id": "南京-福州",
            "query_ids": ["南京-福州", "南京南-福州", "南京南-福州南", "虚构站-福州"],
        }
        response = json.dumps(
            {"votes": [vote], "consensus": vote, "conflict": False},
            ensure_ascii=False,
        )
        router, memory = self.make_router(_FallbackLLM(response=response))
        tasks = router.route("南京到福州有哪些车？", memory)
        ids = [task["params"]["id"] for task in tasks if task.get("action") == "query"]
        self.assertEqual(ids, ["南京-福州", "南京南-福州", "南京南-福州南"])
        self.assertNotIn("虚构站-福州", ids)

    def test_semantic_city_expansion_cannot_replace_explicit_hub_station(self):
        vote = {
            "agent": "tool_intent_agent",
            "intent": "route_benchmark",
            "preferred_action": "query",
            "confidence": 96,
            "reason": "benchmark OD",
            "required_object": "s2s_benchmark",
            "query_id": "南京南-福州",
            "query_ids": ["南京南-福州", "南京-福州", "南京南-福州南"],
        }
        response = json.dumps(
            {"votes": [vote], "consensus": vote, "conflict": False},
            ensure_ascii=False,
        )
        router, memory = self.make_router(_FallbackLLM(response=response))

        tasks = router.route("从南京南到福州有什么标杆车？", memory)

        ids = [task["params"]["id"] for task in tasks if task.get("action") == "query"]
        self.assertEqual(ids, ["南京南-福州", "南京南-福州南"])

    def test_alternative_departure_stations_are_not_parsed_as_od_pair(self):
        router, _memory = self.make_router()

        candidates = router._extract_route_candidates("再查查厦门北或者厦门出发的吧")

        self.assertEqual(candidates, [])

    def test_ambiguous_station_alternatives_use_context_agent_instead_of_local_pending(self):
        router, memory = self.make_router()
        router.set_mode("fast-go")
        memory.add_user_message("五月五号从福州到南京南还有余票吗？")
        memory.add_ai_message("已查询福州到南京南的余票。")
        latest = "再查查厦门北或者厦门出发的吧"
        memory.add_user_message(latest)
        context = router._build_fast_route_context(latest, session=memory, context_agent_result={})

        should_run, reason = router._should_run_context_agent(latest, memory, context)

        self.assertFalse(context.get("explicit_route"))
        self.assertTrue(should_run, reason)

    def test_semantic_query_id_populates_envelope_slots_before_validation(self):
        vote = {
            "agent": "tool_intent_agent",
            "intent": "route_ticket",
            "preferred_action": "query",
            "confidence": 97,
            "reason": "confirmed prior ticket query",
            "required_object": "left_ticket_s2s",
            "query_id": "厦门-南京南",
            "query_date": "2026-05-05",
            "grounded_slots": {
                "dep": "厦门",
                "arr": "南京南",
                "date": "2026-05-05",
            },
        }
        response = json.dumps(
            {"votes": [vote], "consensus": vote, "conflict": False},
            ensure_ascii=False,
        )
        router, memory = self.make_router(_FallbackLLM(response=response))
        router.set_mode("fast-go")
        memory.update_anchor(route="福州-南京南", date="2026-05-05", query_object="left_ticket_s2s")
        memory.add_user_message("再查厦门到南京南5月5日的余票")
        memory.add_ai_message("要继续查询厦门到南京南吗？")

        tasks = router.route("需要，快点查", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "left_ticket_s2s")
        self.assertEqual(tasks[0]["params"]["id"], "厦门-南京南")
        self.assertEqual(tasks[0]["params"]["date"], "2026-05-05")
        self.assertEqual(router.get_last_intent_envelope()["missing_slots"], [])

    def test_explicit_delay_segment_calls_delay_without_od_pending(self):
        router, memory = self.make_router()
        tasks = router.route("G813晚点了吗？徐州东到福州", memory)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["params"]["object"], "train_delay")
        self.assertEqual(tasks[0]["params"]["id"], "G813")
        self.assertEqual(
            router.get_last_intent_envelope()["scope"],
            {"dep": "徐州东", "arr": "福州"},
        )

    def test_semantic_timeout_cannot_downgrade_delay_to_path_only(self):
        router, memory = self.make_router(_FallbackLLM(raises=True))
        tasks = router.route("G813今天有没有晚点？", memory)
        self.assertEqual(
            [task["params"]["object"] for task in tasks],
            ["path_detail", "train_delay"],
        )

    def test_malformed_semantic_output_cannot_downgrade_delay_to_path_only(self):
        router, memory = self.make_router(_FallbackLLM(response="{"))
        tasks = router.route("G813今天有没有晚点？", memory)
        self.assertEqual(
            [task["params"]["object"] for task in tasks],
            ["path_detail", "train_delay"],
        )

    def test_delay_without_train_only_asks_for_train(self):
        router, memory = self.make_router()
        tasks = router.route("它现在晚点了吗？", memory)
        self.assertEqual(tasks[0]["action"], "pending")
        self.assertEqual(tasks[0]["params"]["slot"], ["train"])

    def test_broad_punctuality_question_stays_in_knowledge_chat(self):
        router, memory = self.make_router()
        tasks = router.route("京沪高铁哪个时段最不容易晚点？", memory)
        self.assertEqual(tasks[0]["action"], "chat")

    def test_pending_slot_inference_uses_selected_capability_only(self):
        router, _memory = self.make_router()
        delay_context = {
            "semantic_consensus": {
                "preferred_action": "query",
                "required_object": "train_delay",
            },
            "train_numbers": [],
        }
        self.assertEqual(router._infer_pending_slots_from_context(delay_context), ["train"])

        broad_travel_context = {
            "asks_travel_advice": True,
            "mentions_rail": True,
            "text": "沿着京沪高铁一路玩到滴水湖",
            "station_mentions": [],
        }
        self.assertEqual(router._infer_pending_slots_from_context(broad_travel_context), [])

    def test_travel_question_does_not_enter_od_pending(self):
        router, memory = self.make_router()
        tasks = router.route("去北京有什么好玩的？", memory)
        self.assertEqual(tasks[0]["action"], "chat")

    def test_future_delay_question_returns_capability_boundary_chat(self):
        router, memory = self.make_router()
        with patch("agent.router.datetime", _FixedCurrentDateTime):
            tasks = router.route("G813在2026-07-16会晚点吗？", memory)
        self.assertEqual(tasks[0]["action"], "chat")
        self.assertIn("current-only", tasks[0]["params"]["message"])

    def test_meta_followup_stays_with_previous_answer(self):
        router, memory = self.make_router()
        memory.add_user_message("G813今天有没有晚点？")
        memory.add_ai_message("我根据实时晚点数据核验了G813当前各站状态。")
        tasks = router.route("怎么回事这都知道", memory)
        self.assertEqual(tasks[0]["action"], "chat")

    def test_real_conversation_creative_followups_stay_in_chat(self):
        # Regression snapshots from conversations 007, 009 and 011.
        samples = (
            ("我的意思是根据G20这个素材来写！", "# G20次永恒号\n第一章：最后的上海虹桥", "需要！"),
            ("那写一篇散文或者小说吧，越长越好", "## 铁轨上的远方\n午后的南京南站。", "太短啦！再写长一点，10000字！"),
            ("请你根据这些信息写一篇散文或者小说", "# G1677次：一趟列车上的中国", "再长一点！"),
        )
        for previous_user, previous_answer, followup in samples:
            with self.subTest(followup=followup):
                router, memory = self.make_router()
                memory.add_user_message(previous_user)
                memory.add_ai_message(previous_answer)
                tasks = router.route(followup, memory)
                self.assertEqual(tasks[0]["action"], "chat")

    def test_real_conversation_meta_questions_stay_in_chat(self):
        # Regression snapshots from conversation 017.
        samples = (
            "为了回答这个问题，你进行了什么思考？请你展示你的思考链🌹",
            "系统是否返回冗余信息？",
        )
        for followup in samples:
            with self.subTest(followup=followup):
                router, memory = self.make_router()
                memory.add_user_message("G1次列车的运行路线")
                memory.add_ai_message("G1次运行于北京南至上海虹桥，以下是工具返回的站序。")
                tasks = router.route(followup, memory)
                self.assertEqual(tasks[0]["action"], "chat")


class AnswerContextIsolationTest(unittest.TestCase):
    def test_greeting_does_not_expose_old_route_anchors(self):
        session = SessionMemory()
        session.update_anchor(train="G813", route="秘密旧路线-不要泄露")
        session.set_memory_recall({
            "memory_context_package": {
                "schema_version": 2,
                "hard_anchors": {},
                "soft_context": [{"text": "秘密旧路线-不要泄露"}],
            }
        })
        facts = {
            "queries": [],
            "analysis": [],
            "comparisons": [],
            "meta": {
                "errors": [],
                "warnings": [],
                "chat_messages": ["social greeting"],
                "intent_envelope": {"intent_family": "social_chat", "confidence": 95},
            },
        }
        generator = AnswerGenerator(_FallbackLLM())
        prompt = "\n".join(
            item["content"]
            for item in generator.build_messages("你好！", facts, session=session)
            if item["role"] == "system"
        )
        self.assertNotIn("秘密旧路线", prompt)


class DelayWorkflowTest(unittest.TestCase):
    def make_app(self):
        app = RailwayAgentApp.__new__(RailwayAgentApp)
        app.executor = _RecordingExecutor()
        app.psw = None
        return app

    def test_workflow_executes_path_before_delay(self):
        app = self.make_app()
        context = {"train_numbers": ["G813"], "query_date": "2026-07-15"}
        envelope = build_intent_envelope("train_delay", context, confidence=98)
        plan = [
            {"action": "query", "params": {"domain": "railway", "object": "path_detail", "id": "G813"}},
            {"action": "query", "params": {"domain": "railway", "object": "train_delay", "id": "G813"}},
        ]
        facts = app._execute_plan_with_intent(plan, envelope.to_dict())
        self.assertEqual(app.executor.calls, [["path_detail"], ["train_delay"]])
        self.assertEqual([item["object"] for item in facts["queries"]], ["path_detail", "train_delay"])

    def test_train_overview_executes_path_and_assignment_in_parallel(self):
        app = self.make_app()
        context = {"train_numbers": ["G3"], "query_date": "2026-07-16"}
        envelope = build_intent_envelope("train_overview", context, confidence=98)
        plan = [
            {"action": "query", "params": {"domain": "railway", "object": "path_detail", "id": "G3"}},
            {"action": "query", "params": {"domain": "railway", "object": "train", "id": "G3"}},
        ]

        facts = app._execute_plan_with_intent(plan, envelope.to_dict())

        self.assertEqual(app.executor.calls, [["path_detail", "train"]])
        self.assertEqual([item["object"] for item in facts["queries"]], ["path_detail", "train"])
        self.assertEqual(missing_required_evidence(envelope, facts), [])

    def test_route_train_benchmark_executes_all_evidence_in_parallel(self):
        app = self.make_app()
        context = {
            "train_numbers": ["G3089"],
            "explicit_route": "南京南-福州",
            "dep": "南京南",
            "arr": "福州",
            "query_date": "2026-07-16",
        }
        envelope = build_intent_envelope("route_train_benchmark", context, confidence=98)
        plan = [
            {"action": "query", "params": {"domain": "railway", "object": "s2s_benchmark", "id": "南京南-福州"}},
            {"action": "query", "params": {"domain": "railway", "object": "path_detail", "id": "G3089"}},
            {"action": "query", "params": {"domain": "railway", "object": "train", "id": "G3089"}},
        ]

        facts = app._execute_plan_with_intent(plan, envelope.to_dict())

        self.assertEqual(app.executor.calls, [["s2s_benchmark", "path_detail", "train"]])
        self.assertEqual(missing_required_evidence(envelope, facts), [])

    def test_evidence_gate_replans_delay_once_when_it_was_not_attempted(self):
        app = self.make_app()
        envelope = build_intent_envelope(
            "train_delay",
            {"train_numbers": ["G813"], "query_date": "2026-07-15"},
            confidence=98,
        )
        facts = {
            "queries": [{"object": "path_detail", "id": "G813", "pretty": "scheduled path"}],
            "meta": {},
        }
        extra = app._check_required_evidence(facts, envelope.to_dict(), allow_replan=True)
        self.assertEqual(extra["missing"], [
            {"domain": "railway", "object": "train_delay", "id": "G813"}
        ])

    def test_evidence_gate_does_not_loop_after_empty_delay_attempt(self):
        app = self.make_app()
        envelope = build_intent_envelope(
            "train_delay",
            {"train_numbers": ["G813"], "query_date": "2026-07-15"},
            confidence=98,
        )
        facts = {
            "queries": [{"type": "query_empty", "object": "train_delay", "id": "G813"}],
            "meta": {},
        }
        extra = app._check_required_evidence(facts, envelope.to_dict(), allow_replan=True)
        self.assertIsNone(extra)
        self.assertEqual(facts["meta"]["missing_required_evidence"], ["train_delay"])

    def test_delay_scope_is_applied_only_to_presentation_evidence(self):
        app = self.make_app()
        envelope = build_intent_envelope(
            "train_delay",
            {
                "train_numbers": ["G813"],
                "explicit_route": "徐州东-福州",
                "dep": "徐州东",
                "arr": "福州",
            },
            confidence=98,
        )
        facts = {
            "queries": [{
                "object": "train_delay",
                "id": "G813",
                "evidence": [
                    {"stationName": "北京南"},
                    {"stationName": "徐州东"},
                    {"stationName": "合肥南"},
                    {"stationName": "福州"},
                    {"stationName": "厦门北"},
                ],
            }],
            "meta": {"warnings": []},
        }
        app._scope_live_delay_evidence(facts, envelope.to_dict())
        item = facts["queries"][0]
        self.assertEqual(
            [row["stationName"] for row in item["evidence"]],
            ["徐州东", "合肥南", "福州"],
        )
        self.assertEqual(item["full_evidence_station_count"], 5)


if __name__ == "__main__":
    unittest.main()
