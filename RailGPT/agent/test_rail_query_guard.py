import unittest

from agent.rail_query_guard import clean_station_token, normalize_route_id


class RailQueryGuardTest(unittest.TestCase):
    def test_clean_station_token_strips_fastest_tail(self):
        self.assertEqual(clean_station_token("徐州东最快的"), "徐州东")
        self.assertEqual(clean_station_token("南京南有什么最快的车", is_departure=True), "南京南")

    def test_normalize_route_id_handles_plain_chinese_connector(self):
        self.assertEqual(normalize_route_id("南京南到徐州东最快的"), "南京南-徐州东")

    def test_normalize_route_id_handles_open_route_phrase(self):
        self.assertEqual(normalize_route_id("从福州开往北京南有什么车"), "福州-北京南")


    def test_clean_station_token_strips_weekday_prefix(self):
        self.assertEqual(clean_station_token("这周天南京南", is_departure=True), "南京南")

    def test_normalize_route_id_handles_weekday_prefixed_query(self):
        self.assertEqual(normalize_route_id("这周天南京南到徐州东还有余票吗"), "南京南-徐州东")


if __name__ == "__main__":
    unittest.main()
