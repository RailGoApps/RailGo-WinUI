import unittest

from agent.fast_tool_views import build_fast_candidates, build_fast_views
from tools.rail.s2s_query import pretty_format_s2s_benchmark


NANJING_SOUTH = "\u5357\u4eac\u5357"
XUZHOU_EAST = "\u5f90\u5dde\u4e1c"
JINAN_WEST = "\u6d4e\u5357\u897f"
CHUZHOU = "\u6ec1\u5dde"


def make_rundays(date_text: str, count: int = 80):
    days = [date_text]
    days.extend(f"D{i:02d}" for i in range(max(0, count - 1)))
    return days


class FastToolViewsTest(unittest.TestCase):
    def test_station_board_view_preserves_subject_and_snapshot_semantics(self):
        query = {
            "domain": "railway",
            "object": "station_board",
            "id": "NKH|departure",
            "grounded_slots": {
                "station": NANJING_SOUTH,
                "direction": "departure",
            },
            "freshness": {
                "fetched_at": "2026-07-31T11:46:04+08:00",
                "age_seconds": 0,
            },
        }
        rows = [
            {
                "trainNum": "G1948",
                "time": "11:41",
                "bigScreenStatus": "检票",
                "timeDelay": 0,
                "trainStartStation": "上海虹桥",
                "trainEndStation": "洛阳龙门",
                "bigScreenPort": ["A20", "A21"],
            }
        ]

        views = build_fast_views(query, rows)
        overview = views[0]["text"]

        self.assertIn("TOOL_EXECUTION_STATUS: completed", overview)
        self.assertIn(f"BOARD_STATION: {NANJING_SOUTH}", overview)
        self.assertIn("BOARD_DIRECTION: departure", overview)
        self.assertIn("OBSERVED_AT: 2026-07-31T11:46:04+08:00", overview)
        self.assertIn("SNAPSHOT_BOUNDARY", overview)
        self.assertIn("SUBJECT_BOUNDARY", overview)

    def test_s2s_views_split_large_candidate_pool(self):
        query = {
            "domain": "railway",
            "object": "s2s_benchmark",
            "id": f"{NANJING_SOUTH}-{XUZHOU_EAST}",
            "date": "20260324",
        }
        trains = []
        for train_no, dep, arr in [
            ("G87", "07:00", "09:39"),
            ("G89", "07:12", "09:52"),
            ("G91", "07:18", "09:58"),
            ("G93", "07:25", "10:06"),
            ("G95", "07:31", "10:14"),
            ("G97", "07:36", "10:20"),
            ("G99", "07:41", "10:26"),
            ("G101", "07:48", "10:35"),
        ]:
            trains.append(
                {
                    "number": train_no,
                    "fromDepart": dep,
                    "toArrive": arr,
                    "dayDiff": 0,
                    "bureauName": "\u4e0a\u6d77\u5c40",
                    "car": "CR400AF",
                    "rundays": make_rundays("20260324"),
                    "timetable": [{"station": NANJING_SOUTH}, {"station": XUZHOU_EAST}],
                    "fromPos": 0,
                    "toPos": 1,
                }
            )

        raw_payload = {
            "payload": {"trains": trains},
            "status": {"cert_valid": True, "need_refresh": False},
        }

        views = build_fast_views(query, raw_payload)

        self.assertEqual(views[0]["view_type"], "s2s_overview")
        batch_views = [view for view in views if view["view_type"] == "s2s_candidate_batch"]
        self.assertEqual(len(batch_views), 2)
        self.assertIn("G87", batch_views[0]["text"])
        self.assertIn("G101", batch_views[1]["text"])

    def test_path_views_split_timetable_into_batches(self):
        query = {
            "domain": "railway",
            "object": "path_detail",
            "id": "G87",
            "date": "2026-03-24",
        }
        raw_payload = {
            "numberFull": ["G87"],
            "bureauName": "\u4e0a\u6d77\u5c40",
            "car": "CR400AF",
            "_status": {"running_today": True, "cert_valid": True, "need_refresh": False},
            "rundays": make_rundays("20260324"),
            "timetable": [
                {
                    "station": f"\u8f66\u7ad9{i:02d}",
                    "stationTelecode": f"S{i:02d}",
                    "arrive": f"{6 + i // 2:02d}:{(i * 3) % 60:02d}",
                    "depart": f"{6 + i // 2:02d}:{(i * 3 + 2) % 60:02d}",
                    "stopTime": 2 if i not in (0, 13) else 0,
                    "distance": i * 50,
                }
                for i in range(14)
            ],
        }

        views = build_fast_views(query, raw_payload)

        self.assertEqual(views[0]["view_type"], "path_overview")
        batch_views = [view for view in views if view["view_type"] == "path_stop_batch"]
        self.assertEqual(len(batch_views), 2)
        self.assertIn("STOP_COUNT: 14", views[0]["text"])
        self.assertIn("\u8f66\u7ad913", batch_views[1]["text"])

    def test_stopcheck_views_keep_per_train_scan_results(self):
        query = {
            "domain": "railway",
            "object": "path_stopcheck",
            "id": f"G87,G89|{NANJING_SOUTH},{JINAN_WEST}",
            "date": "2026-03-24",
        }
        raw_payload = {
            "scan": [
                {
                    "train": "G87",
                    "checks": [
                        {"station": NANJING_SOUTH, "result": "SKIP"},
                        {
                            "station": JINAN_WEST,
                            "result": "STOP",
                            "stopTime": 2,
                            "arrive": "08:10",
                            "depart": "08:12",
                        },
                    ],
                },
                {
                    "train": "G89",
                    "checks": [
                        {"station": NANJING_SOUTH, "result": "PASS", "arrive": "07:30", "depart": "07:30"},
                    ],
                },
            ]
        }

        views = build_fast_views(query, raw_payload)

        self.assertEqual(views[0]["view_type"], "stopcheck_overview")
        self.assertIn("SKIP_RESULT_COUNT: 1", views[0]["text"])
        self.assertTrue(any("G87 |" in view["text"] for view in views))
        self.assertTrue(any("SKIP" in view["text"] for view in views))
        self.assertTrue(any("STOP(" in view["text"] for view in views))

    def test_benchmark_views_and_candidates_use_tool_ratings(self):
        query = {
            "domain": "railway",
            "object": "s2s_benchmark",
            "id": f"{NANJING_SOUTH}-{XUZHOU_EAST}",
            "date": "20260324",
        }
        raw_payload = {
            "payload": {
                "trains": [
                    {
                        "number": "G1808",
                        "fromDepart": "08:45",
                        "toArrive": "10:02",
                        "dayDiff": 0,
                        "bureauName": "\u90d1\u5dde\u5c40",
                        "car": "CR400BF-A",
                        "rundays": make_rundays("20260324"),
                        "timetable": [
                            {"station": NANJING_SOUTH},
                            {"station": CHUZHOU},
                            {"station": XUZHOU_EAST},
                        ],
                        "fromPos": 0,
                        "toPos": 2,
                    },
                    {
                        "number": "G84",
                        "fromDepart": "20:15",
                        "toArrive": "21:21",
                        "dayDiff": 0,
                        "bureauName": "\u90d1\u5dde\u5c40",
                        "car": "CR400BF-A",
                        "rundays": make_rundays("20260324"),
                        "timetable": [
                            {"station": NANJING_SOUTH},
                            {"station": "\u868c\u57e0\u5357"},
                            {"station": XUZHOU_EAST},
                        ],
                        "fromPos": 0,
                        "toPos": 2,
                    },
                ]
            },
            "status": {"cert_valid": True, "need_refresh": False},
        }

        pretty_format_s2s_benchmark(raw_payload, "20260324")

        views = build_fast_views(query, raw_payload)
        candidates = build_fast_candidates(query, raw_payload)

        overview = views[0]["text"]
        self.assertIn("BENCHMARK_MODE: tool_scored", overview)
        self.assertNotIn("G1808", "".join(view["text"] for view in views))
        self.assertEqual([item["label"] for item in candidates], ["G84"])


if __name__ == "__main__":
    unittest.main()
