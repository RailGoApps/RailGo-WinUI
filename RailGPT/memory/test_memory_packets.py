import unittest
import tempfile
from pathlib import Path

from memory.curator import MemoryCuratorAgent
from memory.packets import MEMORY_SCHEMA_VERSION, MemoryContextPackage, MemoryPacket
from memory.profile_index import LongTermProfileIndex


class MemoryPacketSchemaTest(unittest.TestCase):
    def test_packet_roundtrip_normalizes_schema(self):
        packet = MemoryPacket(
            id="test:1",
            scope="tool_evidence",
            kind="tool_fact",
            source="tool_fact",
            text="G1 path evidence",
            entities={"trains": ["G1", "G1"], "routes": ["北京南-上海虹桥"]},
            confidence=96,
            tags=["tool_evidence", "tool_evidence"],
        )

        payload = packet.to_dict()
        restored = MemoryPacket.from_dict(payload)

        self.assertEqual(payload["schema_version"], MEMORY_SCHEMA_VERSION)
        self.assertEqual(restored.confidence, 0.96)
        self.assertEqual(restored.entities["trains"], ["G1"])
        self.assertEqual(restored.tags, ["tool_evidence"])

    def test_context_package_roundtrip(self):
        package = MemoryContextPackage(
            hard_anchors={"train": "G1"},
            profile_index=[{"value": "G1", "allowed_usage": "soft_profile_only"}],
            soft_context=[{"summary_l0": "soft"}],
            answer_context=[{"summary_l0": "answer"}],
            rejected=[{"reason": "soft-only"}],
        )

        restored = MemoryContextPackage.from_dict(package.to_dict())

        self.assertEqual(restored.hard_anchors["train"], "G1")
        self.assertEqual(restored.profile_index[0]["allowed_usage"], "soft_profile_only")
        self.assertEqual(restored.soft_context[0]["summary_l0"], "soft")
        self.assertEqual(restored.rejected[0]["reason"], "soft-only")

    def test_curator_marks_assistant_statement_soft_only(self):
        packets = MemoryCuratorAgent().curate_turn(
            user_text="G1今天用什么车？",
            ai_text="G1 今天可能使用 CR400BF-S。",
        )

        assistant_packets = [packet for packet in packets if packet.source == "assistant_statement"]

        self.assertTrue(assistant_packets)
        self.assertIn("soft_only", assistant_packets[0].tags)
        self.assertIn("no_hard_anchor", assistant_packets[0].tags)

    def test_profile_index_ignores_assistant_and_distinguishes_interest_from_preference(self):
        with tempfile.TemporaryDirectory() as root_dir:
            index = LongTermProfileIndex(root_dir)
            index.update(
                [
                    MemoryPacket(
                        id="user:1",
                        scope="dialogue",
                        kind="user_claim",
                        source="explicit_user",
                        text="G813 今天有晚点吗？",
                        entities={"trains": ["G813"]},
                        confidence=0.92,
                    ),
                    MemoryPacket(
                        id="user:2",
                        scope="dialogue",
                        kind="user_claim",
                        source="explicit_user",
                        text="再看看 G813 的线路",
                        entities={"trains": ["G813"]},
                        confidence=0.92,
                    ),
                    MemoryPacket(
                        id="user:3",
                        scope="dialogue",
                        kind="user_claim",
                        source="explicit_user",
                        text="G813 的担当最近稳定吗？",
                        entities={"trains": ["G813"]},
                        confidence=0.92,
                    ),
                    MemoryPacket(
                        id="assistant:1",
                        scope="dialogue",
                        kind="assistant_statement",
                        source="assistant_statement",
                        text="你最喜欢 G20。",
                        entities={"trains": ["G20"]},
                        confidence=0.35,
                    ),
                ]
            )

            recalled = index.retrieve("猜猜我最喜欢的车次", limit=6)

            self.assertEqual(recalled[0]["value"], "G813")
            self.assertEqual(recalled[0]["classification"], "recurring_interest")
            self.assertIn("不代表最喜欢", recalled[0]["summary_l0"])
            self.assertNotIn("G20", {item["value"] for item in recalled})
            self.assertTrue(Path(root_dir, "MEMORY.md").exists())
            self.assertTrue(Path(root_dir, "topics", "train.json").exists())

    def test_one_off_query_is_not_consolidated_into_long_term_profile(self):
        with tempfile.TemporaryDirectory() as root_dir:
            index = LongTermProfileIndex(root_dir)
            changed = index.update(
                [
                    MemoryPacket(
                        id="user:one-off",
                        scope="dialogue",
                        kind="user_claim",
                        source="explicit_user",
                        text="G9999 今天走哪里？",
                        entities={"trains": ["G9999"]},
                        confidence=0.92,
                    )
                ]
            )

            self.assertEqual(changed, 0)
            self.assertEqual(index.retrieve("你记得我关注什么吗"), [])
            self.assertEqual(index.data["entries"], {})

    def test_curator_records_entity_free_explicit_preference(self):
        packets = MemoryCuratorAgent().curate_turn(user_text="我喜欢坐靠窗的位置")

        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0].kind, "preference")
        self.assertIn("explicit_preference", packets[0].tags)

    def test_tool_evidence_memory_does_not_persist_provider_provenance(self):
        packets = MemoryCuratorAgent().curate_turn(
            facts={
                "queries": [{
                    "object": "train_delay",
                    "id": "G1",
                    "pretty": "LIVE DELAY\nSOURCE: RailGo v2 url=https://railgo.dev\nG1 正点",
                    "source": {
                        "provider": "RailGo",
                        "api_version": "v2",
                        "endpoint": "/api/v2/getTrainDelayAll",
                        "url": "https://railgo.dev",
                    },
                    "evidence": [{"stationName": "南京南", "delayStatus": "正点"}],
                }]
            }
        )

        packet = next(item for item in packets if item.source == "tool_fact")
        self.assertNotIn("SOURCE:", packet.text)
        self.assertNotIn("RailGo", packet.overview_l1)
        self.assertNotIn("railgo.dev", packet.overview_l1)


if __name__ == "__main__":
    unittest.main()
