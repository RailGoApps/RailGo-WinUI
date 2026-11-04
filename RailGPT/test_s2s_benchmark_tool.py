import unittest

from tools.rail.s2s_query import pretty_format_s2s_benchmark


def make_running_days():
    return ["20260324"] + [f"D{i:02d}" for i in range(79)]


class S2SBenchmarkToolTest(unittest.TestCase):
    def test_pretty_format_s2s_benchmark_rates_routes_with_intermediate_stops(self):
        payload = {
            "payload": {
                "trains": [
                    {
                        "number": "G1808",
                        "fromDepart": "08:45",
                        "toArrive": "10:02",
                        "dayDiff": 0,
                        "bureauName": "郑州局",
                        "rundays": make_running_days(),
                        "timetable": [
                            {"station": "南京南"},
                            {"station": "滁州"},
                            {"station": "徐州东"},
                        ],
                        "fromStationTelecode": "NKH",
                        "toStationTelecode": "UUH",
                    },
                    {
                        "number": "G84",
                        "fromDepart": "20:15",
                        "toArrive": "21:21",
                        "dayDiff": 0,
                        "bureauName": "郑州局",
                        "rundays": make_running_days(),
                        "timetable": [
                            {"station": "南京南"},
                            {"station": "蚌埠南"},
                            {"station": "徐州东"},
                        ],
                        "fromStationTelecode": "NKH",
                        "toStationTelecode": "UUH",
                    },
                ]
            }
        }

        pretty = pretty_format_s2s_benchmark(payload, "20260324")

        self.assertIn("G84", pretty)
        self.assertNotIn("G1808", pretty)
        self.assertNotIn("No nonstop benchmark", pretty)
        rated = payload["payload"]["trains"][1]
        self.assertEqual(rated["_benchmark_score"], 100)
        self.assertTrue(rated["_benchmark_tier"])


if __name__ == "__main__":
    unittest.main()
