import unittest

from agent.fast_tool_views import build_fast_views
from tools.rail.transfer_12306 import pretty_transfer


class TransferToolResilienceTest(unittest.TestCase):
    def test_pretty_transfer_handles_string_data_payload(self):
        result = {
            "query": "南京-福州|上饶|2026-04-04",
            "source": "cache",
            "payload": {
                "httpstatus": 200,
                "data": "",
                "status": True,
                "errorMsg": "没有查询到中转方案",
            },
        }

        pretty = pretty_transfer(result)

        self.assertIn("没有查询到中转方案", pretty)

    def test_fast_transfer_views_handle_string_data_payload(self):
        query = {
            "domain": "railway",
            "object": "transfer_12306",
            "id": "南京-福州|上饶",
            "date": "2026-04-04",
        }
        raw_payload = {
            "query": "南京-福州|上饶|2026-04-04",
            "source": "cache",
            "payload": {
                "httpstatus": 200,
                "data": "",
                "status": True,
                "errorMsg": "没有查询到中转方案",
            },
        }

        views = build_fast_views(query, raw_payload=raw_payload)

        self.assertTrue(views)
        self.assertEqual(views[0]["view_type"], "transfer_info")
        self.assertIn("没有查询到中转方案", views[0]["text"])
