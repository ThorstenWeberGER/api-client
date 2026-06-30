"""
test_error_responses.py — Error-handling tests using the 'responses' library.

Unlike test_api_client.py (which replaces session.request with a MagicMock),
these tests use a real requests.Session and intercept at the HTTP adapter level.
This exercises the full stack: throttle → session.request → JSON parse → error
detection → _http_error_msg.

Note: the 'responses' library replaces HTTPAdapter.send(), bypassing urllib3's
retry logic. That means 429 and 5xx responses are returned immediately rather
than being retried. This lets us verify the error messages for those codes
directly. In production those codes trigger retries; the messages are visible
when retries are disabled (max_retries=0) or exhausted.

Five error classes under test
────────────────────────────
  1. 400 Bad Request      — malformed/missing fields; attaches response body
  2. 401 Unauthorized     — credentials rejected; names the env-var to fix
  3. 403 Forbidden        — permission denied; different message from 401
  4. 429 Too Many Requests— rate limit; advises increasing rate_delay
  5. 500 Internal Server  — server bug; different message from 502/503/504

Additional classes shown for contrast
──────────────────────────────────────
  422 Unprocessable Entity— validation failure; distinct from 400
  503 Service Unavailable — gateway/upstream; combined with 502/504
"""

import unittest

import responses as responses_lib
from responses import RequestsMock

from api_client import APIClient, APIError


BASE = "https://api.test.example"


def make_client(**overrides):
    """Build an APIClient pointing at BASE with rate limiting disabled."""
    defaults = dict(
        base_url=BASE,
        api_key="test-key",
        api_key_env="MY_API_KEY",
        rate_delay=0.0,
        max_retries=0,
        page_size=10,
        timeout=5,
    )
    defaults.update(overrides)
    return APIClient(**defaults)


class TestErrorClass400BadRequest(unittest.TestCase):
    """400 — server rejected the request as malformed."""

    @responses_lib.activate
    def test_raises_api_error(self):
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/items",
            json={"error": "missing required field 'name'"},
            status=400,
        )
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            client.get("/items")
        self.assertEqual(ctx.exception.status, 400)

    @responses_lib.activate
    def test_message_describes_malformed_request(self):
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/items",
            json={"error": "missing required field 'name'"},
            status=400,
        )
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            client.get("/items")
        msg = str(ctx.exception)
        self.assertIn("400", msg)
        self.assertIn("malformed", msg.lower())

    @responses_lib.activate
    def test_message_guides_to_api_documentation(self):
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/items",
            json={"error": "missing required field 'name'"},
            status=400,
        )
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            client.get("/items")
        msg = str(ctx.exception)
        self.assertIn("documentation", msg.lower())

    @responses_lib.activate
    def test_message_includes_response_body_for_debugging(self):
        body = {"error": "missing required field 'name'", "code": "INVALID_INPUT"}
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/items",
            json=body,
            status=400,
        )
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            client.get("/items")
        # Body is included so the developer can see validation details
        self.assertIn("INVALID_INPUT", str(ctx.exception))

    @responses_lib.activate
    def test_body_accessible_on_exception(self):
        body = {"error": "bad input"}
        responses_lib.add(
            responses_lib.POST,
            f"{BASE}/orders",
            json=body,
            status=400,
        )
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            client.post("/orders", data={"qty": -1})
        self.assertEqual(ctx.exception.body, body)


class TestErrorClass401Unauthorized(unittest.TestCase):
    """401 — missing or rejected credentials."""

    @responses_lib.activate
    def test_raises_api_error(self):
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/protected",
            json={"error": "Unauthorized"},
            status=401,
        )
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            client.get("/protected")
        self.assertEqual(ctx.exception.status, 401)

    @responses_lib.activate
    def test_message_mentions_credentials(self):
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/protected",
            json={"error": "Unauthorized"},
            status=401,
        )
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            client.get("/protected")
        self.assertIn("credentials", str(ctx.exception).lower())

    @responses_lib.activate
    def test_message_names_the_env_var(self):
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/protected",
            json={"error": "Unauthorized"},
            status=401,
        )
        client = make_client(api_key_env="CUSTOM_API_KEY")
        with self.assertRaises(APIError) as ctx:
            client.get("/protected")
        self.assertIn("CUSTOM_API_KEY", str(ctx.exception))

    @responses_lib.activate
    def test_401_message_differs_from_403(self):
        """401 and 403 must produce distinct messages — different root causes."""
        for status in (401, 403):
            responses_lib.add(
                responses_lib.GET,
                f"{BASE}/resource",
                json={"error": "access error"},
                status=status,
            )

        client = make_client()
        with self.assertRaises(APIError) as ctx401:
            client.get("/resource")
        with self.assertRaises(APIError) as ctx403:
            client.get("/resource")

        self.assertNotEqual(str(ctx401.exception), str(ctx403.exception))


class TestErrorClass403Forbidden(unittest.TestCase):
    """403 — authenticated but permission denied."""

    @responses_lib.activate
    def test_raises_api_error(self):
        responses_lib.add(
            responses_lib.DELETE,
            f"{BASE}/admin/users/42",
            json={"error": "Forbidden"},
            status=403,
        )
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            client.delete("/admin/users/42")
        self.assertEqual(ctx.exception.status, 403)

    @responses_lib.activate
    def test_message_mentions_permission(self):
        responses_lib.add(
            responses_lib.DELETE,
            f"{BASE}/admin/users/42",
            json={"error": "Forbidden"},
            status=403,
        )
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            client.delete("/admin/users/42")
        msg = str(ctx.exception).lower()
        self.assertIn("permission", msg)

    @responses_lib.activate
    def test_message_does_not_mention_api_key(self):
        """403 is not a credentials problem — the key is valid but insufficient."""
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/admin",
            json={"error": "Forbidden"},
            status=403,
        )
        client = make_client(api_key_env="SECRET_KEY")
        with self.assertRaises(APIError) as ctx:
            client.get("/admin")
        # 403 guidance should point to permissions, not the API key env var
        self.assertNotIn("SECRET_KEY", str(ctx.exception))

    @responses_lib.activate
    def test_message_guides_to_contact_api_owner(self):
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/admin",
            json={"error": "Forbidden"},
            status=403,
        )
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            client.get("/admin")
        msg = str(ctx.exception).lower()
        self.assertIn("denied", msg)


class TestErrorClass429RateLimited(unittest.TestCase):
    """429 Too Many Requests — rate limit reached.

    429 is in status_forcelist, so urllib3 exhausts retries before returning
    the response. With max_retries=0 the retry loop fails immediately. The
    client extracts the 429 status from the RetryError reason string and
    calls _http_error_msg to produce a specific message.
    """

    @responses_lib.activate
    def test_raises_api_error(self):
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/feed",
            json={"error": "rate limit exceeded"},
            status=429,
        )
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            client.get("/feed")
        self.assertEqual(ctx.exception.status, 429)

    @responses_lib.activate
    def test_message_mentions_rate_limit(self):
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/feed",
            json={"error": "rate limit exceeded"},
            status=429,
        )
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            client.get("/feed")
        self.assertIn("rate limit", str(ctx.exception).lower())

    @responses_lib.activate
    def test_message_guides_to_increase_rate_delay(self):
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/feed",
            json={"error": "rate limit exceeded"},
            status=429,
        )
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            client.get("/feed")
        self.assertIn("rate_delay", str(ctx.exception))

    @responses_lib.activate
    def test_429_message_differs_from_400(self):
        """Rate limiting and bad requests are unrelated — must not share a message."""
        responses_lib.add(
            responses_lib.GET, f"{BASE}/feed", json={"error": "rate limited"}, status=429,
        )
        responses_lib.add(
            responses_lib.GET, f"{BASE}/feed2", json={"error": "bad params"}, status=400,
        )
        client = make_client()
        with self.assertRaises(APIError) as ctx429:
            client.get("/feed")
        with self.assertRaises(APIError) as ctx400:
            client.get("/feed2")
        self.assertNotEqual(str(ctx429.exception), str(ctx400.exception))


class TestErrorClass500InternalServerError(unittest.TestCase):
    """500 — server-side bug; different from gateway errors (502/503/504).

    500 is in status_forcelist; the client extracts the status from the
    RetryError reason string and produces a specific message that points the
    developer at the API maintainer rather than telling them to fix their code.
    """

    @responses_lib.activate
    def test_raises_api_error(self):
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/reports",
            json={"error": "Internal Server Error"},
            status=500,
        )
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            client.get("/reports")
        self.assertEqual(ctx.exception.status, 500)

    @responses_lib.activate
    def test_message_describes_server_side_bug(self):
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/reports",
            json={"error": "Internal Server Error"},
            status=500,
        )
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            client.get("/reports")
        msg = str(ctx.exception).lower()
        self.assertIn("internal error", msg)

    @responses_lib.activate
    def test_message_guides_to_report_to_maintainer(self):
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/reports",
            json={"error": "Internal Server Error"},
            status=500,
        )
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            client.get("/reports")
        msg = str(ctx.exception).lower()
        self.assertIn("status page", msg)

    @responses_lib.activate
    def test_500_message_differs_from_503(self):
        """500 (API bug) and 503 (gateway unavailable) have different root causes."""
        responses_lib.add(
            responses_lib.GET, f"{BASE}/reports500", json={"error": "server error"}, status=500,
        )
        responses_lib.add(
            responses_lib.GET, f"{BASE}/reports503", json={"error": "service down"}, status=503,
        )
        client = make_client()
        with self.assertRaises(APIError) as ctx500:
            client.get("/reports500")
        with self.assertRaises(APIError) as ctx503:
            client.get("/reports503")
        self.assertNotEqual(str(ctx500.exception), str(ctx503.exception))


class TestErrorClassContrasts(unittest.TestCase):
    """Show that logically distinct error classes produce distinct messages."""

    @responses_lib.activate
    def test_422_validation_differs_from_400_malformed(self):
        """400 = syntax problem; 422 = semantic/validation failure."""
        for status in (400, 422):
            responses_lib.add(
                responses_lib.POST,
                f"{BASE}/users",
                json={"error": "invalid"},
                status=status,
            )
        client = make_client()
        with self.assertRaises(APIError) as ctx400:
            client.post("/users", data={})
        with self.assertRaises(APIError) as ctx422:
            client.post("/users", data={})
        self.assertNotEqual(str(ctx400.exception), str(ctx422.exception))

    @responses_lib.activate
    def test_422_message_mentions_validation(self):
        responses_lib.add(
            responses_lib.POST,
            f"{BASE}/users",
            json={"error": "email is required"},
            status=422,
        )
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            client.post("/users", data={"name": "Alice"})
        self.assertIn("validation", str(ctx.exception).lower())

    @responses_lib.activate
    def test_503_message_mentions_unavailable(self):
        """502/503/504 are combined — all mean 'upstream temporarily down'."""
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/data",
            json={"error": "service unavailable"},
            status=503,
        )
        client = make_client()
        with self.assertRaises(APIError) as ctx:
            client.get("/data")
        msg = str(ctx.exception).lower()
        self.assertIn("unavailable", msg)

    @responses_lib.activate
    def test_all_five_classes_produce_distinct_messages(self):
        """Each of the five error classes must carry unique guidance."""
        # Use distinct URLs so each response is registered unambiguously
        error_cases = [
            ("/class/400", 400),
            ("/class/401", 401),
            ("/class/403", 403),
            ("/class/429", 429),
            ("/class/500", 500),
        ]
        client = make_client(api_key_env="MY_API_KEY")
        for path, status in error_cases:
            responses_lib.add(
                responses_lib.GET, f"{BASE}{path}", json={"error": "err"}, status=status,
            )

        messages = []
        for path, _ in error_cases:
            with self.assertRaises(APIError) as ctx:
                client.get(path)
            messages.append(str(ctx.exception))

        self.assertEqual(len(set(messages)), 5, "Each error class must produce a unique message")


if __name__ == "__main__":
    unittest.main(verbosity=2)
