import unittest
from unittest.mock import patch

from agent.fast_tool_views import build_fast_views
from tools.rail.railgo_catalog_tools import query_railgo_catalog_tool


class RailGoCatalogToolsTest(unittest.TestCase):
    @patch("tools.rail.railgo_catalog_tools.fetch_station_preselect_v1")
    def test_station_preselect_is_bounded_and_compressed(self, fetch_mock):
        fetch_mock.return_value = {
            "data": [
                {"name": "句容西", "telecode": "JWH", "pinyin": "jurongxi"},
                {"name": "句容", "telecode": "JRH", "pinyin": "jurong"},
            ],
            "_railgo": {"provider": "RailGo", "api_version": "v1", "endpoint": "/api/station/preselect"},
        }

        result = query_railgo_catalog_tool("station_preselect", "句容")
        views = build_fast_views(result, raw_payload=result["evidence"])

        self.assertEqual(result["object"], "station_preselect")
        self.assertEqual(len(result["evidence"]), 2)
        self.assertTrue(any(view["view_type"] == "station_preselect_candidate_batch" for view in views))
        self.assertNotIn("https://railgo.dev", views[0]["text"])
        fetch_mock.assert_called_once_with("句容")

    @patch("tools.rail.railgo_catalog_tools.fetch_train_preselect_v1")
    def test_train_preselect_preserves_catalog_candidates(self, fetch_mock):
        fetch_mock.return_value = {
            "data": ["G1", "G10", "G100"],
            "_railgo": {"provider": "RailGo", "api_version": "v1", "endpoint": "/api/train/preselect"},
        }

        result = query_railgo_catalog_tool("train_preselect", "g1")

        self.assertEqual(result["id"], "G1")
        self.assertEqual(result["evidence"], ["G1", "G10", "G100"])
        fetch_mock.assert_called_once_with("G1")

    @patch("tools.rail.railgo_catalog_tools.fetch_random_train_v1")
    def test_random_train_uses_single_lucky_request(self, fetch_mock):
        fetch_mock.return_value = {
            "data": {
                "number": "G1",
                "fromStation": "北京南",
                "toStation": "上海",
                "departTime": "07:00",
            },
            "_railgo": {"provider": "RailGo", "api_version": "v1", "endpoint": "/api/lucky"},
        }

        result = query_railgo_catalog_tool("random_train", "random")

        self.assertEqual(result["id"], "G1")
        self.assertEqual(result["evidence"]["fromStation"], "北京南")
        fetch_mock.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
