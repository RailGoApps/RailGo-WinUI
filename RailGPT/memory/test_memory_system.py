import tempfile
import unittest

from memory.entity_parser import extract_entities_from_text
from memory.episodic import MemoryManager
from memory.session import SessionMemory


class MemorySystemTest(unittest.TestCase):
    def make_manager(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        return MemoryManager(root_dir=temp_dir.name)

    def test_memory_manager_recalls_same_conversation_train_anchor(self):
        manager = self.make_manager()
        session = SessionMemory()
        session.set_session_id(7)
        session.update_anchor(train="G257", route="上海虹桥-厦门北", date="2026-03-24", query_type="path_detail")

        manager.record_turn(
            session=session,
            user_text="G257 这趟车沿着什么高铁线路运行？",
            ai_text="G257 沿京沪高铁和福厦高铁运行。",
            tasks=[
                {
                    "action": "query",
                    "params": {
                        "domain": "railway",
                        "object": "path_detail",
                        "id": "G257",
                        "date": "2026-03-24",
                    },
                }
            ],
            facts={
                "queries": [
                    {
                        "domain": "railway",
                        "object": "path_detail",
                        "id": "G257",
                        "date": "2026-03-24",
                    }
                ]
            },
        )

        followup_session = SessionMemory()
        followup_session.set_session_id(7)
        bundle = manager.build_recall_bundle(followup_session, "这趟列车沿着什么高铁线路运行呢")

        self.assertTrue(bundle["episodic"])
        self.assertEqual(bundle["anchor_candidates"].get("train"), "G257")

    def test_memory_manager_does_not_write_every_turn_to_legacy_long_term_bucket(self):
        manager = self.make_manager()

        for session_id in (1, 2):
            session = SessionMemory()
            session.set_session_id(session_id)
            session.update_anchor(route="南京南-徐州东", date="2026-03-24", query_type="s2s_benchmark")
            manager.record_turn(
                session=session,
                user_text="南京南到徐州东最快标杆车",
                ai_text="给出了标杆车排序。",
                tasks=[
                    {
                        "action": "query",
                        "params": {
                            "domain": "railway",
                            "object": "s2s_benchmark",
                            "id": "南京南-徐州东",
                            "date": "2026-03-24",
                        },
                    }
                ],
                facts={
                    "queries": [
                        {
                            "domain": "railway",
                            "object": "s2s_benchmark",
                            "id": "南京南-徐州东",
                            "date": "2026-03-24",
                        }
                    ]
                },
            )

        fresh_session = SessionMemory()
        fresh_session.set_session_id(9)
        bundle = manager.build_recall_bundle(fresh_session, "这条线还有标杆车吗")

        self.assertEqual(bundle["long_term"], [])
        self.assertNotIn("route", bundle["anchor_candidates"])
        self.assertEqual(bundle["memory_context_package"]["profile_index"], [])

    def test_profile_index_is_soft_only_and_never_becomes_anchor(self):
        manager = self.make_manager()
        for session_id in (1, 2, 3):
            session = SessionMemory()
            session.set_session_id(session_id)
            manager.record_turn(session=session, user_text="我又想看看 G813", ai_text="")

        fresh = SessionMemory()
        fresh.set_session_id(99)
        bundle = manager.build_recall_bundle(fresh, "猜猜我最喜欢的车次")

        profile = bundle["memory_context_package"]["profile_index"]
        self.assertTrue(profile)
        self.assertEqual(profile[0]["value"], "G813")
        self.assertEqual(profile[0]["allowed_usage"], "soft_profile_only")
        self.assertNotIn("train", bundle["anchor_candidates"])

    def test_assistant_text_does_not_override_fact_route_anchor(self):
        session = SessionMemory()
        session.update_from_tasks(
            [
                {
                    "action": "query",
                    "params": {
                        "domain": "railway",
                        "object": "s2s_benchmark",
                        "id": "南京南-徐州东",
                        "date": "2026-03-25",
                    },
                }
            ]
        )
        session.update_from_facts(
            {
                "queries": [
                    {
                        "domain": "railway",
                        "object": "s2s_benchmark",
                        "id": "南京南-徐州东",
                        "date": "2026-03-25",
                    }
                ]
            }
        )

        session.add_ai_message("G1808（上海南 -> 徐州东）是当前识别到的车次。")

        self.assertEqual(session.resolve_anchor("route"), "南京南-徐州东")
        self.assertEqual(session.resolve_anchor("dep"), "南京南")
        self.assertEqual(session.resolve_anchor("arr"), "徐州东")

    def test_export_and_restore_preserve_recent_fact_entity_pool(self):
        session = SessionMemory()
        session.update_from_tasks(
            [
                {
                    "action": "query",
                    "params": {
                        "domain": "railway",
                        "object": "s2s_benchmark",
                        "id": "南京南-徐州东",
                        "date": "2026-03-25",
                    },
                }
            ]
        )
        session.update_from_facts(
            {
                "queries": [
                    {
                        "domain": "railway",
                        "object": "s2s_benchmark",
                        "id": "南京南-徐州东",
                        "date": "2026-03-25",
                        "pretty": "G84 20:15→21:21\nG94 09:56→11:02\nG66 07:18→08:25",
                    }
                ]
            }
        )

        restored = SessionMemory()
        restored.restore_state(session.export_state())

        pool = restored.get_recent_entity_pool()
        self.assertIn("G84", pool.get("trains", []))
        self.assertIn("G94", pool.get("trains", []))
        self.assertIn("G66", pool.get("trains", []))
        self.assertNotIn("G84 20:15-21:21", pool.get("routes", []))

    def test_generic_assistant_clarification_does_not_create_fake_route(self):
        entities = extract_entities_from_text("请再告诉我你想查的关键条件，比如出发站、到达站或车次号，我就可以继续。")

        self.assertEqual(entities.get("routes"), [])
        self.assertEqual(entities.get("stations"), [])

    def test_station_description_keeps_station_without_fabricating_route(self):
        entities = extract_entities_from_text("南京南站是华东很重要的铁路枢纽，车次密度也很高。")

        self.assertEqual(entities.get("routes"), [])
        self.assertIn("南京南", entities.get("stations", []))
        self.assertNotIn("南京", entities.get("stations", []))

    def test_two_station_description_does_not_fabricate_route_without_route_cues(self):
        entities = extract_entities_from_text("北京南和上海虹桥都是特大型高铁车站。")

        self.assertEqual(entities.get("routes"), [])
        self.assertIn("北京南", entities.get("stations", []))
        self.assertIn("上海虹桥", entities.get("stations", []))

    def test_token_only_assistant_prompt_is_not_stored_in_recent_entities(self):
        session = SessionMemory()
        session.add_ai_message("请再告诉我你想查的关键条件，比如出发站、到达站或车次号，我就可以继续。")

        self.assertEqual(session.get_recent_entity_pool(), {})

    def test_current_user_explicit_date_overrides_restored_memory_state_date(self):
        session = SessionMemory()
        session.restore_state(
            {
                "anchors": {"route": "福州-南京南", "date": "2026-04-27"},
                "anchor_priority": {"route": 95, "date": 95},
            }
        )

        session.add_user_message("福州到南京南2026-05-05还有哪些车有余票？")

        self.assertEqual(session.resolve_anchor("date"), "2026-05-05")
        self.assertEqual(session.resolve_anchor("route"), "福州-南京南")

    def test_build_llm_history_uses_mode_budget_and_excludes_current_user(self):
        session = SessionMemory()
        for idx in range(14):
            session.add_user_message(f"用户问题{idx}")
            session.add_ai_message(f"助手回答{idx}")
        session.add_user_message("当前问题")

        fast_history = session.build_llm_history(mode="fast-go", latest_user_text="当前问题")
        plus_history = session.build_llm_history(mode="fast-plus", latest_user_text="当前问题")

        self.assertLessEqual(len(fast_history), 8)
        self.assertGreater(len(plus_history), len(fast_history))
        self.assertNotEqual(fast_history[-1]["content"], "当前问题")

    def test_agent_context_package_contains_dialogue_and_memory_contract(self):
        session = SessionMemory()
        session.add_user_message("北京南到天津有哪些班次？")
        session.add_ai_message("北京南到天津城际列车很密集。")

        package = session.build_agent_context_package(mode="fast-plus", user_text="所以为啥会有这么多车")

        self.assertIn("dialogue_history", package)
        self.assertIn("memory_context_package", package)
        self.assertEqual(package["latest_user_text"], "所以为啥会有这么多车")
        self.assertTrue(package["dialogue_history"])

    def test_profile_memory_is_visible_only_to_router_and_profile_answer_path(self):
        session = SessionMemory()
        session.update_anchor(train="G20", date="2026-07-15")
        session.set_memory_recall(
            {
                "memory_context_package": {
                    "schema_version": 2,
                    "hard_anchors": {"train": "G20", "date": "2026-07-15"},
                    "profile_index": [{"id": "train:g813", "value": "G813"}],
                    "soft_context": [
                        {"source": "legacy_profile_hint", "summary_l0": "legacy route"},
                        {"source": "working_memory", "summary_l0": "active turn"},
                    ],
                }
            }
        )

        router_view = session.build_agent_context_view("router", user_text="你记得我吗")
        resolver_view = session.build_agent_context_view("context_resolver", user_text="这趟呢")
        date_view = session.build_agent_context_view("date", user_text="那天呢", include_dialogue=True)

        self.assertEqual(router_view["memory_context_package"]["profile_index"][0]["value"], "G813")
        self.assertNotIn("profile_index", resolver_view["memory_context_package"])
        self.assertEqual(
            resolver_view["memory_context_package"]["soft_context"][0]["source"],
            "working_memory",
        )
        self.assertEqual(date_view["memory_context_package"]["hard_anchors"], {"date": "2026-07-15"})
        self.assertEqual(date_view["working_anchors"], {"date": "2026-07-15"})

    def test_assistant_statement_is_soft_memory_and_does_not_create_hard_anchor(self):
        session = SessionMemory()

        session.add_ai_message("G20 途经徐州东-济南西区间时可能会有耳压不适。")

        self.assertIsNone(session.resolve_anchor("route"))
        self.assertIn("徐州东-济南西", session.get_recent_entity_pool().get("routes", []))

    def test_memory_manager_promotes_same_conversation_context_but_not_long_term_profile(self):
        manager = self.make_manager()
        session = SessionMemory()
        session.set_session_id(77)
        session.update_anchor(train="G257", query_type="path_detail")
        manager.record_turn(
            session=session,
            user_text="G257 这趟车走什么线路？",
            ai_text="G257 沿京沪高铁运行。",
            tasks=[{"action": "query", "params": {"domain": "railway", "object": "path_detail", "id": "G257"}}],
            facts={"queries": [{"domain": "railway", "object": "path_detail", "id": "G257"}]},
        )

        followup = SessionMemory()
        followup.set_session_id(77)
        bundle = manager.build_recall_bundle(followup, "这趟列车具体路线是什么？")

        self.assertEqual(bundle["anchor_candidates"].get("train"), "G257")
        self.assertTrue(bundle["memory_context_package"]["soft_context"])


if __name__ == "__main__":
    unittest.main()
