import unittest

from agent.pending_utils import compose_pending_question, normalize_pending_payload


class PendingUtilsTest(unittest.TestCase):
    def test_dep_missing_with_known_arr_is_humanized(self):
        payload = normalize_pending_payload(
            question="请补充一下",
            slot=["dep"],
            context={"arr": "上海"},
        )

        self.assertIn("上海", payload["question"])
        self.assertIn("从哪里出发", payload["question"])

    def test_arr_missing_with_known_dep_is_humanized(self):
        payload = normalize_pending_payload(
            question="我还需要你补充一下信息才能继续",
            slot=["arr"],
            context={"dep": "南京南"},
        )

        self.assertIn("南京南", payload["question"])
        self.assertIn("到哪一站", payload["question"])

    def test_train_and_station_pending_is_humanized(self):
        question = compose_pending_question(
            slot=["train_no"],
            context={"station": "南京南"},
        )

        self.assertIn("南京南", question)
        self.assertIn("车次号", question)

    def test_hub_pending_mentions_route_context(self):
        question = compose_pending_question(
            slot=["hub"],
            context={"route": "南京南-上海"},
        )

        self.assertIn("南京南到上海", question)
        self.assertIn("中转站", question)

    def test_date_pending_mentions_existing_route(self):
        question = compose_pending_question(
            slot=["date"],
            context={"route": "南京南-上海"},
        )

        self.assertIn("南京南-上海", question)
        self.assertIn("日期", question)


if __name__ == "__main__":
    unittest.main()
