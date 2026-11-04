import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import web_app
from memory.conversation_store import ConversationStore
from tools.rail.rail_store import RailStore


class WebAssetApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = RailStore(str(Path(self.temp.name, "rail.db")))
        self.client = web_app.app.test_client()
        self._globals = (web_app._backend, web_app._store, web_app._session, web_app._settings, web_app._busy)

    def tearDown(self):
        web_app._backend, web_app._store, web_app._session, web_app._settings, web_app._busy = self._globals
        self.store.close_all()
        self.temp.cleanup()

    def test_invalid_hashes_are_rejected(self):
        self.assertEqual(self.client.get("/api/assets/coach/not-a-hash").status_code, 400)
        self.assertEqual(self.client.get("/api/assets/routes/../../bad").status_code, 404)

    def test_toolbar_has_fixed_multi_provider_attribution(self):
        template = Path("templates/index.html").read_text(encoding="utf-8")
        styles = Path("static/css/styles.css").read_text(encoding="utf-8")

        self.assertIn('class="data-source-bar"', template)
        self.assertIn("https://railgo.dev", template)
        self.assertIn("https://rail.re", template)
        self.assertIn("https://www.12306.cn/", template)
        self.assertIn("按问题动态选用", template)
        self.assertIn(".data-source-adaptive", styles)
        self.assertIn(".data-source-label", styles)

    def test_coach_asset_is_served_only_from_whitelisted_database_path(self):
        digest = "a" * 64
        root = Path(self.temp.name, "media", "coach")
        root.mkdir(parents=True)
        image = root.joinpath(f"{digest}.png")
        image.write_bytes(b"png-data")
        self.store.save_coach_media_asset({
            "car_code": "CR400BF-A-5059", "media_kind": "whole_train", "selector": "default",
            "content_hash": digest, "mime_type": "image/png", "local_path": str(image),
        })
        with patch.object(web_app, "railstore", self.store), patch.object(web_app, "user_data_path", return_value=str(root)):
            response = self.client.get(f"/api/assets/coach/{digest}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"png-data")
        response.close()

    def test_route_asset_endpoint_returns_geojson_and_svg(self):
        digest = "b" * 64
        self.store.save_route_asset(
            "G1", "2026-Q3", "path", digest,
            {"type": "FeatureCollection", "features": []},
            {"train": "G1", "station_count": 2},
            {"coordinate_system": "GCJ-02"},
            "<svg></svg>", {"provider": "RailGo"},
        )
        with patch.object(web_app, "railstore", self.store):
            response = self.client.get(f"/api/assets/routes/{digest}")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["geojson"]["type"], "FeatureCollection")
        self.assertEqual(payload["coordinate_metadata"]["coordinate_system"], "GCJ-02")

    def test_sse_attachment_is_forwarded_and_persisted_with_assistant_message(self):
        digest = "c" * 64

        class Settings:
            @staticmethod
            def has_api_key():
                return True

        class Session:
            @staticmethod
            def export_state():
                return {}

            @staticmethod
            def set_session_id(_cid):
                return None

        class Backend:
            @staticmethod
            def stream_events(_text, _session):
                yield {"type": "attachment", "attachment": {"type": "route_map", "asset_id": digest, "caption": "G1线路图"}}
                yield {"type": "token", "text": "线路图已经准备好了。"}
                yield {"type": "final", "text": "线路图已经准备好了。"}

        store = ConversationStore(root_dir=str(Path(self.temp.name, "conversations")))
        web_app._backend = Backend()
        web_app._store = store
        web_app._session = Session()
        web_app._settings = Settings()
        web_app._busy = False
        response = self.client.post("/api/chat", json={"text": "画G1线路图"}, buffered=True)
        body = response.data.decode("utf-8")
        self.assertIn('"type": "attachment"', body)
        self.assertEqual(store.messages[-1]["attachments"][0]["asset_id"], digest)
        self.assertEqual(store.messages[-1]["content"], "线路图已经准备好了。")
        # The title task can still update index.json after SSE done. Wait before
        # removing its temporary directory, especially on Windows file handles.
        deadline = time.monotonic() + 3
        while store._title_jobs_inflight and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(store._title_jobs_inflight)


if __name__ == "__main__":
    unittest.main()
