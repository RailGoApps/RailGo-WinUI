import unittest
from unittest.mock import patch

import requests

from utils.net_retry import RetryExhausted, truncated_binary_backoff


class NetRetryTest(unittest.TestCase):
    def test_plain_callable_is_supported(self):
        calls = []

        def request():
            calls.append("called")
            return "ok"

        self.assertEqual(truncated_binary_backoff(request), "ok")
        self.assertEqual(calls, ["called"])

    def test_attempt_keyword_is_passed(self):
        attempts = []

        def request(*, attempt):
            attempts.append(attempt)
            return attempt

        self.assertEqual(truncated_binary_backoff(request), 1)
        self.assertEqual(attempts, [1])

    def test_internal_type_error_does_not_execute_callable_twice(self):
        calls = []

        def request(*, attempt):
            calls.append(attempt)
            raise TypeError("contract bug inside request")

        with self.assertRaises(TypeError):
            truncated_binary_backoff(request)
        self.assertEqual(calls, [1])

    @patch("utils.net_retry.time.sleep")
    @patch("utils.net_retry.random.uniform", return_value=0)
    def test_transient_connection_failure_retries_to_limit(self, _uniform, _sleep):
        calls = []

        def request():
            calls.append("called")
            raise requests.ConnectionError("offline")

        with self.assertRaises(RetryExhausted):
            truncated_binary_backoff(request, max_attempts=3)
        self.assertEqual(len(calls), 3)

    @patch("utils.net_retry.time.sleep")
    @patch("utils.net_retry.random.uniform", return_value=0)
    def test_incomplete_chunked_response_is_retried(self, _uniform, _sleep):
        calls = []

        def request():
            calls.append("called")
            if len(calls) == 1:
                raise requests.exceptions.ChunkedEncodingError("incomplete chunked read")
            return "recovered"

        self.assertEqual(truncated_binary_backoff(request, max_attempts=2), "recovered")
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
