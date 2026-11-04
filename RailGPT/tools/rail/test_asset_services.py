import os
import struct
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from tools.rail.coach_assets import CoachAssetService
from tools.rail.rail_store import RailStore
from tools.rail.railgo_client import RailGoContractError, RailGoTemporaryError
from tools.rail.route_assets import RouteAssetService, gcj02_to_wgs84, parse_map_payload


def coach_envelope(car_code="CR400BF-A-5059", capacity="1231人"):
    return {
        "data": {
            "carCode": car_code,
            "carType": "复兴号",
            "trainStyle": "CR400BF-A",
            "carInfo": [
                {"pictureName": "列车定员", "pictureValue": capacity, "pictureUrl": "https://res.railgo.zenglingkun.cn/ignored.png"},
                {"pictureName": "最高速度", "pictureValue": "350km/h"},
            ],
            "carPic": "https://res.railgo.zenglingkun.cn/whole.png",
            "coachPicList": [
                {"pictureName": "08车 二等座 90", "pictureValue": "90人", "pictureUrl": "https://res.railgo.zenglingkun.cn/08.png"}
            ],
            "coachDetailPicList": [
                {"pictureName": "商务座#鱼骨式", "pictureUrl": "https://res.railgo.zenglingkun.cn/business.jpg"}
            ],
        },
        "_railgo": {"provider": "RailGo", "api_version": "v2"},
    }


def map_envelope():
    return {
        "data": {
            "stations": [{"北京南": [116.378, 39.865]}, {"上海虹桥": [121.327, 31.200]}],
            "train": {
                "北京南-上海虹桥": {
                    "index": 1,
                    "line": [[116.378, 39.865], [118.0, 35.0], [999, 999], [121.327, 31.200]],
                }
            },
        },
        "_railgo": {"provider": "RailGo", "api_version": "v2"},
    }


class AssetServicesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = RailStore(str(Path(self.temp.name, "rail.db")))
        self.media_root = str(Path(self.temp.name, "media"))

    def tearDown(self):
        self.store.close_all()
        self.temp.cleanup()

    def test_coach_binding_hits_cache_within_24_hours_and_urls_stay_private(self):
        fetcher = Mock(return_value=coach_envelope())
        service = CoachAssetService(self.store, fetcher=fetcher, media_root=self.media_root)

        first = service.get_layout("G1")
        second = service.get_layout("G1")

        self.assertEqual(fetcher.call_count, 1)
        self.assertEqual(first["evidence"]["carCode"], "CR400BF-A-5059")
        self.assertEqual(second["cache_status"], "fresh")
        self.assertNotIn("pictureUrl", str(first["evidence"]))
        locator = self.store.get_coach_media_locator("CR400BF-A-5059", "coach", "08")
        self.assertIn("https://", locator["remote_url"])

    def test_immutable_coach_asset_rejects_structural_conflict(self):
        service = CoachAssetService(self.store, fetcher=Mock(return_value=coach_envelope()), media_root=self.media_root)
        service.get_layout("G1")
        original = self.store.get_coach_asset("CR400BF-A-5059")
        outcome = self.store.save_coach_asset(
            {
                "car_code": "CR400BF-A-5059", "car_type": "复兴号", "train_style": "CR400BF-A",
                "car_info": [{"pictureName": "列车定员", "pictureValue": "9999人"}],
                "coach_catalog": [], "detail_catalog": [], "structural_fingerprint": "different",
            }
        )
        self.assertEqual(outcome, "conflict")
        self.assertEqual(self.store.get_coach_asset("CR400BF-A-5059")["structural_fingerprint"], original["structural_fingerprint"])

    def test_only_temporary_failure_uses_recent_stale_binding(self):
        service = CoachAssetService(self.store, fetcher=Mock(return_value=coach_envelope()), media_root=self.media_root)
        service.get_layout("G1")
        old = datetime.now() - timedelta(days=2)
        self.store._get_conn().execute(
            "UPDATE coach_train_binding SET observed_at=?, expires_at=? WHERE train_no='G1'",
            (old.isoformat(timespec="seconds"), old.isoformat(timespec="seconds")),
        )
        self.store._get_conn().commit()
        service.fetcher = Mock(side_effect=RailGoTemporaryError("temporary"))
        self.assertEqual(service.get_layout("G1")["cache_status"], "stale")
        service.fetcher = Mock(side_effect=RailGoContractError("bad schema"))
        with self.assertRaises(RailGoContractError):
            service.get_layout("G1")

    def test_requested_image_is_downloaded_once_and_content_addressed(self):
        service = CoachAssetService(self.store, fetcher=Mock(return_value=coach_envelope()), media_root=self.media_root)
        service.get_layout("G1")
        png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", 4, 3) + b"\x08\x02\x00\x00\x00" + b"payload"
        response = Mock(status_code=200, content=png, headers={"Content-Type": "image/png"})
        with patch("tools.rail.coach_assets.http_get", return_value=response) as get:
            first = service.resolve_media("G1", "coach", "08")
            second = service.resolve_media("G1", "coach", "08")
        self.assertEqual(get.call_count, 1)
        self.assertEqual(first["asset_id"], second["asset_id"])
        self.assertTrue(Path(self.store.get_coach_media_by_hash(first["asset_id"])["local_path"]).is_file())

    def test_media_domain_allowlist_blocks_untrusted_url(self):
        service = CoachAssetService(self.store, fetcher=Mock(return_value=coach_envelope()), media_root=self.media_root)
        service.get_layout("G1")
        self.store._get_conn().execute(
            "UPDATE coach_media_locator SET remote_url='https://example.com/evil.png' "
            "WHERE car_code='CR400BF-A-5059' AND media_kind='coach' AND selector='08'"
        )
        self.store._get_conn().commit()
        with self.assertRaises(RailGoContractError):
            service.resolve_media("G1", "coach", "08")

    def test_route_geometry_is_cached_for_quarter_and_exposes_summary_only(self):
        fetcher = Mock(return_value=map_envelope())
        service = RouteAssetService(self.store, fetcher=fetcher)
        first = service.get_route("G1")
        second = service.get_route("G1")
        self.assertEqual(fetcher.call_count, 1)
        self.assertEqual(first["evidence"]["raw_point_count"], 3)
        self.assertNotIn("geojson", first["evidence"])
        self.assertEqual(second["cache_status"], "fresh")
        asset = self.store.get_route_asset(first["artifacts"][0]["asset_id"])
        self.assertEqual(asset["geojson_json"]["type"], "FeatureCollection")
        self.assertIn("<svg", asset["fallback_svg"])

    def test_gcj_conversion_and_bad_point_filtering(self):
        converted = gcj02_to_wgs84(116.4, 39.9)
        self.assertNotEqual(converted, (116.4, 39.9))
        parsed = parse_map_payload(map_envelope()["data"], "G1")
        self.assertEqual(parsed["summary"]["raw_point_count"], 3)
        self.assertGreater(parsed["summary"]["estimated_polyline_km"], 0)


if __name__ == "__main__":
    unittest.main()
