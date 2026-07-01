"""
test_error_responses.py — Error-handling tests for each HTTP error class.

Tests use unittest.mock (no third-party libraries or network calls). The
session is replaced with a MagicMock so responses are delivered directly to
the request() error-handling path, exercising _http_error_msg() for each
distinct HTTP error class.

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

import json
import unittest
from unittest.mock import MagicMock

# Import the custom API client and custom error class being tested
from api_client import APIClient, APIError

# Define a constant base URL for the mocked API requests
BASE = "https://api.test.example"


def make_client(**overrides):
    """Build an APIClient with a mocked session."""
    # Define standard configuration settings for the test client
    defaults = dict(
        base_url=BASE,
        api_key="test-key",
        api_key_env="MY_API_KEY",
        rate_delay=0.0,
        max_retries=0,
        page_size=10,
        timeout=5,
    )
    # Allow specific test cases to overwrite default settings if needed
    defaults.update(overrides)
    client = APIClient(**defaults)
    
    # Replace the actual network session object with a mock to prevent real network calls
    client.session = MagicMock()
    return client


def mock_response(status, body):
    """Build a fake requests response."""
    # Create a mock object to simulate an HTTP response
    r = MagicMock()
    r.status_code = status
    # Convert the Python dictionary body into a JSON string and encode it to bytes
    r.content = json.dumps(body).encode("utf-8")
    # Configure the mock's .json() method to return the original dictionary
    r.json.return_value = body
    return r


# Test suite targeting HTTP 400 Bad Request error scenarios
class TestErrorClass400BadRequest(unittest.TestCase):
    """400 - server rejected the request as malformed."""

    def setUp(self):
        """Runs before every individual test method to initialize the environment."""
        self.client = make_client()
        # Pre-configure the mocked session to return a 400 Bad Request for any request made
        self.client.session.request.return_value = mock_response(
            400, {"error": "missing required field 'name'"}
        )

    def test_raises_api_error(self):
        """Verify that a 400 response triggers a custom APIError exception."""
        with self.assertRaises(APIError) as ctx:
            self.client.get("/items")
        # Assert that the status code captured in the exception is 400
        self.assertEqual(ctx.exception.status, 400)

    def test_message_describes_malformed_request(self):
        """Verify the error message mentions the status code and indicates a malformed request."""
        with self.assertRaises(APIError) as ctx:
            self.client.get("/items")
        msg = str(ctx.exception)
        self.assertIn("400", msg)
        self.assertIn("malformed", msg.lower())

    def test_message_guides_to_api_documentation(self):
        """Verify that the error message helpfully points the user to documentation."""
        with self.assertRaises(APIError) as ctx:
            self.client.get("/items")
        self.assertIn("documentation", str(ctx.exception).lower())

    def test_message_includes_response_body_for_debugging(self):
        """Verify that custom JSON error codes from the server are visible in the message."""
        body = {"error": "missing required field 'name'", "code": "INVALID_INPUT"}
        # Temporarily overwrite the response structure for this specific test
        self.client.session.request.return_value = mock_response(400, body)
        with self.assertRaises(APIError) as ctx:
            self.client.get("/items")
        self.assertIn("INVALID_INPUT", str(ctx.exception))

    def test_body_accessible_on_exception(self):
        """Verify the raw JSON payload is directly attached to the exception object."""
        body = {"error": "bad input"}
        # Overwrite the response structure to test a POST context
        self.client.session.request.return_value = mock_response(400, body)
        with self.assertRaises(APIError) as ctx:
            self.client.post("/orders", data={"qty": -1})
        # Check if the exception object exposes the exact server response body
        self.assertEqual(ctx.exception.body, body)
        
class TestErrorClass401Unauthorized(unittest.TestCase):
    """401 — missing or rejected credentials."""

    def setUp(self):
        self.client = make_client()
        self.client.session.request.return_value = mock_response(
            401, {"error": "Unauthorized"}
        )

    def test_raises_api_error(self):
        with self.assertRaises(APIError) as ctx:
            self.client.get("/protected")
        self.assertEqual(ctx.exception.status, 401)

    def test_message_mentions_credentials(self):
        with self.assertRaises(APIError) as ctx:
            self.client.get("/protected")
        self.assertIn("credentials", str(ctx.exception).lower())

    def test_message_names_the_env_var(self):
        client = make_client(api_key_env="CUSTOM_API_KEY")
        client.session.request.return_value = mock_response(
            401, {"error": "Unauthorized"}
        )
        with self.assertRaises(APIError) as ctx:
            client.get("/protected")
        self.assertIn("CUSTOM_API_KEY", str(ctx.exception))

    def test_401_message_differs_from_403(self):
        """401 and 403 must produce distinct messages — different root causes."""
        msgs = []
        for status in (401, 403):
            self.client.session.request.return_value = mock_response(
                status, {"error": "access error"}
            )
            with self.assertRaises(APIError) as ctx:
                self.client.get("/resource")
            msgs.append(str(ctx.exception))
        self.assertNotEqual(msgs[0], msgs[1])


class TestErrorClass403Forbidden(unittest.TestCase):
    """403 — authenticated but permission denied."""

    def setUp(self):
        self.client = make_client()
        self.client.session.request.return_value = mock_response(
            403, {"error": "Forbidden"}
        )

    def test_raises_api_error(self):
        with self.assertRaises(APIError) as ctx:
            self.client.delete("/admin/users/42")
        self.assertEqual(ctx.exception.status, 403)

    def test_message_mentions_permission(self):
        with self.assertRaises(APIError) as ctx:
            self.client.delete("/admin/users/42")
        self.assertIn("permission", str(ctx.exception).lower())

    def test_message_does_not_mention_api_key(self):
        """403 is not a credentials problem — the key is valid but insufficient."""
        client = make_client(api_key_env="SECRET_KEY")
        client.session.request.return_value = mock_response(
            403, {"error": "Forbidden"}
        )
        with self.assertRaises(APIError) as ctx:
            client.get("/admin")
        self.assertNotIn("SECRET_KEY", str(ctx.exception))

    def test_message_guides_to_contact_api_owner(self):
        with self.assertRaises(APIError) as ctx:
            self.client.get("/admin")
        self.assertIn("denied", str(ctx.exception).lower())


class TestErrorClass429RateLimited(unittest.TestCase):
    """429 Too Many Requests — rate limit reached.

    In production, 429 is in status_forcelist and exhausts retries before
    returning. These tests deliver 429 directly via the mock to verify the
    error message produced by _http_error_msg() for that status code.
    """

    def setUp(self):
        self.client = make_client()
        self.client.session.request.return_value = mock_response(
            429, {"error": "rate limit exceeded"}
        )

    def test_raises_api_error(self):
        with self.assertRaises(APIError) as ctx:
            self.client.get("/feed")
        self.assertEqual(ctx.exception.status, 429)

    def test_message_mentions_rate_limit(self):
        with self.assertRaises(APIError) as ctx:
            self.client.get("/feed")
        self.assertIn("rate limit", str(ctx.exception).lower())

    def test_message_guides_to_increase_rate_delay(self):
        with self.assertRaises(APIError) as ctx:
            self.client.get("/feed")
        self.assertIn("rate_delay", str(ctx.exception))

    def test_429_message_differs_from_400(self):
        """Rate limiting and bad requests are unrelated — must not share a message."""
        msgs = []
        for status in (429, 400):
            self.client.session.request.return_value = mock_response(
                status, {"error": "err"}
            )
            with self.assertRaises(APIError) as ctx:
                self.client.get("/feed")
            msgs.append(str(ctx.exception))
        self.assertNotEqual(msgs[0], msgs[1])


class TestErrorClass500InternalServerError(unittest.TestCase):
    """500 — server-side bug; different from gateway errors (502/503/504).

    In production, 500 is in status_forcelist and exhausts retries before
    returning. These tests deliver 500 directly via the mock to verify the
    error message produced by _http_error_msg() for that status code.
    """

    def setUp(self):
        self.client = make_client()
        self.client.session.request.return_value = mock_response(
            500, {"error": "Internal Server Error"}
        )

    def test_raises_api_error(self):
        with self.assertRaises(APIError) as ctx:
            self.client.get("/reports")
        self.assertEqual(ctx.exception.status, 500)

    def test_message_describes_server_side_bug(self):
        with self.assertRaises(APIError) as ctx:
            self.client.get("/reports")
        self.assertIn("internal error", str(ctx.exception).lower())

    def test_message_guides_to_report_to_maintainer(self):
        with self.assertRaises(APIError) as ctx:
            self.client.get("/reports")
        self.assertIn("status page", str(ctx.exception).lower())

    def test_500_message_differs_from_503(self):
        """500 (API bug) and 503 (gateway unavailable) have different root causes."""
        msgs = []
        for status in (500, 503):
            self.client.session.request.return_value = mock_response(
                status, {"error": "server error"}
            )
            with self.assertRaises(APIError) as ctx:
                self.client.get("/reports")
            msgs.append(str(ctx.exception))
        self.assertNotEqual(msgs[0], msgs[1])


class TestErrorClassContrasts(unittest.TestCase):
    """Show that logically distinct error classes produce distinct messages."""

    def setUp(self):
        self.client = make_client(api_key_env="MY_API_KEY")

    def test_422_validation_differs_from_400_malformed(self):
        """400 = syntax problem; 422 = semantic/validation failure."""
        msgs = []
        for status in (400, 422):
            self.client.session.request.return_value = mock_response(
                status, {"error": "invalid"}
            )
            with self.assertRaises(APIError) as ctx:
                self.client.post("/users", data={})
            msgs.append(str(ctx.exception))
        self.assertNotEqual(msgs[0], msgs[1])

    def test_422_message_mentions_validation(self):
        self.client.session.request.return_value = mock_response(
            422, {"error": "email is required"}
        )
        with self.assertRaises(APIError) as ctx:
            self.client.post("/users", data={"name": "Alice"})
        self.assertIn("validation", str(ctx.exception).lower())

    def test_503_message_mentions_unavailable(self):
        """502/503/504 are combined — all mean 'upstream temporarily down'."""
        self.client.session.request.return_value = mock_response(
            503, {"error": "service unavailable"}
        )
        with self.assertRaises(APIError) as ctx:
            self.client.get("/data")
        self.assertIn("unavailable", str(ctx.exception).lower())

    def test_all_five_classes_produce_distinct_messages(self):
        """Each of the five error classes must carry unique guidance."""
        error_cases = [
            ("/class/400", 400),
            ("/class/401", 401),
            ("/class/403", 403),
            ("/class/429", 429),
            ("/class/500", 500),
        ]
        messages = []
        for path, status in error_cases:
            self.client.session.request.return_value = mock_response(
                status, {"error": "err"}
            )
            with self.assertRaises(APIError) as ctx:
                self.client.get(path)
            messages.append(str(ctx.exception))
        self.assertEqual(len(set(messages)), 5, "Each error class must produce a unique message")


if __name__ == "__main__":
    unittest.main(verbosity=2)
