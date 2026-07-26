import json
import os
import tempfile
import unittest

from memory.conversation_store import ConversationStore, DEFAULT_NEW_TITLE
from memory.session import SessionMemory


class FakeLLM:
    def __init__(self, title="测试标题"):
        self.title = title
        self.generate_called = 0

    def generate(self, messages):
        self.generate_called += 1
        return self.title


class ConversationStoreTest(unittest.TestCase):
    def make_store(self, llm=None):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        store = ConversationStore(root_dir=temp_dir.name, llm=llm)
        return store, temp_dir.name

    def test_new_conversation_does_not_block_on_title_llm(self):
        llm = FakeLLM()
        store, _ = self.make_store(llm=llm)
        scheduled = []
        store._schedule_title_update = lambda text, cid=None: scheduled.append((cid, text))

        cid = store.new_conversation("南京南到徐州东最快的车")

        self.assertEqual(store.current_id, cid)
        self.assertEqual(store.title, DEFAULT_NEW_TITLE)
        self.assertEqual(llm.generate_called, 0)
        self.assertEqual(scheduled, [(cid, "南京南到徐州东最快的车")])

    def test_append_user_schedules_async_title_for_placeholder_conversation(self):
        store, _ = self.make_store()
        cid = store.new_conversation("")
        scheduled = []
        store._schedule_title_update = lambda text, cid=None: scheduled.append((cid, text))

        store.append_user("G87 最近是什么车底")

        self.assertEqual(store.current_id, cid)
        self.assertEqual(len(store.messages), 1)
        self.assertEqual(store.messages[0]["role"], "user")
        self.assertEqual(scheduled, [(cid, "G87 最近是什么车底")])

    def test_async_title_updates_target_conversation_only(self):
        llm = FakeLLM(title="会话一标题")
        store, root_dir = self.make_store(llm=llm)
        cid1 = store.new_conversation("")
        cid2 = store.new_conversation("")

        store._auto_title_async(cid1, "南京南到徐州东最快的车")

        self.assertEqual(store.current_id, cid2)
        self.assertEqual(store.title, DEFAULT_NEW_TITLE)
        self.assertEqual(llm.generate_called, 1)

        with open(os.path.join(root_dir, f"{cid1:03d}.json"), "r", encoding="utf-8") as handle:
            data1 = json.load(handle)

        with open(os.path.join(root_dir, f"{cid2:03d}.json"), "r", encoding="utf-8") as handle:
            data2 = json.load(handle)

        self.assertEqual(data1["title"], "会话一标题")
        self.assertEqual(data2["title"], DEFAULT_NEW_TITLE)

    def test_save_and_restore_session_memory_state(self):
        store, _ = self.make_store()
        cid = store.new_conversation("")
        session = SessionMemory()
        session.set_session_id(cid)
        session.add_user_message("南京南到徐州东最快标杆车")
        session.update_anchor(route="南京南-徐州东", date="2026-03-25", query_type="s2s_benchmark")
        session.enter_followup(
            question="请告诉我具体日期。",
            slot=["date"],
            context={"route": "南京南-徐州东"},
        )

        self.assertTrue(store.save_session_memory(session))

        restored = SessionMemory()
        store.build_session_memory(restored)

        self.assertEqual(restored.resolve_anchor("route"), "南京南-徐州东")
        self.assertEqual(restored.resolve_anchor("date"), "2026-03-25")
        self.assertTrue(restored.in_followup())
        self.assertEqual(restored.followup_slots.get("context", {}).get("route"), "南京南-徐州东")

    def test_save_and_restore_recent_entity_pool(self):
        store, _ = self.make_store()
        cid = store.new_conversation("")
        session = SessionMemory()
        session.set_session_id(cid)
        session.update_from_facts(
            {
                "queries": [
                    {
                        "domain": "railway",
                        "object": "s2s_benchmark",
                        "id": "南京南-徐州东",
                        "date": "2026-03-25",
                        "pretty": "G84 20:15→21:21\nG94 09:56→11:02",
                    }
                ]
            }
        )

        self.assertTrue(store.save_session_memory(session))

        restored = SessionMemory()
        store.build_session_memory(restored)

        pool = restored.get_recent_entity_pool()
        self.assertIn("G84", pool.get("trains", []))
        self.assertIn("G94", pool.get("trains", []))

    def test_assistant_attachments_are_persisted_and_exported(self):
        store, _ = self.make_store()
        cid = store.new_conversation("")
        digest = "a" * 64
        store.append_ai(
            "这是线路与车厢附件。",
            attachments=[
                {"type": "coach_image", "asset_id": digest, "caption": "G1整列图", "source": "RailGo"},
                {"type": "route_map", "asset_id": "b" * 64, "caption": "G1线路图", "source": "RailGo + OpenStreetMap"},
                {"type": "coach_image", "asset_id": "../bad", "caption": "invalid"},
            ],
        )
        self.assertEqual(len(store.messages[-1]["attachments"]), 2)
        exported = store.export_markdown(cid)
        with open(exported, "r", encoding="utf-8") as handle:
            markdown = handle.read()
        self.assertIn(f"/api/assets/coach/{digest}", markdown)
        self.assertIn("RailGo + OpenStreetMap", markdown)


if __name__ == "__main__":
    unittest.main()
