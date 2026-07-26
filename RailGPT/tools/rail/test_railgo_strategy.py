import unittest
from unittest.mock import Mock, patch

from tools.rail.path_query import query_train_path
from tools.rail.railgo_client import RailGoNotFoundError
from tools.rail.s2s_query import query_s2s_route
from tools.rail.station_query import query_station


def path_payload(version="v1"):
    return {
        "numberFull": ["G1"],
        "rundays": ["20260715"],
        "timetable": [{"station": "北京南", "stationTelecode": "VNP"}],
        "_railgo": {"provider": "RailGo", "api_version": version},
    }


class RailGoPathStrategyTest(unittest.TestCase):
    @patch("tools.rail.path_query.fetch_train_main_v2")
    @patch("tools.rail.path_query.fetch_train_path_v1")
    @patch("tools.rail.path_query.railstore")
    def test_valid_local_certificate_avoids_all_network_calls(self, store, fetch_v1, fetch_v2):
        store.get_path.return_value = path_payload("v1")
        store.check_validity.return_value = {
            "exists": True,
            "cert_valid": True,
            "running_today": True,
            "need_refresh": False,
        }

        result = query_train_path("G1", "20260715")

        self.assertTrue(result["_status"]["running_today"])
        fetch_v1.assert_not_called()
        fetch_v2.assert_not_called()

    @patch("tools.rail.path_query.fetch_train_main_v2")
    @patch("tools.rail.path_query.fetch_train_path_v1")
    @patch("tools.rail.path_query.railstore")
    def test_v1_valid_certificate_avoids_v2(self, store, fetch_v1, fetch_v2):
        store.get_path.return_value = None
        store.check_validity.return_value = {
            "exists": True,
            "cert_valid": True,
            "running_today": True,
            "need_refresh": False,
        }
        fetch_v1.return_value = path_payload("v1")

        result = query_train_path("G1", "20260715")

        self.assertEqual(result["_railgo"]["api_version"], "v1")
        fetch_v2.assert_not_called()

    @patch("tools.rail.path_query.fetch_train_main_v2")
    @patch("tools.rail.path_query.fetch_train_path_v1")
    @patch("tools.rail.path_query.railstore")
    def test_v1_certificate_miss_uses_v2(self, store, fetch_v1, fetch_v2):
        store.get_path.return_value = None
        store.check_validity.side_effect = [
            {"exists": True, "cert_valid": False, "need_refresh": True},
            {"exists": True, "cert_valid": True, "running_today": True, "need_refresh": False},
        ]
        fetch_v1.return_value = path_payload("v1")
        fetch_v2.return_value = path_payload("v2")

        result = query_train_path("G1", "20260715")

        self.assertEqual(result["_railgo"]["api_version"], "v2")
        fetch_v2.assert_called_once_with("G1", "20260715")

    @patch("tools.rail.path_query.fetch_train_main_v2")
    @patch("tools.rail.path_query.fetch_train_path_v1")
    @patch("tools.rail.path_query.railstore")
    def test_v2_not_running_keeps_v1_skeleton_but_marks_date_false(self, store, fetch_v1, fetch_v2):
        store.get_path.return_value = None
        store.check_validity.return_value = {
            "exists": True,
            "cert_valid": False,
            "need_refresh": True,
        }
        fetch_v1.return_value = path_payload("v1")
        fetch_v2.side_effect = RailGoNotFoundError("not running")

        result = query_train_path("G1", "20260715")

        self.assertFalse(result["_status"]["running_today"])
        self.assertEqual(result["_status"]["exact_date_source"], "RailGo v2")


class RailGoS2SStrategyTest(unittest.TestCase):
    @patch("tools.rail.s2s_query.fetch_s2s_v1")
    @patch("tools.rail.s2s_query.railstore")
    def test_valid_s2s_certificate_avoids_network(self, store, fetch_s2s):
        cached = {"trains": [{"number": "G1"}]}
        store.get_s2s.return_value = cached
        store.check_validity.return_value = {
            "exists": True,
            "cert_valid": True,
            "running_today": True,
            "need_refresh": False,
        }

        result = query_s2s_route("VNP", "AOH", "20260715")

        self.assertIs(result["payload"], cached)
        fetch_s2s.assert_not_called()

    @patch("tools.rail.s2s_query.inject_s2s_into_path")
    @patch("tools.rail.s2s_query.fetch_s2s_v1")
    @patch("tools.rail.s2s_query.railstore")
    def test_network_result_matches_cached_wrapper_shape(self, store, fetch_s2s, inject):
        store.get_s2s.return_value = None
        store.check_validity.return_value = {
            "exists": True,
            "cert_valid": True,
            "running_today": True,
            "need_refresh": False,
        }
        train = {"number": "G1", "rundays": ["20260715"], "timetable": []}
        fetch_s2s.return_value = [train]

        result = query_s2s_route("北京南", "上海虹桥", "20260715")

        self.assertEqual(result["payload"]["trains"], [train])
        self.assertEqual(result["source"]["api_version"], "v1")
        store.save_s2s.assert_called_once()
        inject.assert_called_once()


class RailGoStationStrategyTest(unittest.TestCase):
    @patch("tools.rail.station_query.fetch_station_v1")
    @patch("tools.rail.station_query.railstore")
    def test_cached_station_avoids_network(self, store, fetch_station):
        cached = {"data": {"name": "北京南", "telecode": "VNP"}}
        store.get_station.return_value = cached

        result = query_station("VNP")

        self.assertIs(result, cached)
        fetch_station.assert_not_called()


if __name__ == "__main__":
    unittest.main()
