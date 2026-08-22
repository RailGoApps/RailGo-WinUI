import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from tools.rail.operational_cache import (
    BEIJING_TZ,
    RailGoOperationalCacheService,
)
from tools.rail.rail_store import RailStore
from tools.rail.railgo_client import RailGoTemporaryError


def envelope(data):
    return {
        "data": data,
        "_railgo": {
            "provider": "RailGo",
            "api_version": "v2",
            "endpoint": "/api/v2/test",
            "fetched_at": "2026-07-16T10:00:00",
        },
    }


class FakeClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class RailGoOperationalCacheTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = RailStore(str(Path(self.temp.name, "rail.db")))
        self.clock = FakeClock(datetime(2026, 7, 16, 10, 0, tzinfo=BEIJING_TZ))
        self.service = RailGoOperationalCacheService(self.store, now_fn=self.clock)

    def tearDown(self):
        self.store.close_all()
        self.temp.cleanup()

    def test_station_board_reuses_five_minute_certificate_then_refreshes(self):
        calls = []

        def fetcher():
            calls.append(self.clock.value)
            return envelope([])

        first = self.service.get_or_fetch(
            "station_board", "NKH|departure", fetcher, service_date="2026-07-16"
        )
        self.clock.value += timedelta(minutes=4, seconds=59)
        second = self.service.get_or_fetch(
            "station_board", "NKH|departure", fetcher, service_date="2026-07-16"
        )
        self.clock.value += timedelta(seconds=1)
        third = self.service.get_or_fetch(
            "station_board", "NKH|departure", fetcher, service_date="2026-07-16"
        )

        self.assertEqual([first["cache_status"], second["cache_status"], third["cache_status"]], ["network", "hit", "network"])
        self.assertEqual(len(calls), 2)

    def test_delay_certificate_expires_after_fifteen_minutes(self):
        calls = 0

        def fetcher():
            nonlocal calls
            calls += 1
            return envelope([{"stationName": "南京南", "delayStatus": "正点"}])

        self.service.get_or_fetch("train_delay", "G1", fetcher, service_date="2026-07-16")
        self.clock.value += timedelta(minutes=14, seconds=59)
        hit = self.service.get_or_fetch("train_delay", "G1", fetcher, service_date="2026-07-16")
        self.clock.value += timedelta(seconds=1)
        refreshed = self.service.get_or_fetch("train_delay", "G1", fetcher, service_date="2026-07-16")

        self.assertEqual(hit["cache_status"], "hit")
        self.assertEqual(refreshed["cache_status"], "network")
        self.assertEqual(calls, 2)

    def test_rolling_ttl_is_capped_at_beijing_midnight(self):
        self.clock.value = datetime(2026, 7, 16, 23, 58, tzinfo=BEIJING_TZ)
        calls = 0

        def fetcher():
            nonlocal calls
            calls += 1
            return envelope([])

        first = self.service.get_or_fetch(
            "train_delay", "G1", fetcher, service_date="2026-07-16"
        )
        self.assertEqual(first["expires_at"], "2026-07-17T00:00:00+08:00")
        self.clock.value = datetime(2026, 7, 17, 0, 0, tzinfo=BEIJING_TZ)
        self.service.get_or_fetch("train_delay", "G1", fetcher, service_date="2026-07-17")
        self.assertEqual(calls, 2)

    def test_station_access_is_valid_until_fetch_day_midnight(self):
        calls = 0

        def fetcher():
            nonlocal calls
            calls += 1
            return envelope({"entrance": ["北进站口"], "exit": [], "platform": "3"})

        first = self.service.get_or_fetch(
            "train_station_access",
            "G1|NKH|20260720|departure",
            fetcher,
            service_date="2026-07-20",
        )
        self.clock.value = datetime(2026, 7, 16, 23, 59, 59, tzinfo=BEIJING_TZ)
        hit = self.service.get_or_fetch(
            "train_station_access",
            "G1|NKH|20260720|departure",
            fetcher,
            service_date="2026-07-20",
        )
        self.clock.value = datetime(2026, 7, 17, 0, 0, tzinfo=BEIJING_TZ)
        refreshed = self.service.get_or_fetch(
            "train_station_access",
            "G1|NKH|20260720|departure",
            fetcher,
            service_date="2026-07-20",
        )

        self.assertEqual(first["expires_at"], "2026-07-17T00:00:00+08:00")
        self.assertEqual(hit["cache_status"], "hit")
        self.assertEqual(refreshed["cache_status"], "network")
        self.assertEqual(calls, 2)

    def test_valid_empty_list_is_cached(self):
        calls = 0

        def fetcher():
            nonlocal calls
            calls += 1
            return envelope([])

        self.service.get_or_fetch("station_board", "NKH|arrival", fetcher, service_date="2026-07-16")
        cached = self.service.get_or_fetch("station_board", "NKH|arrival", fetcher, service_date="2026-07-16")

        self.assertEqual(cached["payload"]["data"], [])
        self.assertTrue(cached["payload"]["_railgo"]["cached"])
        self.assertEqual(calls, 1)

    def test_hash_mismatch_rejects_row_and_refreshes(self):
        self.service.get_or_fetch(
            "station_board", "NKH|departure", lambda: envelope([]), service_date="2026-07-16"
        )
        conn = self.store._get_conn()
        conn.execute(
            "UPDATE railgo_operational_cache SET payload_hash='broken' WHERE object='station_board'"
        )
        conn.commit()
        calls = 0

        def fetcher():
            nonlocal calls
            calls += 1
            return envelope([{"trainNum": "G1"}])

        refreshed = self.service.get_or_fetch(
            "station_board", "NKH|departure", fetcher, service_date="2026-07-16"
        )
        self.assertEqual(refreshed["cache_status"], "network")
        self.assertEqual(calls, 1)

    def test_expired_cache_is_never_returned_when_refresh_fails(self):
        self.service.get_or_fetch(
            "train_delay", "G1", lambda: envelope([{"delayStatus": "正点"}]), service_date="2026-07-16"
        )
        self.clock.value += timedelta(minutes=16)

        with self.assertRaises(RailGoTemporaryError):
            self.service.get_or_fetch(
                "train_delay",
                "G1",
                lambda: (_ for _ in ()).throw(RailGoTemporaryError("offline")),
                service_date="2026-07-16",
            )

    def test_singleflight_allows_only_one_network_fetch(self):
        calls = 0
        calls_lock = threading.Lock()

        def fetcher():
            nonlocal calls
            with calls_lock:
                calls += 1
            time.sleep(0.05)
            return envelope([])

        def query():
            return self.service.get_or_fetch(
                "station_board", "NKH|departure", fetcher, service_date="2026-07-16"
            )["cache_status"]

        with ThreadPoolExecutor(max_workers=6) as pool:
            statuses = list(pool.map(lambda _: query(), range(6)))

        self.assertEqual(calls, 1)
        self.assertEqual(statuses.count("network"), 1)
        self.assertEqual(statuses.count("hit"), 5)


if __name__ == "__main__":
    unittest.main()
