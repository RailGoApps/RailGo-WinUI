import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tools.rail.rail_store import (
    RailStore,
    extract_path_runways,
    extract_s2s_runways,
    normalize_run_days,
)


class RailStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name, "rail_store.db"))
        self.store = RailStore(self.db_path)

    def tearDown(self):
        self.store.close_all()
        self.temp_dir.cleanup()

    def test_run_days_are_normalized_deduplicated_and_sorted(self):
        self.assertEqual(
            normalize_run_days(["2026-07-16", "bad", 20260715, "20260716"]),
            ["20260715", "20260716"],
        )

    def test_extractors_accept_v1_v2_and_wrapped_shapes(self):
        self.assertEqual(
            extract_path_runways({"train": {"runways": ["2026-07-15"]}}),
            ["20260715"],
        )
        wrapped = {
            "payload": {
                "trains": [
                    {"rundays": ["20260715"]},
                    {"runways": ["2026-07-16"]},
                ]
            }
        }
        self.assertEqual(extract_s2s_runways(wrapped), ["20260715", "20260716"])

    def test_path_certificate_accepts_hyphenated_query_date(self):
        self.store.save_path(
            "g1",
            {"numberFull": ["G1"], "rundays": ["20260715", "20260716"]},
        )
        status = self.store.check_validity("path_cache", "G1", "2026-07-16")
        self.assertTrue(status["cert_valid"])
        self.assertTrue(status["running_today"])
        self.assertEqual(status["certificate_days"], 2)

    def test_station_validity_checks_row_existence(self):
        missing = self.store.check_validity("station_cache", "VNP")
        self.assertFalse(missing["exists"])
        self.assertTrue(missing["need_refresh"])

        self.store.save_station("vnp", {"data": {"name": "Beijing South"}})
        present = self.store.check_validity("station_cache", "VNP")
        self.assertTrue(present["exists"])
        self.assertTrue(present["cert_valid"])

    def test_legacy_payload_gets_non_persistent_source_label(self):
        self.store.save_path("G1", {"rundays": ["20260715"]})
        payload = self.store.get_path("G1")
        self.assertEqual(payload["_railgo"]["api_version"], "v1-legacy")
        self.assertTrue(payload["_railgo"]["cached"])

        conn = sqlite3.connect(self.db_path)
        try:
            raw = conn.execute(
                "SELECT data FROM path_cache WHERE id='G1'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertNotIn("_railgo", json.loads(raw))

    def test_corrupt_json_is_treated_as_cache_miss(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO path_cache (id,data) VALUES (?,?)", ("G1", "{broken")
        )
        conn.commit()
        conn.close()

        self.assertIsNone(self.store.get_path("G1"))
        self.assertIn("read path_cache:G1", self.store.last_error)

    def test_close_allows_connection_to_reopen(self):
        self.store.save_path("G1", {"rundays": ["20260715"]})
        self.store.close()
        self.assertIsNotNone(self.store.get_path("G1"))

    def test_concurrent_writes_wait_instead_of_locking_database(self):
        def write(index):
            return self.store.save_path(
                f"G{index}", {"rundays": ["20260715"], "timetable": []}
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, range(10)))
        self.assertTrue(all(results))


if __name__ == "__main__":
    unittest.main()
