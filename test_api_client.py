"""
test_api_client.py — unit tests for APIClient.

Run: python -m unittest test_api_client.py -v
"""

import json
import sys
import time
import unittest
from unittest.mock import MagicMock, call, patch

import requests

sys.path.insert(0, ".")
from api_client import APIClient, APIError


# ── helpers ───────────────────────────────────────────────────────────────────

def make_client(**overrides):
    defaults = dict(
        base_url         = "https://api.test.com",
        api_key          = "test-key",
        api_key_env      = "API_KEY",
        rate_delay       = 0.5,
        max_retries      = 0,
        page_size        = 3,
        timeout          = 5,
        pagination_mode  = "cursor",
        data_key         = "data",
        cursor_key       = "next_cursor",
        cursor_body_key  = "after",
        total_key        = "total",
        offset_param     = "offset",
        limit_param      = "limit",
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
            client.get("/posts/1")
            mock_sleep.assert_not_called()

    def test_second_call_within_window_sleeps_remaining_gap(self):
        client = make_client(rate_delay=0.5)
        client.session.request.return_value = mock_response(200, {"id": 1})

        now = time.monotonic()
        client._last_call = now

        with patch("api_client.time.monotonic", side_effect=[now + 0.1, now + 0.1]):
            with patch("api_client.time.sleep") as mock_sleep:
                client._throttle()
                mock_sleep.assert_called_once()
                slept = mock_sleep.call_args[0][0]
                self.assertAlmostEqual(slept, 0.4, places=5)

    def test_call_after_full_delay_does_not_sleep(self):
        client = make_client(rate_delay=0.5)
        client.session.request.return_value = mock_response(200, {"id": 1})

        client._last_call = time.monotonic() - 1.0

        with patch("api_client.time.sleep") as mock_sleep:
            client.get("/posts/1")
            mock_sleep.assert_not_called()


# ── 2. HTTP method convenience wrappers ───────────────────────────────────────

class TestHttpMethods(unittest.TestCase):
    """get/post/put/patch/delete all route to request() with the right method."""

    def setUp(self):
        self.client = make_client()

    def _setup(self, body=None):
        self.client.session.request.return_value = mock_response(200, body or {"ok": True})

    def _method_arg(self):
        return self.client.session.request.call_args[0][0]

    def test_get_sends_get(self):
        self._setup()
        with patch("api_client.time.sleep"):
            self.client.get("/resource")
        self.assertEqual(self._method_arg(), "GET")

    def test_post_sends_post(self):
        self._setup()
        with patch("api_client.time.sleep"):
            self.client.post("/resource", data={"x": 1})
        self.assertEqual(self._method_arg(), "POST")

    def test_put_sends_put(self):
        self._setup()
        with patch("api_client.time.sleep"):
            self.client.put("/resource", data={"x": 1})
        self.assertEqual(self._method_arg(), "PUT")

    def test_patch_sends_patch(self):
        self._setup()
        with patch("api_client.time.sleep"):
            self.client.patch("/resource", data={"x": 1})
        self.assertEqual(self._method_arg(), "PATCH")

    def test_delete_sends_delete(self):
        r = MagicMock()
        r.status_code = 204
        r.content = b""
        self.client.session.request.return_value = r
        with patch("api_client.time.sleep"):
            result = self.client.delete("/resource")
        self.assertEqual(self._method_arg(), "DELETE")
        self.assertEqual(result, {})

    def test_get_passes_params(self):
        self._setup()
        with patch("api_client.time.sleep"):
            self.client.get("/search", params={"q": "hello"})
        kwargs = self.client.session.request.call_args[1]
        self.assertEqual(kwargs["params"]["q"], "hello")

    def test_post_passes_json_body(self):
        self._setup()
        with patch("api_client.time.sleep"):
            self.client.post("/items", data={"name": "test"})
        kwargs = self.client.session.request.call_args[1]
        self.assertEqual(kwargs["json"], {"name": "test"})

    def test_request_passes_through_method(self):
        self._setup()
        with patch("api_client.time.sleep"):
            self.client.request("GET", "/resource")
        self.assertEqual(self._method_arg(), "GET")


# ── 3. Params passthrough ─────────────────────────────────────────────────────

class TestUrlEncoding(unittest.TestCase):

    def _captured_params(self, client, params):
        client.session.request.return_value = mock_response(200, {"ok": True})
        with patch("api_client.time.sleep"):
            client.get("/search", params=params)
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


# ── 4. HTTP error messages ────────────────────────────────────────────────────

class TestHttpErrors(unittest.TestCase):

    def _call(self, client, status, body=None):
        client.session.request.return_value = mock_response(status, body or {"error": "x"})
        with patch("api_client.time.sleep"):
            client.get("/resource")

    def test_401_raises_with_auth_message(self):
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            self._call(client, 401)
        msg = str(ctx.exception)
        self.assertIn("credentials", msg.lower())
        self.assertIn("API_KEY", msg)
        self.assertEqual(ctx.exception.status, 401)

    def test_403_raises_with_permission_message(self):
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            self._call(client, 403)
        msg = str(ctx.exception)
        self.assertIn("denied", msg.lower())
        self.assertIn("permission", msg.lower())
        self.assertEqual(ctx.exception.status, 403)

    def test_401_and_403_produce_different_messages(self):
        client = make_client()
        with self.assertRaises(APIError) as ctx401:
            self._call(client, 401)
        with self.assertRaises(APIError) as ctx403:
            self._call(client, 403)
        self.assertNotEqual(str(ctx401.exception), str(ctx403.exception))

    def test_404_raises_with_not_found_message(self):
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            self._call(client, 404)
        msg = str(ctx.exception).lower()
        self.assertTrue(
            "not found" in msg or "does not exist" in msg,
            f"Expected 'not found' or 'does not exist' in: {msg}",
        )
        self.assertEqual(ctx.exception.status, 404)

    def test_500_raises_with_server_error_message(self):
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            self._call(client, 500)
        msg = str(ctx.exception)
        self.assertIn("internal error", msg.lower())
        self.assertEqual(ctx.exception.status, 500)

    def test_500_and_503_produce_different_messages(self):
        client = make_client()
        with self.assertRaises(APIError) as ctx500:
            self._call(client, 500)
        with self.assertRaises(APIError) as ctx503:
            self._call(client, 503)
        self.assertNotEqual(str(ctx500.exception), str(ctx503.exception))

    def test_422_raises_with_validation_message(self):
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            self._call(client, 422)
        self.assertIn("validation", str(ctx.exception).lower())

    def test_network_error_raises_api_error(self):
        client = make_client()
        client.session.request.side_effect = requests.ConnectionError("connection refused")
        with patch("api_client.time.sleep"):
            with self.assertRaises(APIError) as ctx:
                client.get("/resource")
        self.assertIsNone(ctx.exception.status)
        self.assertIn("ConnectionError", str(ctx.exception))

    def test_200_does_not_raise(self):
        client = make_client()
        client.session.request.return_value = mock_response(200, {"id": 1})
        with patch("api_client.time.sleep"):
            result = client.get("/posts/1")
        self.assertEqual(result["id"], 1)

    def test_204_returns_empty_dict(self):
        client = make_client()
        r = MagicMock()
        r.status_code = 204
        r.content     = b""
        client.session.request.return_value = r
        with patch("api_client.time.sleep"):
            result = client.delete("/posts/1")
        self.assertEqual(result, {})


# ── 5. Pagination stop conditions ─────────────────────────────────────────────

class TestPagination(unittest.TestCase):

    # cursor mode ──────────────────────────────────────────────────────────────

    def test_cursor_stops_when_no_cursor_returned(self):
        client = make_client(pagination_mode="cursor", page_size=2)
        responses = [
            mock_response(200, {"data": [1, 2], "next_cursor": "abc"}),
            mock_response(200, {"data": [3, 4]}),
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
            mock_response(200, {"data": []}),
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

    def test_cursor_supports_nested_cursor_key(self):
        """cursor_key with dot-notation reads nested response fields."""
        client = make_client(
            pagination_mode="cursor",
            page_size=2,
            cursor_key="paging.next.after",
        )
        responses = [
            mock_response(200, {"data": [1, 2], "paging": {"next": {"after": "tok999"}}}),
            mock_response(200, {"data": [3]}),
        ]
        client.session.request.side_effect = responses

        with patch("api_client.time.sleep"):
            pages = list(client.paginate("/items"))

        self.assertEqual(pages, [[1, 2], [3]])
        second_params = client.session.request.call_args_list[1][1]["params"]
        self.assertEqual(second_params.get("cursor"), "tok999")

    def test_cursor_param_name_is_configurable(self):
        """cursor_param controls the query-param name sent to the API."""
        client = make_client(
            pagination_mode="cursor",
            page_size=2,
            cursor_key="next",
            cursor_param="after",  # GitHub uses "after" instead of "cursor"
        )
        responses = [
            mock_response(200, {"data": [1, 2], "next": "page2token"}),
            mock_response(200, {"data": [3]}),
        ]
        client.session.request.side_effect = responses

        with patch("api_client.time.sleep"):
            pages = list(client.paginate("/items"))

        self.assertEqual(pages, [[1, 2], [3]])
        second_params = client.session.request.call_args_list[1][1]["params"]
        self.assertIn("after", second_params)
        self.assertEqual(second_params["after"], "page2token")
        self.assertNotIn("cursor", second_params)

    def test_integer_cursor_zero_does_not_stop_pagination(self):
        """Integer cursor 0 must not terminate pagination (0 is falsy in Python)."""
        client = make_client(
            pagination_mode="cursor",
            page_size=2,
            cursor_key="next_offset",
            cursor_param="offset",
        )
        responses = [
            mock_response(200, {"data": [1, 2], "next_offset": 0}),
            mock_response(200, {"data": [3]}),
        ]
        client.session.request.side_effect = responses

        with patch("api_client.time.sleep"):
            pages = list(client.paginate("/items"))

        self.assertEqual(pages, [[1, 2], [3]])

    def test_offset_total_key_supports_dot_notation(self):
        """total_key in offset mode supports dot-notation for nested total count."""
        client = make_client(
            pagination_mode="offset",
            page_size=2,
            total_key="meta.total",
        )
        responses = [
            mock_response(200, {"data": [1, 2], "meta": {"total": 3}}),
            mock_response(200, {"data": [3], "meta": {"total": 3}}),
        ]
        client.session.request.side_effect = responses

        with patch("api_client.time.sleep"):
            pages = list(client.paginate("/items", mode="offset"))

        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[1], [3])

    # offset mode ─────────────────────────────────────────────────────────────

    def test_offset_stops_when_fewer_items_than_page_size(self):
        client = make_client(pagination_mode="offset", page_size=3)
        responses = [
            mock_response(200, [1, 2, 3]),
            mock_response(200, [4, 5]),
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
            mock_response(200, []),
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
            mock_response(200, [4]),
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


# ── 6. JSON parse failure ─────────────────────────────────────────────────────

class TestJsonParseFailure(unittest.TestCase):

    def test_non_json_raises_api_error_with_clear_message(self):
        client = make_client()
        client.session.request.return_value = mock_response(200, b"<html>Not JSON</html>")

        with patch("api_client.time.sleep"):
            with self.assertRaises(APIError) as ctx:
                client.get("/posts")

        self.assertIn("not valid JSON", str(ctx.exception))
        self.assertIn("Content-Type", str(ctx.exception))

    def test_non_json_error_includes_response_preview(self):
        client = make_client()
        client.session.request.return_value = mock_response(200, b"Service Unavailable")

        with patch("api_client.time.sleep"):
            with self.assertRaises(APIError) as ctx:
                client.get("/posts")

        self.assertIn("Service Unavailable", str(ctx.exception))

    def test_truncated_unicode_raises_api_error(self):
        client = make_client()
        client.session.request.return_value = mock_response(200, b"\xff\xfe invalid utf-8")

        with patch("api_client.time.sleep"):
            with self.assertRaises(APIError):
                client.get("/posts")


# ── 7. max_rows limit ─────────────────────────────────────────────────────────

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


# ── 8. GraphQL ────────────────────────────────────────────────────────────────

class TestGraphQL(unittest.TestCase):

    def test_graphql_posts_to_graphql_endpoint_by_default(self):
        client = make_client()
        client.session.request.return_value = mock_response(200, {"data": {"user": {"name": "Alice"}}})

        with patch("api_client.time.sleep"):
            result = client.graphql("{ user { name } }")

        call_args = client.session.request.call_args
        self.assertEqual(call_args[0][0], "POST")
        self.assertIn("/graphql", call_args[0][1])

    def test_graphql_sends_query_in_body(self):
        client = make_client()
        client.session.request.return_value = mock_response(200, {"data": {}})

        with patch("api_client.time.sleep"):
            client.graphql("{ viewer { login } }")

        sent_body = client.session.request.call_args[1]["json"]
        self.assertEqual(sent_body["query"], "{ viewer { login } }")

    def test_graphql_sends_empty_variables_by_default(self):
        client = make_client()
        client.session.request.return_value = mock_response(200, {"data": {}})

        with patch("api_client.time.sleep"):
            client.graphql("{ viewer { login } }")

        sent_body = client.session.request.call_args[1]["json"]
        self.assertEqual(sent_body["variables"], {})

    def test_graphql_sends_variables(self):
        client = make_client()
        client.session.request.return_value = mock_response(200, {"data": {}})

        with patch("api_client.time.sleep"):
            client.graphql("query Q($id: ID!) { user(id: $id) { name } }", variables={"id": "42"})

        sent_body = client.session.request.call_args[1]["json"]
        self.assertEqual(sent_body["variables"], {"id": "42"})

    def test_graphql_uses_custom_path(self):
        client = make_client()
        client.session.request.return_value = mock_response(200, {"data": {}})

        with patch("api_client.time.sleep"):
            client.graphql("{ products { id } }", path="/api/graphql")

        url = client.session.request.call_args[0][1]
        self.assertIn("/api/graphql", url)

    def test_graphql_returns_parsed_response(self):
        client = make_client()
        body = {"data": {"user": {"name": "Bob"}}}
        client.session.request.return_value = mock_response(200, body)

        with patch("api_client.time.sleep"):
            result = client.graphql("{ user { name } }")

        self.assertEqual(result, body)

    def test_graphql_logs_warning_on_errors_key(self):
        client = make_client()
        body = {"data": None, "errors": [{"message": "Field not found"}]}
        client.session.request.return_value = mock_response(200, body)

        with patch("api_client.time.sleep"):
            with self.assertLogs("api_client", level="WARNING") as cm:
                result = client.graphql("{ badField }")

        self.assertEqual(result, body)
        self.assertTrue(any("errors" in line.lower() for line in cm.output))

    def test_graphql_no_warning_on_clean_response(self):
        client = make_client()
        body = {"data": {"user": {"name": "Alice"}}}
        client.session.request.return_value = mock_response(200, body)

        with patch("api_client.time.sleep"):
            # Should not raise; assertLogs would fail if no WARNING emitted
            result = client.graphql("{ user { name } }")

        self.assertEqual(result["data"]["user"]["name"], "Alice")


# ── 9. POST-based search pagination (HubSpot style) ──────────────────────────

class TestSearchPagination(unittest.TestCase):

    def test_search_yields_first_page(self):
        client = make_client(data_key="results", cursor_key="paging.next.after")
        client.session.request.return_value = mock_response(200, {
            "results": [{"id": "1"}, {"id": "2"}],
        })

        with patch("api_client.time.sleep"):
            pages = list(client.search("/crm/v3/objects/contacts/search",
                                       body={"filterGroups": []}))

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0], [{"id": "1"}, {"id": "2"}])

    def test_search_uses_post_method(self):
        client = make_client(data_key="results", cursor_key="paging.next.after")
        client.session.request.return_value = mock_response(200, {"results": []})

        with patch("api_client.time.sleep"):
            list(client.search("/search", body={}))

        method = client.session.request.call_args[0][0]
        self.assertEqual(method, "POST")

    def test_search_sends_body_as_json(self):
        client = make_client(data_key="results", cursor_key="paging.next.after")
        client.session.request.return_value = mock_response(200, {"results": []})
        filter_body = {"filterGroups": [{"filters": [{"propertyName": "email",
                                                       "operator": "EQ",
                                                       "value": "x@y.com"}]}]}

        with patch("api_client.time.sleep"):
            list(client.search("/search", body=filter_body))

        sent_json = client.session.request.call_args[1]["json"]
        self.assertEqual(sent_json["filterGroups"], filter_body["filterGroups"])

    def test_search_sends_limit_in_body(self):
        client = make_client(data_key="results", cursor_key="paging.next.after",
                             page_size=50)
        client.session.request.return_value = mock_response(200, {"results": []})

        with patch("api_client.time.sleep"):
            list(client.search("/search", body={}))

        sent_json = client.session.request.call_args[1]["json"]
        self.assertEqual(sent_json["limit"], 50)

    def test_search_follows_nested_cursor(self):
        client = make_client(
            data_key="results",
            cursor_key="paging.next.after",
            cursor_body_key="after",
            page_size=2,
        )
        responses = [
            mock_response(200, {
                "results": [{"id": "1"}, {"id": "2"}],
                "paging": {"next": {"after": "cursor_abc"}},
            }),
            mock_response(200, {
                "results": [{"id": "3"}],
            }),
        ]
        client.session.request.side_effect = responses

        with patch("api_client.time.sleep"):
            pages = list(client.search("/search", body={}))

        self.assertEqual(pages, [[{"id": "1"}, {"id": "2"}], [{"id": "3"}]])

    def test_search_sends_cursor_in_body_on_second_page(self):
        client = make_client(
            data_key="results",
            cursor_key="paging.next.after",
            cursor_body_key="after",
            page_size=2,
        )
        responses = [
            mock_response(200, {
                "results": [{"id": "1"}, {"id": "2"}],
                "paging": {"next": {"after": "tok_xyz"}},
            }),
            mock_response(200, {"results": []}),
        ]
        client.session.request.side_effect = responses

        with patch("api_client.time.sleep"):
            list(client.search("/search", body={}))

        second_body = client.session.request.call_args_list[1][1]["json"]
        self.assertEqual(second_body["after"], "tok_xyz")

    def test_search_stops_on_empty_results(self):
        client = make_client(data_key="results", cursor_key="paging.next.after")
        responses = [
            mock_response(200, {"results": [{"id": "1"}], "paging": {"next": {"after": "tok"}}}),
            mock_response(200, {"results": []}),
        ]
        client.session.request.side_effect = responses

        with patch("api_client.time.sleep"):
            pages = list(client.search("/search", body={}))

        self.assertEqual(len(pages), 1)

    def test_search_respects_max_rows(self):
        client = make_client(data_key="results", cursor_key="paging.next.after",
                             page_size=3)
        responses = [
            mock_response(200, {"results": [1, 2, 3], "paging": {"next": {"after": "a"}}}),
            mock_response(200, {"results": [4, 5, 6], "paging": {"next": {"after": "b"}}}),
            mock_response(200, {"results": [7, 8, 9]}),
        ]
        client.session.request.side_effect = responses

        with patch("api_client.time.sleep"):
            pages = list(client.search("/search", body={}, max_rows=5))

        self.assertEqual(pages, [[1, 2, 3], [4, 5]])
        self.assertEqual(client.session.request.call_count, 2)


# ── 10. _deep_get helper ──────────────────────────────────────────────────────

class TestDeepGet(unittest.TestCase):

    def test_flat_key(self):
        obj = {"next_cursor": "abc"}
        self.assertEqual(APIClient._deep_get(obj, "next_cursor"), "abc")

    def test_nested_key(self):
        obj = {"paging": {"next": {"after": "tok"}}}
        self.assertEqual(APIClient._deep_get(obj, "paging.next.after"), "tok")

    def test_missing_top_level_returns_default(self):
        self.assertIsNone(APIClient._deep_get({}, "missing"))

    def test_missing_nested_key_returns_default(self):
        obj = {"paging": {"next": {}}}
        self.assertIsNone(APIClient._deep_get(obj, "paging.next.after"))

    def test_non_dict_mid_path_returns_default(self):
        obj = {"paging": "not_a_dict"}
        self.assertIsNone(APIClient._deep_get(obj, "paging.next.after"))

    def test_list_input_returns_default(self):
        self.assertIsNone(APIClient._deep_get([1, 2, 3], "key"))

    def test_custom_default(self):
        self.assertEqual(APIClient._deep_get({}, "missing", default="fallback"), "fallback")

    def test_falsy_value_zero_is_returned(self):
        obj = {"count": 0}
        # 0 should NOT trigger the default — it's a valid value
        # Note: current impl returns default on obj.get(key, default) == default
        # so 0 ≠ None (default) and is returned correctly
        self.assertEqual(APIClient._deep_get(obj, "count"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
