"""
test_api_client.py — unit tests covering the 5 most likely failure points.

Run: python -m unittest test_api_client.py -v
"""

import json
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

import requests

sys.path.insert(0, ".")
from api_client import APIClient, APIError


# ── helpers ───────────────────────────────────────────────────────────────────

def make_client(**overrides):
    defaults = dict(
        base_url        = "https://api.test.com",
        api_key         = "test-key",
        api_key_env     = "API_KEY",
        rate_delay      = 0.5,
        max_retries     = 0,
        page_size       = 3,
        timeout         = 5,
        pagination_mode = "cursor",
        data_key        = "data",
        cursor_key      = "next_cursor",
        total_key       = "total",
        offset_param    = "offset",
        limit_param     = "limit",
    )
    defaults.update(overrides)
    client = APIClient(**defaults)
    client.session = MagicMock()
    return client


def mock_response(status: int, body):
    """Build a fake requests response."""
    r = MagicMock()
    r.status_code = status
    if isinstance(body, bytes):
        r.content = body
        r.json.side_effect = ValueError("not JSON")
    else:
        r.content = json.dumps(body).encode("utf-8")
        r.json.return_value = body
    return r


# ── 1. Rate limiting ──────────────────────────────────────────────────────────

class TestRateLimiting(unittest.TestCase):

    def test_first_call_does_not_sleep(self):
        client = make_client(rate_delay=0.5)
        client.session.request.return_value = mock_response(200, {"id": 1})

        with patch("api_client.time.sleep") as mock_sleep:
            # _last_call starts at 0.0; monotonic() will be >> 0.5 already
            client.call("GET", "/posts/1")
            mock_sleep.assert_not_called()

    def test_second_call_within_window_sleeps_remaining_gap(self):
        client = make_client(rate_delay=0.5)
        client.session.request.return_value = mock_response(200, {"id": 1})

        now = time.monotonic()
        client._last_call = now  # pretend a call just happened

        with patch("api_client.time.monotonic", side_effect=[now + 0.1, now + 0.1]):
            with patch("api_client.time.sleep") as mock_sleep:
                client._throttle()
                # elapsed = 0.1s, rate_delay = 0.5s → should sleep ~0.4s
                mock_sleep.assert_called_once()
                slept = mock_sleep.call_args[0][0]
                self.assertAlmostEqual(slept, 0.4, places=5)

    def test_call_after_full_delay_does_not_sleep(self):
        client = make_client(rate_delay=0.5)
        client.session.request.return_value = mock_response(200, {"id": 1})

        client._last_call = time.monotonic() - 1.0  # 1s ago, > rate_delay

        with patch("api_client.time.sleep") as mock_sleep:
            client.call("GET", "/posts/1")
            mock_sleep.assert_not_called()


# ── 2. Params passthrough ─────────────────────────────────────────────────────

class TestUrlEncoding(unittest.TestCase):

    def _captured_params(self, client, params):
        client.session.request.return_value = mock_response(200, {"ok": True})
        with patch("api_client.time.sleep"):
            client.call("GET", "/search", params=params)
        return client.session.request.call_args[1].get("params")

    def test_spaces_are_passed_intact(self):
        client = make_client()
        params = self._captured_params(client, {"q": "hello world"})
        self.assertEqual(params["q"], "hello world")

    def test_ampersand_in_value_is_passed_intact(self):
        client = make_client()
        params = self._captured_params(client, {"type": "a&b"})
        self.assertEqual(params["type"], "a&b")

    def test_multiple_params_are_passed_intact(self):
        client = make_client()
        params = self._captured_params(client, {"status": "open", "page": 1})
        self.assertEqual(params["status"], "open")
        self.assertEqual(params["page"], 1)


# ── 3. HTTP error messages ────────────────────────────────────────────────────

class TestHttpErrors(unittest.TestCase):

    def _call(self, client, status, body=None):
        client.session.request.return_value = mock_response(status, body or {"error": "x"})
        with patch("api_client.time.sleep"):
            client.call("GET", "/resource")

    def test_401_raises_with_auth_message(self):
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            self._call(client, 401)
        self.assertIn("Authentication failed", str(ctx.exception))
        self.assertIn("API_KEY", str(ctx.exception))
        self.assertEqual(ctx.exception.status, 401)

    def test_403_raises_with_auth_message(self):
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            self._call(client, 403)
        self.assertIn("Authentication failed", str(ctx.exception))

    def test_404_raises_with_not_found_message(self):
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            self._call(client, 404)
        self.assertIn("not found", str(ctx.exception).lower())
        self.assertEqual(ctx.exception.status, 404)

    def test_500_raises_with_server_error_message(self):
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            self._call(client, 500)
        self.assertIn("Server error", str(ctx.exception))
        self.assertEqual(ctx.exception.status, 500)

    def test_422_raises_with_client_error_message(self):
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            self._call(client, 422)
        self.assertIn("Client error", str(ctx.exception))

    def test_network_error_raises_api_error(self):
        client = make_client()
        client.session.request.side_effect = requests.ConnectionError("connection refused")
        with patch("api_client.time.sleep"):
            with self.assertRaises(APIError) as ctx:
                client.call("GET", "/resource")
        self.assertIsNone(ctx.exception.status)
        self.assertIn("ConnectionError", str(ctx.exception))

    def test_200_does_not_raise(self):
        client = make_client()
        client.session.request.return_value = mock_response(200, {"id": 1})
        with patch("api_client.time.sleep"):
            result = client.call("GET", "/posts/1")
        self.assertEqual(result["id"], 1)

    def test_204_returns_empty_dict(self):
        client = make_client()
        r = MagicMock()
        r.status_code = 204
        r.content     = b""
        client.session.request.return_value = r
        with patch("api_client.time.sleep"):
            result = client.call("DELETE", "/posts/1")
        self.assertEqual(result, {})


# ── 4. Pagination stop conditions ─────────────────────────────────────────────

class TestPagination(unittest.TestCase):

    # cursor mode ──────────────────────────────────────────────────────────────

    def test_cursor_stops_when_no_cursor_returned(self):
        client = make_client(pagination_mode="cursor", page_size=2)
        responses = [
            mock_response(200, {"data": [1, 2], "next_cursor": "abc"}),
            mock_response(200, {"data": [3, 4]}),          # no cursor → stop
        ]
        client.session.request.side_effect = responses

        with patch("api_client.time.sleep"):
            pages = list(client.paginate("/items"))

        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0], [1, 2])
        self.assertEqual(pages[1], [3, 4])

    def test_cursor_stops_on_empty_data(self):
        client = make_client(pagination_mode="cursor", page_size=2)
        responses = [
            mock_response(200, {"data": [1, 2], "next_cursor": "abc"}),
            mock_response(200, {"data": []}),               # empty → stop
        ]
        client.session.request.side_effect = responses

        with patch("api_client.time.sleep"):
            pages = list(client.paginate("/items"))

        self.assertEqual(len(pages), 1)

    def test_cursor_sends_cursor_param_on_second_call(self):
        client = make_client(pagination_mode="cursor", page_size=2)
        responses = [
            mock_response(200, {"data": [1, 2], "next_cursor": "tok123"}),
            mock_response(200, {"data": [3]}),
        ]
        client.session.request.side_effect = responses

        with patch("api_client.time.sleep"):
            list(client.paginate("/items"))

        second_call_params = client.session.request.call_args_list[1][1]["params"]
        self.assertEqual(second_call_params.get("cursor"), "tok123")

    # offset mode ─────────────────────────────────────────────────────────────

    def test_offset_stops_when_fewer_items_than_page_size(self):
        client = make_client(pagination_mode="offset", page_size=3)
        responses = [
            mock_response(200, [1, 2, 3]),   # full page
            mock_response(200, [4, 5]),      # partial → stop
        ]
        client.session.request.side_effect = responses

        with patch("api_client.time.sleep"):
            pages = list(client.paginate("/items", mode="offset"))

        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[1], [4, 5])

    def test_offset_stops_on_empty_response(self):
        client = make_client(pagination_mode="offset", page_size=3)
        responses = [
            mock_response(200, [1, 2, 3]),
            mock_response(200, []),          # empty → stop
        ]
        client.session.request.side_effect = responses

        with patch("api_client.time.sleep"):
            pages = list(client.paginate("/items", mode="offset"))

        self.assertEqual(len(pages), 1)

    def test_offset_stops_when_total_reached(self):
        client = make_client(pagination_mode="offset", page_size=3)
        responses = [
            mock_response(200, {"data": [1, 2, 3], "total": 4}),
            mock_response(200, {"data": [4], "total": 4}),
        ]
        client.session.request.side_effect = responses

        with patch("api_client.time.sleep"):
            pages = list(client.paginate("/items", mode="offset"))

        self.assertEqual(len(pages), 2)

    # page-number mode ────────────────────────────────────────────────────────

    def test_page_stops_on_partial_last_page(self):
        client = make_client(pagination_mode="page", page_size=3)
        responses = [
            mock_response(200, [1, 2, 3]),
            mock_response(200, [4]),         # partial → stop
        ]
        client.session.request.side_effect = responses

        with patch("api_client.time.sleep"):
            pages = list(client.paginate("/items", mode="page"))

        self.assertEqual(len(pages), 2)

    def test_page_stops_on_empty_response(self):
        client = make_client(pagination_mode="page", page_size=3)
        responses = [
            mock_response(200, [1, 2, 3]),
            mock_response(200, []),
        ]
        client.session.request.side_effect = responses

        with patch("api_client.time.sleep"):
            pages = list(client.paginate("/items", mode="page"))

        self.assertEqual(len(pages), 1)

    def test_invalid_mode_raises_value_error(self):
        client = make_client()
        with self.assertRaises(ValueError) as ctx:
            list(client.paginate("/items", mode="magic"))
        self.assertIn("Valid values", str(ctx.exception))


# ── 5. JSON parse failure ─────────────────────────────────────────────────────

class TestJsonParseFailure(unittest.TestCase):

    def test_non_json_raises_api_error_with_clear_message(self):
        client = make_client()
        client.session.request.return_value = mock_response(200, b"<html>Not JSON</html>")

        with patch("api_client.time.sleep"):
            with self.assertRaises(APIError) as ctx:
                client.call("GET", "/posts")

        self.assertIn("not valid JSON", str(ctx.exception))
        self.assertIn("Content-Type", str(ctx.exception))

    def test_non_json_error_includes_response_preview(self):
        client = make_client()
        client.session.request.return_value = mock_response(200, b"Service Unavailable")

        with patch("api_client.time.sleep"):
            with self.assertRaises(APIError) as ctx:
                client.call("GET", "/posts")

        self.assertIn("Service Unavailable", str(ctx.exception))

    def test_truncated_unicode_raises_api_error(self):
        client = make_client()
        client.session.request.return_value = mock_response(200, b"\xff\xfe invalid utf-8")

        with patch("api_client.time.sleep"):
            with self.assertRaises(APIError):
                client.call("GET", "/posts")


# ── 6. max_rows limit ─────────────────────────────────────────────────────────

class TestMaxRows(unittest.TestCase):

    def test_stops_exactly_at_max_rows(self):
        client = make_client(page_size=3)
        client.session.request.side_effect = [
            mock_response(200, {"data": [1, 2, 3], "next_cursor": "a"}),
            mock_response(200, {"data": [4, 5, 6], "next_cursor": "b"}),
            mock_response(200, {"data": [7, 8, 9]}),
        ]
        with patch("api_client.time.sleep"):
            pages = list(client.paginate("/items", max_rows=5))
        self.assertEqual(pages, [[1, 2, 3], [4, 5]])
        self.assertEqual(client.session.request.call_count, 2)

    def test_max_rows_larger_than_total_returns_all(self):
        client = make_client(page_size=3)
        client.session.request.side_effect = [
            mock_response(200, {"data": [1, 2, 3], "next_cursor": "a"}),
            mock_response(200, {"data": [4, 5]}),
        ]
        with patch("api_client.time.sleep"):
            pages = list(client.paginate("/items", max_rows=100))
        self.assertEqual(pages, [[1, 2, 3], [4, 5]])

    def test_max_rows_none_returns_all(self):
        client = make_client(page_size=3)
        client.session.request.side_effect = [
            mock_response(200, {"data": [1, 2, 3], "next_cursor": "a"}),
            mock_response(200, {"data": [4, 5]}),
        ]
        with patch("api_client.time.sleep"):
            pages = list(client.paginate("/items", max_rows=None))
        self.assertEqual(pages, [[1, 2, 3], [4, 5]])

    def test_default_max_rows_from_init(self):
        client = make_client(page_size=3, max_rows=4)
        client.session.request.side_effect = [
            mock_response(200, {"data": [1, 2, 3], "next_cursor": "a"}),
            mock_response(200, {"data": [4, 5, 6], "next_cursor": "b"}),
        ]
        with patch("api_client.time.sleep"):
            pages = list(client.paginate("/items"))
        self.assertEqual(pages, [[1, 2, 3], [4]])

    def test_paginate_max_rows_overrides_init(self):
        client = make_client(page_size=3, max_rows=99)
        client.session.request.side_effect = [
            mock_response(200, {"data": [1, 2, 3], "next_cursor": "a"}),
            mock_response(200, {"data": [4, 5]}),
        ]
        with patch("api_client.time.sleep"):
            pages = list(client.paginate("/items", max_rows=2))
        self.assertEqual(pages, [[1, 2]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
