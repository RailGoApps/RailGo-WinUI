import json
import unittest
from unittest.mock import patch

from agent.actions import validate_query_semantics
from agent.capabilities import CAPABILITIES
from agent.executor import Executor
from agent.planner import Planner
from agent.router import Router
from memory.session import SessionMemory


class CouncilLLM:
    def __init__(self, obj, query_id, query_date="", omit_required_object=False):
        self.obj = obj
        self.query_id = query_id
        self.query_date = query_date
        self.omit_required_object = omit_required_object
        self.calls = 0

    def get_mode(self):
        return "fast-go"

    def set_mode(self, _mode):
        pass

    def generate(self, _messages, timeout=None, max_retries=None):
        self.calls += 1
        vote = {
            "agent": "tool_intent_agent",
            "intent": self.obj if self.omit_required_object else "railway_tool_lookup",
            "preferred_action": "query",
            "confidence": 97,
            "reason": "explicit capability match",
            "required_object": "" if self.omit_required_object else self.obj,
            "query_id": self.query_id,
            "query_date": self.query_date,
        }
        return json.dumps({"votes": [vote], "consensus": vote, "conflict": False}, ensure_ascii=False)


class RailGoV2RouterIntegrationTest(unittest.TestCase):
    def route_with(self, text, obj, query_id, query_date="", expect_llm=True):
        memory = SessionMemory()
        router = Router(memory)
        router.llm = CouncilLLM(obj, query_id, query_date)
        tasks = router.route(text, memory)
        if expect_llm:
            self.assertGreaterEqual(router.llm.calls, 1)
        self.assertEqual(tasks[0]["action"], "query")
        return tasks[0]["params"]

    def test_live_delay_uses_train_delay(self):
        memory = SessionMemory()
        router = Router(memory)
        router.llm = CouncilLLM("train_delay", "G1")

        tasks = router.route("G1现在正点吗？", memory)

        self.assertEqual(
            [task["params"]["object"] for task in tasks],
            ["path_detail", "train_delay"],
        )
        envelope = router.get_last_intent_envelope()
        self.assertEqual(envelope["selected_capability"], "train_delay")
        self.assertEqual(envelope["required_evidence"], ["train_delay"])

    def test_station_access_uses_exact_station_contract(self):
        params = self.route_with(
            "G1在2026-07-15北京南哪个检票口、几站台？",
            "train_station_access",
            "G1|北京南|departure",
            "2026-07-15",
        )
        self.assertEqual(params["object"], "train_station_access")
        self.assertEqual(params["date"], "2026-07-15")

    def test_station_board_uses_board_not_station_metadata(self):
        params = self.route_with("北京南现在大屏上有哪些车？", "station_board", "北京南|departure")
        self.assertEqual(params["object"], "station_board")

    def test_station_board_defaults_to_departure_when_user_omits_direction(self):
        params = self.route_with("看看北京南站的大屏", "station_board", "北京南")
        self.assertEqual(params["id"], "北京南|departure")

    def test_explicit_arrival_board_overrides_departure_default(self):
        params = self.route_with("看看北京南站的到达大屏", "station_board", "北京南|arrival")
        self.assertEqual(params["id"], "北京南|arrival")

    def test_station_access_defaults_to_today_and_departure(self):
        params = self.route_with(
            "G1在北京南哪个检票口？",
            "train_station_access",
            "G1|北京南",
        )
        self.assertEqual(params["id"], "G1|北京南|departure")
        self.assertTrue(params.get("date"))

    def test_explicit_station_exit_request_uses_arrival_direction(self):
        params = self.route_with(
            "G1今天到北京南后从哪个出站口走？",
            "train_station_access",
            "G1|北京南|arrival",
        )
        self.assertEqual(params["id"], "G1|北京南|arrival")

    def test_station_access_only_asks_for_missing_station(self):
        memory = SessionMemory()
        router = Router(memory)
        router.llm = CouncilLLM("train_station_access", "G1")

        tasks = router.route("G1的检票口在哪里？", memory)

        self.assertEqual(tasks[0]["action"], "pending")
        self.assertEqual(tasks[0]["params"]["slot"], ["station"])
        contract = tasks[0]["params"]["context"]["missing_slot_contract"]
        self.assertEqual(contract["capability"], "train_station_access")
        self.assertEqual(contract["missing_slots"], ["station"])

    def test_station_access_followup_reuses_only_confirmed_contract_slots(self):
        memory = SessionMemory()
        memory.enter_followup(
            question="请告诉我要查询的具体车站。",
            slot=["station"],
            context={
                "query_object": "train_station_access",
                "train": "G1",
                "date": "2026-07-16",
                "direction": "departure",
            },
        )
        latest = "北京南"
        memory.add_user_message(latest)
        router = Router(memory)
        router.llm = CouncilLLM("train_station_access", "")

        tasks = router.route(latest, memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "train_station_access")
        self.assertEqual(tasks[0]["params"]["id"], "G1|北京南|departure")
        self.assertEqual(tasks[0]["params"]["date"], "2026-07-16")

    def test_left_ticket_without_user_date_enters_date_followup(self):
        memory = SessionMemory()
        router = Router(memory)
        router.llm = CouncilLLM("left_ticket_s2s", "南京南-上海")

        tasks = router.route("南京南到上海还有余票吗？", memory)

        self.assertEqual(tasks[0]["action"], "pending")
        self.assertEqual(tasks[0]["params"]["slot"], ["date"])
        contract = tasks[0]["params"]["context"]["missing_slot_contract"]
        self.assertEqual(contract["capability"], "left_ticket_s2s")

    def test_capability_in_intent_survives_missing_required_object(self):
        memory = SessionMemory()
        router = Router(memory)
        router.llm = CouncilLLM(
            "station_board",
            "句容西|departure",
            omit_required_object=True,
        )

        tasks = router.route("帮我看看句容西的车站大屏", memory)

        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "station_board")
        self.assertEqual(tasks[0]["params"]["id"], "句容西|departure")

    def test_station_board_followup_replaces_station_through_semantic_council(self):
        memory = SessionMemory()
        memory.add_user_message("帮我看看句容西站的大屏")
        memory.update_from_tasks([
            {"action": "query", "params": {"domain": "railway", "object": "station_board", "id": "句容西|departure"}}
        ])
        memory.update_from_facts({
            "queries": [
                {
                    "domain": "railway",
                    "object": "station_board",
                    "id": "JWH|departure",
                    "evidence": [],
                    "pretty": "LIVE STATION BOARD",
                }
            ]
        })
        memory.add_ai_message("句容西站当前没有发车记录。")
        latest = "ok，那看看南京南的吧"
        memory.add_user_message(latest)

        router = Router(memory)
        router.llm = CouncilLLM("station_board", "")
        tasks = router.route(latest, memory)

        self.assertGreaterEqual(router.llm.calls, 1)
        self.assertEqual(tasks[0]["action"], "query")
        self.assertEqual(tasks[0]["params"]["object"], "station_board")
        self.assertEqual(tasks[0]["params"]["id"], "南京南|departure")

    def test_live_delay_followup_replaces_train_through_semantic_council(self):
        memory = SessionMemory()
        memory.add_user_message("G813现在晚点吗？")
        memory.update_from_tasks([
            {"action": "query", "params": {"domain": "railway", "object": "train_delay", "id": "G813"}}
        ])
        memory.update_from_facts({
            "queries": [
                {
                    "domain": "railway",
                    "object": "train_delay",
                    "id": "G813",
                    "evidence": [{"delayStatus": "正点"}],
                    "pretty": "LIVE TRAIN DELAY",
                }
            ]
        })
        memory.add_ai_message("G813当前正点运行。")
        latest = "那G20呢？"
        memory.add_user_message(latest)

        router = Router(memory)
        router.llm = CouncilLLM("train_delay", "")
        tasks = router.route(latest, memory)

        self.assertGreaterEqual(router.llm.calls, 1)
        self.assertEqual(
            [task["params"]["object"] for task in tasks],
            ["path_detail", "train_delay"],
        )
        self.assertTrue(all(task["params"]["id"] == "G20" for task in tasks))

    def test_official_railgo_service_matrix_is_mapped_without_duplicates(self):
        expected = {
            "path_detail": {"railgo_v1_train", "railgo_v2_train_main"},
            "station_to_station_mini": {"railgo_v1_s2s"},
            "station_preselect": {"railgo_v1_station_preselect"},
            "station": {"railgo_v1_station"},
            "train_preselect": {"railgo_v1_train_preselect"},
            "random_train": {"railgo_v1_random_train"},
            "train_station_access": {"railgo_v2_access"},
            "train_delay": {"railgo_v2_delay"},
            "station_board": {"railgo_v2_station_board"},
            "coach_layout": {"railgo_v2_coach"},
            "train_route_map": {"railgo_v2_map"},
        }

        for object_name, providers in expected.items():
            self.assertTrue(providers.issubset(set(CAPABILITIES[object_name].providers)))

    def test_explicit_single_station_metadata_opens_station_tool(self):
        params = self.route_with("北京南属于哪个铁路局？", "station", "北京南")
        self.assertEqual(params["object"], "station")

    def test_coach_layout_is_temporarily_unavailable_without_affecting_assignment(self):
        assignment = self.route_with("G1今天具体用什么车底？", "train", "G1", expect_llm=False)
        self.assertEqual(CAPABILITIES["coach_layout"].availability, "disabled")
        self.assertFalse(
            validate_query_semantics(
                {"domain": "railway", "object": "coach_layout", "id": "G1"}
            )
        )
        self.assertEqual(assignment["object"], "train")

    def test_route_map_is_temporarily_unavailable_without_affecting_timetable(self):
        timetable = self.route_with("G1经过哪些站？", "path_detail", "G1", expect_llm=False)
        self.assertEqual(CAPABILITIES["train_route_map"].availability, "disabled")
        self.assertFalse(
            validate_query_semantics(
                {"domain": "railway", "object": "train_route_map", "id": "G1"}
            )
        )
        self.assertEqual(timetable["object"], "path_detail")


class RailGoV2PlannerExecutorTest(unittest.TestCase):
    def test_actions_and_planner_accept_new_contracts(self):
        planner = Planner()
        self.assertTrue(
            validate_query_semantics(
                {"domain": "railway", "object": "station_board", "id": "北京南|departure"}
            )
        )
        self.assertEqual(
            planner.normalize_query_id("train_station_access", "g1｜北京南｜departure"),
            "G1|北京南|departure",
        )
        self.assertEqual(planner.normalize_query_id("train_delay", "g1次"), "G1")

    @patch("agent.executor.query_railgo_v2_tool")
    def test_executor_dispatches_v2_tool_without_persisting(self, query_tool):
        query_tool.return_value = {
            "domain": "railway",
            "object": "train_delay",
            "id": "G1",
            "evidence": [{"delayStatus": "正点"}],
            "pretty": "LIVE TRAIN DELAY: G1",
        }
        executor = Executor(psw=None, max_workers=1)

        result = executor._handle_query(
            {"domain": "railway", "object": "train_delay", "id": "G1"}
        )

        self.assertEqual(result["object"], "train_delay")
        self.assertTrue(result.get("fast_views"))
        query_tool.assert_called_once_with("train_delay", "G1", date=None, psw=None)

    @patch("agent.executor.query_railgo_catalog_tool")
    def test_executor_dispatches_v1_catalog_tool(self, query_tool):
        query_tool.return_value = {
            "domain": "railway",
            "object": "train_preselect",
            "id": "G1",
            "evidence": ["G1", "G10"],
            "source": {"provider": "RailGo", "api_version": "v1"},
            "pretty": "TRAIN PRESELECT: G1",
        }
        executor = Executor(psw=None, max_workers=1)

        result = executor._handle_query(
            {"domain": "railway", "object": "train_preselect", "id": "G1"}
        )

        self.assertEqual(result["object"], "train_preselect")
        self.assertTrue(result.get("fast_views"))
        query_tool.assert_called_once_with("train_preselect", "G1", psw=None)


if __name__ == "__main__":
    unittest.main()
