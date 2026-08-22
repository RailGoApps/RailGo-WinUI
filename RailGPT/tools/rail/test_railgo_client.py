import unittest
from unittest.mock import Mock, patch

from tools.rail.railgo_client import (
    RailGoNotFoundError,
    RailGoTemporaryError,
    _get_json,
    fetch_coach_pic_v2,
    fetch_map_line_v2,
    fetch_random_train_v1,
    fetch_s2s_v1,
    fetch_station_big_screen_v2,
    fetch_station_preselect_v1,
    fetch_train_delay_all_v2,
    fetch_train_main_v2,
    fetch_train_preselect_v1,
    fetch_train_station_access_v2,
    normalize_railgo_date,
)
from tools.rail.http_client import RAILGO_HEADERS


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class RailGoClientTest(unittest.TestCase):
    def test_normalize_date_accepts_documented_formats(self):
        self.assertEqual(normalize_railgo_date("20260715"), "20260715")
        self.assertEqual(normalize_railgo_date("2026-07-15"), "20260715")
        self.assertEqual(normalize_railgo_date("2026.07.15"), "20260715")

    @patch("tools.rail.railgo_client.http_get")
    def test_v2_train_main_is_normalized_for_legacy_tools(self, get_mock):
        get_mock.return_value = FakeResponse(
            payload={
                "success": True,
                "msg": "Succeeded.",
                "data": {
                    "bureau": "H",
                    "bureauShortName": "上局",
                    "car": "CR400BF-S",
                    "numberFull": ["G1"],
                    "rundays": ["20260715", "20260716"],
                    "timetable": [{"station": "北京南", "stationTelecode": "VNP"}],
                },
            }
        )

        payload = fetch_train_main_v2("g1", "2026-07-15")

        self.assertEqual(payload["bureauName"], "上局")
        self.assertEqual(payload["numberFull"], ["G1"])
        self.assertEqual(payload["_railgo"]["api_version"], "v2")
        get_mock.assert_called_once_with(
            "https://rg-api.zenglingkun.cn/api/v2/getTrainMain",
            params={"trainNum": "G1", "date": "20260715"},
            timeout=20,
            min_interval=0.3,
            headers=RAILGO_HEADERS,
        )

    def test_railgo_headers_identify_railgpt_without_user_secrets(self):
        self.assertEqual(RAILGO_HEADERS["X-Client-Name"], "RailGPT")
        self.assertEqual(RAILGO_HEADERS["X-Client-Version"], "2.6.6")
        self.assertRegex(RAILGO_HEADERS["X-RailGPT-Installation-ID"], r"^[0-9a-f-]{36}$")
        self.assertNotIn("api", " ".join(RAILGO_HEADERS.values()).lower().replace("railgpt", ""))

    @patch("tools.rail.railgo_client.http_get")
    def test_v2_400_is_semantic_not_found(self, get_mock):
        get_mock.return_value = FakeResponse(
            status_code=400,
            payload={"success": False, "msg": "Train not found."},
        )

        with self.assertRaises(RailGoNotFoundError):
            fetch_train_main_v2("G99999", "20260715")

    @patch("tools.rail.railgo_client.http_get")
    def test_v1_s2s_keeps_raw_train_list_contract(self, get_mock):
        get_mock.return_value = FakeResponse(payload=[{"number": "G1"}])

        payload = fetch_s2s_v1("VNP", "AOH", "20260715")

        self.assertEqual(payload, [{"number": "G1"}])

    @patch("tools.rail.railgo_client.http_get")
    def test_v1_catalog_endpoints_keep_documented_contracts(self, get_mock):
        get_mock.side_effect = [
            FakeResponse(payload=[{"name": "句容西", "telecode": "JWH"}]),
            FakeResponse(payload=["G1", "G10"]),
            FakeResponse(
                payload={
                    "number": "G1",
                    "fromStation": "北京南",
                    "toStation": "上海",
                    "departTime": "07:00",
                }
            ),
        ]

        stations = fetch_station_preselect_v1("句容")
        trains = fetch_train_preselect_v1("g1")
        lucky = fetch_random_train_v1()

        self.assertEqual(stations["data"][0]["telecode"], "JWH")
        self.assertEqual(trains["data"], ["G1", "G10"])
        self.assertEqual(lucky["data"]["number"], "G1")
        self.assertEqual(stations["_railgo"]["api_version"], "v1")
        self.assertEqual(trains["_railgo"]["keyword"], "G1")

    @patch("tools.rail.railgo_client.http_get")
    @patch("utils.net_retry.time.sleep")
    @patch("utils.net_retry.random.uniform", return_value=0)
    def test_transient_failure_retries_with_bounded_backoff(self, _uniform, _sleep, get_mock):
        get_mock.return_value = FakeResponse(
            status_code=503,
            payload={"msg": "temporarily unavailable"},
        )

        with self.assertRaises(RailGoTemporaryError):
            _get_json("https://example.invalid/railgo")

        self.assertEqual(get_mock.call_count, 5)

    @patch("tools.rail.railgo_client.http_get")
    def test_v2_dynamic_endpoints_keep_source_metadata(self, get_mock):
        get_mock.side_effect = [
            FakeResponse(payload={"success": True, "data": [{"delayStatus": "正点"}]}),
            FakeResponse(payload={"success": True, "data": {"entrance": [], "exit": [], "platform": "1"}}),
            FakeResponse(payload={"success": True, "data": [{"trainNum": "G1"}]}),
            FakeResponse(payload={"success": True, "data": {"carCode": "CR400BF-S-3158"}}),
            FakeResponse(payload={"success": True, "data": {"stations": [], "train": {}}}),
        ]

        delay = fetch_train_delay_all_v2("G1")
        access = fetch_train_station_access_v2("G1", "VNP", "2026-07-15", "departure")
        board = fetch_station_big_screen_v2("VNP", "departure")
        coach = fetch_coach_pic_v2("G1")
        route_map = fetch_map_line_v2("G1")

        for payload in (delay, access, board, coach, route_map):
            self.assertEqual(payload["_railgo"]["provider"], "RailGo")
            self.assertEqual(payload["_railgo"]["api_version"], "v2")
            self.assertEqual(payload["_railgo"]["url"], "https://railgo.dev")
        self.assertEqual(access["_railgo"]["date"], "20260715")


if __name__ == "__main__":
    unittest.main()
