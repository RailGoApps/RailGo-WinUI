import unittest

from agent.answer_generator import AnswerGenerator
from memory.session import SessionMemory


class DummyLLM:
    def __init__(self, mode="deep"):
        self.mode = mode
        self.generate_calls = 0
        self.stream_calls = 0

    def set_mode(self, mode):
        self.mode = mode

    def get_mode(self):
        return self.mode

    def generate(self, messages):
        self.generate_calls += 1
        return "{}"

    def stream_generate(self, messages):
        self.stream_calls += 1
        return iter(())


class CountingFastAnswerGenerator(AnswerGenerator):
    def __init__(self):
        super().__init__(DummyLLM(mode="fast"))
        self.context_bundle_calls = 0

    def build_context_bundle(self, user_text, facts):
        self.context_bundle_calls += 1
        return {
            "format": "fast_fact_context_v1",
            "candidates": [],
            "observations": [],
            "retained_evidence": [],
            "source_stats": {"cache_hit": False},
        }


class AnswerGeneratorPromptTest(unittest.TestCase):
    def test_late_extra_request_filters_temporarily_disabled_capabilities(self):
        answer_generator = AnswerGenerator(DummyLLM(mode="fast"))

        extra_request = answer_generator._validated_extra_request({
            "missing": [
                {"domain": "railway", "object": "coach_layout", "id": "G1"},
                {"domain": "railway", "object": "train_route_map", "id": "G1"},
                {"domain": "railway", "object": "path_detail", "id": "G1"},
            ]
        })

        self.assertEqual(extra_request, {
            "missing": [
                {"domain": "railway", "object": "path_detail", "id": "G1"},
            ]
        })

    def test_deep_prompt_strips_fast_only_fields(self):
        answer_generator = AnswerGenerator(DummyLLM(mode="deep"))
        facts = {
            "queries": [
                {
                    "domain": "railway",
                    "object": "s2s_benchmark",
                    "id": "南京南-徐州东",
                    "pretty": "G87, G89",
                    "fast_views": [
                        {
                            "view_id": "overview",
                            "view_type": "s2s_overview",
                            "priority": 120,
                            "text": "G87 is top candidate",
                        }
                    ],
                    "fast_candidates": [
                        {
                            "candidate_key": "train:G87",
                            "candidate_type": "train",
                            "label": "G87",
                            "score": 95,
                            "why": "seed candidate",
                            "supporting_points": ["duration=2h39m"],
                            "attributes": {"train_no": "G87"},
                        }
                    ]
                }
            ],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }

        messages = answer_generator.build_messages("帮我找最快的车", facts)
        prompt_text = "\n".join(message["content"] for message in messages if message["role"] == "system")

        self.assertIn("G87, G89", prompt_text)
        self.assertNotIn("fast_views", prompt_text)
        self.assertNotIn("fast_candidates", prompt_text)
        self.assertNotIn("s2s_overview", prompt_text)

    def test_media_urls_coordinates_and_attachments_never_enter_prompt(self):
        answer_generator = AnswerGenerator(DummyLLM(mode="deep"))
        facts = {
            "queries": [{
                "object": "coach_layout", "id": "G1",
                "evidence": {"carCode": "CR400BF-A-5059", "pictureUrl": "https://secret/image.png"},
                "_media_catalog": [{"kind": "coach", "selector": "08"}],
                "artifacts": [{"type": "coach_image", "asset_id": "a" * 64, "local_path": "C:/secret.png"}],
                "geojson": {"coordinates": [[116.3, 39.8]]},
            }],
            "analysis": [], "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": [], "attachments": [{"asset_id": "a" * 64}]},
        }
        messages = answer_generator.build_messages("G1定员", facts)
        prompt = "\n".join(item["content"] for item in messages)
        self.assertIn("CR400BF-A-5059", prompt)
        self.assertNotIn("https://secret", prompt)
        self.assertNotIn("C:/secret.png", prompt)
        self.assertNotIn("coordinates", prompt)
        self.assertNotIn("_media_catalog", prompt)

    def test_fast_prompt_uses_compact_fast_instructions(self):
        answer_generator = AnswerGenerator(DummyLLM(mode="fast"))
        context_bundle = {
            "format": "fast_fact_context_v1",
            "candidates": [
                {"candidate_key": "train:G87", "label": "G87", "score": 95}
            ],
            "observations": [],
            "retained_evidence": [],
            "source_stats": {"cache_hit": False},
        }
        facts = {
            "queries": [],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }

        messages = answer_generator.build_messages(
            "帮我找最快的车",
            facts,
            context_bundle=context_bundle,
        )
        prompt_text = "\n".join(message["content"] for message in messages if message["role"] == "system")

        self.assertIn("RailGPT Fast Answer Writer", prompt_text)
        self.assertNotIn("Smart EMU Knowledge Patch", prompt_text)
        self.assertIn("Return polished Markdown", prompt_text)
        self.assertIn("prefer a compact Markdown table", prompt_text)

    def test_fast_prompt_includes_markdown_structure_guidance(self):
        answer_generator = AnswerGenerator(DummyLLM(mode="fast"))
        context_bundle = {
            "format": "fast_fact_context_v1",
            "candidates": [{"candidate_key": "train:G84", "label": "G84", "score": 99}],
            "observations": [],
            "retained_evidence": [],
            "source_stats": {"cache_hit": False},
        }
        facts = {
            "queries": [],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }

        messages = answer_generator.build_messages(
            "rank the best trains",
            facts,
            context_bundle=context_bundle,
        )
        prompt_text = "\n".join(message["content"] for message in messages if message["role"] == "system")

        self.assertIn("Use polished Markdown", prompt_text)
        self.assertIn("at least two comparable records", prompt_text)
        self.assertIn("1-3 meaningful, context-appropriate emoji", prompt_text)

    def test_final_presentation_contract_uses_tables_and_restrained_emoji(self):
        answer_generator = AnswerGenerator(DummyLLM(mode="fast"))
        facts = {
            "queries": [],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }

        messages = answer_generator.build_messages("对比G1和G3", facts)
        prompt_text = "\n".join(message["content"] for message in messages if message["role"] == "system")

        self.assertIn("train lists, rankings, timetables", prompt_text)
        self.assertIn("Never create a value merely to fill a table cell", prompt_text)
        self.assertIn("Do not use a table for a single-value lookup", prompt_text)
        self.assertIn("Do not place emoji in every row", prompt_text)

    def test_route_train_benchmark_prompt_defers_to_tool_rating(self):
        answer_generator = AnswerGenerator(DummyLLM(mode="fast"))
        facts = {
            "queries": [],
            "analysis": [],
            "comparisons": [],
            "meta": {
                "errors": [],
                "warnings": [],
                "chat_messages": [],
                "intent_envelope": {
                    "intent_family": "route_train_benchmark",
                    "selected_capability": "route_train_benchmark",
                    "required_evidence": ["s2s_benchmark", "path_detail", "train"],
                    "workflow": ["s2s_benchmark", "path_detail", "train"],
                    "execution_strategy": "parallel",
                },
            },
        }

        messages = answer_generator.build_messages("G3089是不是南京南到福州的标杆车？", facts)
        prompt_text = "\n".join(message["content"] for message in messages if message["role"] == "system")

        self.assertIn("sole authority", prompt_text)
        self.assertIn("intermediate OD segment", prompt_text)
        self.assertIn("Never redefine benchmark as zero stops", prompt_text)

    def test_chat_presentation_contract_does_not_force_tables_or_emoji(self):
        answer_generator = AnswerGenerator(DummyLLM(mode="fast"))
        facts = {
            "queries": [],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": ["Dedicated chat route"]},
        }

        messages = answer_generator.build_messages("原来如此！", facts)
        prompt_text = "\n".join(message["content"] for message in messages if message["role"] == "system")

        self.assertIn("at most one natural, context-appropriate emoji", prompt_text)
        self.assertIn("Do not force a table into casual chat", prompt_text)

    def test_route_candidate_whitelist_prevents_unseen_train_recommendations(self):
        answer_generator = AnswerGenerator(DummyLLM(mode="fast"))
        facts = {
            "queries": [
                {
                    "domain": "railway",
                    "object": "s2s_benchmark",
                    "id": "南京南-福州",
                    "date": "2026-07-15",
                    "fast_candidates": [
                        {
                            "candidate_key": "train:G3089",
                            "candidate_type": "train",
                            "label": "G3089",
                            "attributes": {"train_no": "G3089"},
                        }
                    ],
                }
            ],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }

        messages = answer_generator.build_messages("南京南到福州有什么标杆车？", facts)
        prompt_text = "\n".join(message["content"] for message in messages if message["role"] == "system")

        self.assertIn("Route candidate whitelist", prompt_text)
        self.assertIn("G3089", prompt_text)
        self.assertIn("must appear in this whitelist", prompt_text)

    def test_build_messages_marks_dedicated_chat_turn(self):
        answer_generator = AnswerGenerator(DummyLLM(mode="fast"))
        facts = {
            "queries": [],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": ["Dedicated chat route"]},
        }

        messages = answer_generator.build_messages("你是谁？", facts)
        prompt_text = "\n".join(message["content"] for message in messages if message["role"] == "system")

        self.assertIn("Chat continuation turn detected", prompt_text)
        self.assertIn("Do not continue any previous pending clarification", prompt_text)
        self.assertNotIn("Dedicated chat route", prompt_text)

    def test_build_messages_includes_safe_chat_hints_and_warm_chat_style(self):
        answer_generator = AnswerGenerator(DummyLLM(mode="fast"))
        facts = {
            "queries": [],
            "analysis": [],
            "comparisons": [],
            "meta": {
                "errors": [],
                "warnings": [],
                "chat_messages": ["Dedicated contextual social chat route: acknowledge surprise first."],
            },
        }

        messages = answer_generator.build_messages("这么厉害的吗？", facts)
        prompt_text = "\n".join(message["content"] for message in messages if message["role"] == "system")

        self.assertIn("Chat route hints", prompt_text)
        self.assertIn("Acknowledge the user's reaction first", prompt_text)
        self.assertIn("Reply like a warm, knowledgeable railway companion", prompt_text)
        self.assertIn("acknowledge that reaction first", prompt_text)
        self.assertNotIn("Dedicated contextual social chat route", prompt_text)

    def test_build_messages_includes_railway_knowledge_guardrails(self):
        answer_generator = AnswerGenerator(DummyLLM(mode="fast"))
        facts = {
            "queries": [],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }

        messages = answer_generator.build_messages("什么是CTCS-3？", facts)
        prompt_text = "\n".join(message["content"] for message in messages if message["role"] == "system")

        self.assertIn("Railway knowledge answering rules", prompt_text)
        self.assertIn("stable general railway knowledge", prompt_text)
        self.assertIn("real-time assignment", prompt_text)
        self.assertIn("current-turn facts", prompt_text)
        self.assertIn("Tool provenance is displayed by the application UI", prompt_text)
        self.assertNotIn("https://railgo.dev", prompt_text)

    def test_provider_provenance_is_removed_from_fast_and_deep_prompts(self):
        source = {
            "provider": "RailGo",
            "api_version": "v2",
            "endpoint": "/api/v2/getTrainDelayAll",
            "url": "https://railgo.dev",
        }
        facts = {
            "queries": [{
                "object": "train_delay",
                "id": "G1",
                "source": source,
                "pretty": "LIVE DELAY\nSOURCE: RailGo v2 endpoint=x url=https://railgo.dev\n正点",
                "evidence": [{"stationName": "南京南", "delayStatus": "正点"}],
                "freshness": {"fetched_at": "2026-07-16T10:00:00+08:00", "age_seconds": 30},
            }],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }

        deep = AnswerGenerator(DummyLLM(mode="deep"))
        deep_prompt = "\n".join(item["content"] for item in deep.build_messages("G1晚点吗", facts))
        self.assertNotIn("https://railgo.dev", deep_prompt)
        self.assertNotIn("getTrainDelayAll", deep_prompt)
        self.assertNotIn("SOURCE: RailGo", deep_prompt)
        self.assertIn("2026-07-16T10:00:00+08:00", deep_prompt)

        fast = AnswerGenerator(DummyLLM(mode="fast"))
        context_bundle = {
            "format": "fast_fact_context_v1",
            "candidates": [],
            "observations": [{
                "content": "SOURCE: RailGo v2 endpoint=x url=https://railgo.dev\nOBSERVED_AT: 2026-07-16T10:00:00+08:00",
            }],
            "retained_evidence": [{"source": source, "content": "G1 正点"}],
            "source_stats": {"provider": "RailGo", "cache_hit": True},
        }
        fast_prompt = "\n".join(
            item["content"]
            for item in fast.build_messages("G1晚点吗", facts, context_bundle=context_bundle)
        )
        self.assertNotIn("https://railgo.dev", fast_prompt)
        self.assertNotIn("getTrainDelayAll", fast_prompt)
        self.assertNotIn("SOURCE: RailGo", fast_prompt)
        self.assertIn("2026-07-16T10:00:00+08:00", fast_prompt)
    
    def test_build_messages_uses_precomputed_rag_context_and_presentation_plan(self):
        answer_generator = AnswerGenerator(DummyLLM(mode="fast"))
        context_bundle = {
            "format": "fast_fact_context_v1",
            "candidates": [],
            "observations": [],
            "retained_evidence": [],
            "source_stats": {"cache_hit": False},
        }
        facts = {
            "queries": [],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }

        messages = answer_generator.build_messages(
            "南京南电报码",
            facts,
            context_bundle=context_bundle,
            rag_context="Knowledge-Augmented Railway Context\n[RAG-1] 南京南",
            presentation_plan={
                "answer_shape": "lookup_card",
                "sections": ["answer", "details"],
                "highlight_fields": ["station_name", "telecode"],
            },
        )
        prompt_text = "\n".join(message["content"] for message in messages if message["role"] == "system")

        self.assertIn("Knowledge-Augmented Railway Context", prompt_text)
        self.assertIn("Fast presentation plan:", prompt_text)
        self.assertIn("answer_shape: lookup_card", prompt_text)

    def test_should_use_fast_direct_final_for_simple_high_confidence_case(self):
        answer_generator = AnswerGenerator(DummyLLM(mode="fast"))
        context_bundle = {
            "format": "fast_fact_context_v1",
            "candidates": [],
            "observations": [],
            "retained_evidence": [],
            "source_stats": {
                "cache_hit": False,
                "merged_candidate_count": 0,
                "retained_evidence_count": 1,
                "raw_fallback_count": 0,
            },
        }
        facts = {
            "queries": [
                {
                    "domain": "railway",
                    "object": "telecode",
                    "id": "南京南",
                }
            ],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }

        self.assertTrue(
            answer_generator.should_use_fast_direct_final(
                user_text="南京南电报码是什么",
                facts=facts,
                context_bundle=context_bundle,
            )
        )

    def test_should_not_use_fast_direct_final_for_api_heavy_benchmark_query(self):
        answer_generator = AnswerGenerator(DummyLLM(mode="fast"))
        context_bundle = {
            "format": "fast_fact_context_v1",
            "candidates": [{"candidate_key": "train:G84", "label": "G84", "score": 99}],
            "observations": [],
            "retained_evidence": [],
            "source_stats": {
                "cache_hit": False,
                "merged_candidate_count": 1,
                "retained_evidence_count": 1,
                "raw_fallback_count": 0,
            },
        }
        facts = {
            "queries": [
                {
                    "domain": "railway",
                    "object": "s2s_benchmark",
                    "id": "NKH-UUH",
                }
            ],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }

        self.assertFalse(
            answer_generator.should_use_fast_direct_final(
                user_text="南京南到徐州东最快的标杆车",
                facts=facts,
                context_bundle=context_bundle,
            )
        )

    def test_should_not_use_fast_direct_final_when_raw_fallback_exists(self):
        answer_generator = AnswerGenerator(DummyLLM(mode="fast"))
        context_bundle = {
            "format": "fast_fact_context_v1",
            "candidates": [],
            "observations": [],
            "retained_evidence": [],
            "source_stats": {
                "cache_hit": False,
                "merged_candidate_count": 0,
                "retained_evidence_count": 1,
                "raw_fallback_count": 1,
            },
        }
        facts = {
            "queries": [
                {
                    "domain": "railway",
                    "object": "telecode",
                    "id": "南京南",
                }
            ],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }

        self.assertFalse(
            answer_generator.should_use_fast_direct_final(
                user_text="南京南电报码是什么",
                facts=facts,
                context_bundle=context_bundle,
            )
        )

    def test_build_messages_includes_calendar_grounding_for_query_dates(self):
        answer_generator = AnswerGenerator(DummyLLM(mode="fast"))
        facts = {
            "queries": [
                {
                    "domain": "railway",
                    "object": "left_ticket_s2s",
                    "id": "南京南-徐州东",
                    "date": "2026-03-29",
                }
            ],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }

        messages = answer_generator.build_messages("这周天南京南到徐州东还有余票吗", facts)
        prompt_text = "\n".join(message["content"] for message in messages if message["role"] == "system")

        self.assertIn("Calendar grounding:", prompt_text)
        self.assertIn("2026-03-29 = 周日", prompt_text)
        self.assertIn("use these exact mappings", prompt_text)

    def test_build_messages_includes_memory_context(self):
        answer_generator = AnswerGenerator(DummyLLM(mode="fast"))
        session = SessionMemory()
        session.update_anchor(train="G257", route="上海虹桥-厦门北", date="2026-03-24", query_type="path_detail")
        session.set_memory_recall({
            "session": [{"text": "当前会话锚点: train=G257"}],
            "episodic": [{"text": "G257线路问题 -> 已回答"}],
            "long_term": [],
            "anchor_candidates": {"train": "G257"},
        })
        facts = {
            "queries": [],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }

        messages = answer_generator.build_messages(
            "这趟列车沿着什么高铁线路运行",
            facts,
            session=session,
        )
        prompt_text = "\n".join(message["content"] for message in messages if message["role"] == "system")

        self.assertIn("Role-scoped AgentContextPackage", prompt_text)
        self.assertIn("G257", prompt_text)

    def test_memory_profile_chat_keeps_only_soft_profile_and_uncertainty_contract(self):
        answer_generator = AnswerGenerator(DummyLLM(mode="fast"))
        session = SessionMemory()
        session.update_anchor(train="G20", route="上海虹桥-北京南")
        session.set_memory_recall(
            {
                "memory_context_package": {
                    "schema_version": 2,
                    "hard_anchors": {"train": "G20", "route": "上海虹桥-北京南"},
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
        facts = {
            "queries": [],
            "analysis": [],
            "comparisons": [],
            "meta": {
                "errors": [],
                "warnings": [],
                "chat_messages": [],
                "intent_envelope": {"intent_family": "memory_profile_chat", "confidence": 96},
            },
        }

        messages = answer_generator.build_messages(
            "猜猜我最喜欢的车次",
            facts,
            session=session,
        )
        prompt_text = "\n".join(message["content"] for message in messages if message["role"] == "system")

        self.assertIn("G813", prompt_text)
        self.assertIn("tentative guess", prompt_text)
        self.assertIn("closed evidence set", prompt_text)
        self.assertIn("Never infer an EMU/model from a train number", prompt_text)
        self.assertIn("Every train number, EMU/model, route, station", prompt_text)
        self.assertNotIn("Smart EMU Knowledge Patch", prompt_text)
        self.assertNotIn("Railway knowledge answering rules", prompt_text)
        self.assertNotIn('"route": "上海虹桥-北京南"', prompt_text)

    def test_build_messages_guides_train_ticket_queries_to_reuse_known_route(self):
        answer_generator = AnswerGenerator(DummyLLM(mode="deep"))
        session = SessionMemory()
        session.update_anchor(train="G1", route="上海虹桥-北京南", date="2026-03-24", query_type="path_detail")
        facts = {
            "queries": [
                {
                    "domain": "railway",
                    "object": "path_detail",
                    "id": "G1",
                    "date": "2026-03-24",
                    "pretty": "上海虹桥 06:00 -> 北京南 10:28",
                }
            ],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }

        messages = answer_generator.build_messages(
            "请用12306实时余票数据验证你刚才说的G1次今天商务座已售罄。",
            facts,
            session=session,
        )
        prompt_text = "\n".join(message["content"] for message in messages if message["role"] == "system")

        self.assertIn("不能在已有对应能力时声称无法核验", prompt_text)
        self.assertIn("优先转成 left_ticket_s2s 所需的 DEP-ARR", prompt_text)
        self.assertIn("如果用户想核验某车次的余票，而 memory / facts / path_detail 已经能确定这趟车的发到区间", prompt_text)

    def test_generate_structured_requests_left_ticket_when_path_detail_reveals_route(self):
        llm = DummyLLM(mode="fast")
        answer_generator = AnswerGenerator(llm)
        answer_generator.set_mode_profile("fast-go")
        facts = {
            "queries": [
                {
                    "domain": "railway",
                    "object": "path_detail",
                    "id": "G1",
                    "date": "2026-03-24",
                    "pretty": (
                        "==================================================\n"
                        "🚄 Train Path Profile: G1\n"
                        "--------------------------------------------------\n"
                        "站序 | 到达 | 发车 | 停时 | 里程(km) | 车站\n"
                        "--------------------------------------------------\n"
                        "  1 |    -- | 06:30 |   0分 |       0 | 北京南 (VNP)\n"
                        "  2 | 11:24 |    -- |   0分 |    1318 | 上海虹桥 (AOH)\n"
                    ),
                }
            ],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }

        result = answer_generator.generate_structured(
            "请用12306实时余票数据验证你刚才说的G1次今天商务座已售罄。",
            facts,
        )

        self.assertEqual(result["type"], "need_more_facts")
        self.assertEqual(result["extra_request"]["missing"][0]["object"], "left_ticket_s2s")
        self.assertEqual(result["extra_request"]["missing"][0]["id"], "北京南-上海虹桥")
        self.assertEqual(result["extra_request"]["missing"][0]["date"], "2026-03-24")
        self.assertEqual(llm.generate_calls, 0)

    def test_generate_structured_prefers_full_path_route_over_partial_session_anchor_for_ticket_followup(self):
        llm = DummyLLM(mode="fast")
        answer_generator = AnswerGenerator(llm)
        answer_generator.set_mode_profile("fast-go")
        session = SessionMemory()
        session.update_anchor(train="G1", route="北京南-沧州西", date="2026-03-24", query_type="path_detail")
        facts = {
            "queries": [
                {
                    "domain": "railway",
                    "object": "path_detail",
                    "id": "G1",
                    "date": "2026-03-24",
                    "pretty": (
                        "==================================================\n"
                        "🚄 Train Path Profile: G1\n"
                        "--------------------------------------------------\n"
                        "站序 | 到达 | 发车 | 停时 | 里程(km) | 车站\n"
                        "--------------------------------------------------\n"
                        "  1 |    -- | 06:30 |   0分 |       0 | 北京南 (VNP)\n"
                        "  2 | 07:21 | 07:23 |   2分 |     240 | 沧州西 (COP)\n"
                        "  3 | 11:24 |    -- |   0分 |    1318 | 上海虹桥 (AOH)\n"
                    ),
                }
            ],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }

        result = answer_generator.generate_structured(
            "请用12306实时余票数据验证你刚才说的G1次今天商务座已售罄。",
            facts,
            session=session,
        )

        self.assertEqual(result["type"], "need_more_facts")
        self.assertEqual(result["extra_request"]["missing"][0]["object"], "left_ticket_s2s")
        self.assertEqual(result["extra_request"]["missing"][0]["id"], "北京南-上海虹桥")
        self.assertEqual(result["extra_request"]["missing"][0]["date"], "2026-03-24")
        self.assertEqual(llm.generate_calls, 0)

    def test_generate_structured_fast_finalizes_grounded_multi_train_path_comparison_without_extra_round(self):
        llm = DummyLLM(mode="fast")
        answer_generator = AnswerGenerator(llm)
        answer_generator.set_mode_profile("fast-go")
        facts = {
            "queries": [
                {
                    "domain": "railway",
                    "object": "path_detail",
                    "id": "G73",
                    "date": "2026-03-25",
                    "pretty": "G73 path ready",
                },
                {
                    "domain": "railway",
                    "object": "path_detail",
                    "id": "G71",
                    "date": "2026-03-25",
                    "pretty": "G71 path ready",
                },
            ],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }

        result = answer_generator.generate_structured(
            "G73和G71同样从北京到贵阳，为什么G73更快？它们停站有什么不同？",
            facts,
        )

        self.assertEqual(result["type"], "final")
        self.assertIn("路径和停站信息", result["content"])
        self.assertEqual(llm.generate_calls, 0)

    def test_fast_plus_uses_pro_thinking_mode_for_pipeline_and_final(self):
        pipeline_llm = DummyLLM(mode="fast-go")
        final_llm = DummyLLM(mode="fast-go")
        answer_generator = AnswerGenerator(pipeline_llm, final_llm=final_llm)

        answer_generator.set_mode_profile("fast-plus")

        self.assertTrue(answer_generator.is_fast_mode())
        self.assertEqual(answer_generator.get_mode_profile(), "fast-plus")
        self.assertEqual(pipeline_llm.get_mode(), "fast-plus")
        self.assertEqual(final_llm.get_mode(), "fast-plus")

    def test_fast_go_keeps_fast_for_final(self):
        pipeline_llm = DummyLLM(mode="deep")
        final_llm = DummyLLM(mode="deep")
        answer_generator = AnswerGenerator(pipeline_llm, final_llm=final_llm)

        answer_generator.set_mode_profile("fast-go")

        self.assertTrue(answer_generator.is_fast_mode())
        self.assertEqual(pipeline_llm.get_mode(), "fast-go")
        self.assertEqual(final_llm.get_mode(), "fast-go")

    def test_build_messages_includes_fast_plus_context_agent_context(self):
        answer_generator = AnswerGenerator(DummyLLM(mode="fast"))
        answer_generator.set_mode_profile("fast-plus")
        session = SessionMemory()
        session.set_context_agent_state(
            {
                "intent_category": "route_benchmark",
                "rewritten_user_text": "请查询北京南到上海虹桥的标杆车",
                "resolved_route": "北京南-上海虹桥",
                "resolved_train_numbers": [],
                "resolved_emu": "",
                "resolved_date": "2026-03-25",
                "resolved_station_mentions": ["北京南", "上海虹桥"],
                "resolved_query_object": "s2s_benchmark",
                "confidence": 94,
                "reason": "reuse previous route benchmark intent",
            }
        )
        facts = {
            "queries": [],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }

        messages = answer_generator.build_messages(
            "有什么标杆车？",
            facts,
            session=session,
        )
        prompt_text = "\n".join(message["content"] for message in messages if message["role"] == "system")

        self.assertIn("FAST context-agent normalization", prompt_text)
        self.assertIn("intent_category=route_benchmark", prompt_text)
        self.assertIn("resolved_route=北京南-上海虹桥", prompt_text)

    def test_generate_structured_fast_finalizes_when_only_query_empty_exists(self):
        llm = DummyLLM(mode="fast")
        answer_generator = AnswerGenerator(llm)
        facts = {
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

        result = answer_generator.generate_structured(
            "从八达岭长城到南京今天有什么标杆车？",
            facts,
        )

        self.assertEqual(result["type"], "final")
        self.assertIn("没有查到可用的匹配结果", result["content"])
        self.assertEqual(llm.generate_calls, 0)

    def test_build_messages_skips_fast_context_bundle_for_placeholder_only_queries(self):
        answer_generator = CountingFastAnswerGenerator()
        facts = {
            "queries": [
                {
                    "type": "query_empty",
                    "domain": "railway",
                    "object": "station_to_station_mini",
                    "id": "八达岭长城-南京",
                    "date": "2026-04-02",
                }
            ],
            "analysis": [],
            "comparisons": [],
            "meta": {"errors": [], "warnings": [], "chat_messages": []},
        }

        messages = answer_generator.build_messages(
            "八达岭长城到南京今天有车吗",
            facts,
        )
        prompt_text = "\n".join(message["content"] for message in messages if message["role"] == "system")

        self.assertEqual(answer_generator.context_bundle_calls, 0)
        self.assertIn("Empty-result grounding", prompt_text)
        self.assertIn("没有查到可用的匹配结果", prompt_text)


    def test_build_pending_messages_includes_missing_slots_and_known_context(self):
        answer_generator = AnswerGenerator(DummyLLM(mode="fast"))
        session = SessionMemory()
        session.add_user_message("北京南到天津有哪些班次？")
        session.add_ai_message("今天北京南到天津班次很多。")
        messages = answer_generator.build_pending_messages(
            user_text="那明天的呢？",
            pending_payload={
                "question": "请补充关键信息",
                "slot": ["date"],
                "context": {"route": "北京南-天津", "dep": "北京南", "arr": "天津"},
            },
            session=session,
            tasks=[{"action": "pending"}],
            facts={"queries": [], "analysis": [], "comparisons": [], "meta": {"warnings": [], "errors": []}},
        )

        prompt_text = "\n".join(message["content"] for message in messages)
        self.assertIn("Missing slots", prompt_text)
        self.assertIn('"date"', prompt_text)
        self.assertIn("北京南-天津", prompt_text)
        self.assertIn("Latest user message", prompt_text)

    def test_stream_pending_question_uses_final_llm_stream(self):
        llm = DummyLLM(mode="fast")
        answer_generator = AnswerGenerator(llm)

        chunks = list(answer_generator.stream_pending_question(
            user_text="那明天的呢？",
            pending_payload={"question": "请补充关键信息", "slot": ["date"], "context": {}},
        ))

        self.assertEqual(chunks, [])
        self.assertEqual(llm.stream_calls, 1)

    def test_pending_writer_receives_capability_specific_slot_contract(self):
        answer_generator = AnswerGenerator(DummyLLM(mode="fast"))
        messages = answer_generator.build_pending_messages(
            user_text="G1的检票口在哪里？",
            pending_payload={
                "question": "请补充信息",
                "slot": ["station"],
                "context": {
                    "query_object": "train_station_access",
                    "missing_slot_contract": {
                        "capability": "train_station_access",
                        "missing_slots": ["station"],
                        "questions": [
                            {
                                "slot": "station",
                                "guidance": "同一车次沿途各站信息不同，请用户明确具体车站。",
                            }
                        ],
                    },
                },
            },
        )

        prompt_text = "\n".join(message["content"] for message in messages)
        self.assertIn("Capability-specific missing-slot contract", prompt_text)
        self.assertIn("train_station_access", prompt_text)
        self.assertIn("同一车次沿途各站信息不同", prompt_text)

if __name__ == "__main__":
    unittest.main()
