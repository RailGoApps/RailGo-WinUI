import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from agent.fast_tool_views import build_fast_views
from tools.rail.operational_cache import BEIJING_TZ, RailGoOperationalCacheService
from tools.rail.rail_store import RailStore
from tools.rail.railgo_v2_tools import query_railgo_v2_tool, station_dict


def envelope(data, endpoint):
    return {
        "data": data,
        "_railgo": {
            "provider": "RailGo",
            "api_version": "v2",
            "endpoint": endpoint,
            "fetched_at": "2026-07-15T16:00:00",
        },
    }


class RecordingPSW:
    def __init__(self):
        self.states = []

    def set_state(self, state, detail=""):
        self.states.append((state.name, detail))


class RailGoV2ToolsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = RailStore(str(Path(self.temp.name, "rail.db")))
        fixed_now = datetime(2026, 7, 16, 16, 0, tzinfo=BEIJING_TZ)
        self.cache_service = RailGoOperationalCacheService(
            self.store,
            now_fn=lambda: fixed_now,
        )
        self.cache_patcher = patch(
            "tools.rail.railgo_v2_tools.railgo_operational_cache",
            self.cache_service,
        )
        self.cache_patcher.start()

    def tearDown(self):
        self.cache_patcher.stop()
        self.store.close_all()
        self.temp.cleanup()

    @patch("tools.rail.railgo_v2_tools.fetch_station_big_screen_v2")
    @patch.object(station_dict, "telecode_of", return_value="JWH")
    def test_station_board_converts_exact_station_name_locally(self, _telecode, fetch):
        fetch.return_value = envelope([], "/api/v2/getStationBigScreen")

        result = query_railgo_v2_tool("station_board", "句容西|departure")
        psw = RecordingPSW()
        cached = query_railgo_v2_tool("station_board", "句容西|departure", psw=psw)

        self.assertEqual(result["id"], "JWH|departure")
        fetch.assert_called_once_with("JWH", "departure")
        self.assertEqual(result["cache_status"], "network")
        self.assertEqual(cached["cache_status"], "hit")
        self.assertEqual(result["freshness"]["age_seconds"], 0)
        self.assertEqual(result["grounded_slots"]["direction"], "departure")
        self.assertEqual(result["grounded_slots"]["station"], station_dict.name_of("JWH"))
        self.assertIn("RAILGO_OP_CACHE_HIT", [state for state, _ in psw.states])
        self.assertNotIn("QUERYING", [state for state, _ in psw.states])

    @patch("tools.rail.railgo_v2_tools.fetch_train_station_access_v2")
    @patch.object(station_dict, "telecode_of", return_value="NKH")
    def test_station_access_cache_separates_date_and_direction(self, _telecode, fetch):
        fetch.return_value = envelope(
            {"entrance": ["北进站口"], "exit": [], "platform": "3"},
            "/api/v2/getExit",
        )

        first = query_railgo_v2_tool(
            "train_station_access", "G1|南京南|departure", date="2026-07-16"
        )
        cached = query_railgo_v2_tool(
            "train_station_access", "G1|南京南|departure", date="2026-07-16"
        )
        query_railgo_v2_tool(
            "train_station_access", "G1|南京南|arrival", date="2026-07-16"
        )
        query_railgo_v2_tool(
            "train_station_access", "G1|南京南|departure", date="2026-07-17"
        )

        self.assertEqual(first["cache_status"], "network")
        self.assertEqual(cached["cache_status"], "hit")
        self.assertEqual(fetch.call_count, 3)
        rows = self.store._get_conn().execute(
            "SELECT cache_key FROM railgo_operational_cache WHERE object='train_station_access'"
        ).fetchall()
        self.assertEqual(len(rows), 3)

    @patch("tools.rail.railgo_v2_tools.fetch_train_delay_all_v2")
    def test_delay_evidence_is_split_into_mining_views(self, fetch):
        fetch.return_value = envelope(
            [
                {
                    "stationName": f"站{i}",
                    "stationTelecode": "VNP",
                    "delayStatus": "正点",
                    "delayStatusCode": "ON_TIME",
                    "delayTime": 0,
                }
                for i in range(15)
            ],
            "/api/v2/getTrainDelayAll",
        )

        result = query_railgo_v2_tool("train_delay", "G1")
        views = build_fast_views(result, result["evidence"])

        self.assertEqual(result["object"], "train_delay")
        self.assertEqual(views[0]["view_type"], "train_delay_overview")
        self.assertGreaterEqual(len(views), 3)
        prompt_view_text = "\n".join(view["text"] for view in views)
        self.assertNotIn("SOURCE:", prompt_view_text)
        self.assertNotIn("railgo.dev", prompt_view_text)
        self.assertIn("OBSERVED_AT:", prompt_view_text)

    @patch("tools.rail.railgo_v2_tools.route_asset_service.get_route")
    def test_map_geometry_is_compacted_but_keeps_mining_features(self, get_route):
        get_route.return_value = {
            "evidence": {
                "coordinate_source": "RailGo GCJ-02; converted to WGS-84 for OSM display",
                "station_count": 2,
                "stations": ["北京南", "上海虹桥"],
                "segment_count": 1,
                "segments": ["北京南-上海虹桥"],
                "raw_point_count": 3,
                "display_point_count": 3,
                "estimated_polyline_km": 1200.0,
                "estimated_direct_km": 1000.0,
                "distance_note": "Coordinate estimate, not railway operating mileage.",
            },
            "source": envelope({}, "/api/v2/mapLine")["_railgo"],
            "cache_status": "network",
            "artifacts": [{"type": "route_map", "asset_id": "a" * 64}],
        }

        result = query_railgo_v2_tool("train_route_map", "G1")
        views = build_fast_views(result, result["evidence"])

        self.assertEqual(result["evidence"]["display_point_count"], 3)
        self.assertNotIn("geojson", result["evidence"])
        self.assertEqual(result["artifacts"][0]["type"], "route_map")
        self.assertTrue(any(view["view_type"] == "train_route_map_station_batch" for view in views))

    @patch("tools.rail.railgo_v2_tools.coach_asset_service.get_layout")
    def test_coach_layout_preserves_assignment_boundary(self, get_layout):
        get_layout.return_value = {
            "evidence": {
                "carCode": "CR400BF-S-3158",
                "carType": "复兴号",
                "trainStyle": "CR400BF-S",
                "carInfo": [{"pictureName": "列车定员", "pictureValue": "619人"}],
                "coachPicList": [],
                "coachDetailPicList": [],
                "mediaCatalog": [],
            },
            "source": envelope({}, "/api/v2/getCoachPic")["_railgo"],
            "cache_status": "network",
        }

        result = query_railgo_v2_tool("coach_layout", "G1")
        views = build_fast_views(result, result["evidence"])

        self.assertEqual(result["evidence"]["carCode"], "CR400BF-S-3158")
        self.assertIn("historical assignment analysis belongs to rail.re", views[0]["text"])


if __name__ == "__main__":
    unittest.main()
