"""
api_client.py — importable REST API client using requests.

Typical usage::

    import os
    from api_client import APIClient, APIError

    client = APIClient(
        base_url = "https://api.example.com",
        api_key  = os.environ.get("API_KEY", ""),
    )

    # Single GET request
    post = client.get("/posts/1")

    # POST request (create or HubSpot-style fetch)
    created = client.post("/posts", data={"title": "Hello", "body": "World"})

    # Paginated fetch — yields one page (list) at a time
    for page in client.paginate("/posts", mode="offset"):
        for item in page:
            process(item)

    # HubSpot-style POST search with cursor pagination
    for page in client.search("/crm/v3/objects/contacts/search", body={
        "filterGroups": [{"filters": [...]}],
        "properties": ["email"],
    }):
        for contact in page:
            process(contact)

    # GraphQL
    result = client.graphql("{ user(id: 1) { name email } }")
"""

import logging
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.exceptions import MaxRetryError
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)


# ── retry with logging ────────────────────────────────────────────────────────

class _LoggingRetry(Retry):
    """urllib3 Retry subclass that emits structured log messages on each attempt.

    - HTTP 429 (rate-limited) → ``log.info`` so operators see throttling.
    - Other retried errors (5xx, connection) → ``log.debug``.
    - Retries exhausted → ``log.error`` before re-raising ``MaxRetryError``.
    """

    def increment(self, method=None, url=None, response=None, error=None, **kwargs):
        """Log each retry attempt, then delegate to the standard urllib3 logic.

        Args:
            method (str | None): HTTP method of the failing request.
            url (str | None): Full URL of the failing request.
            response: urllib3 response object, present on HTTP-status retries.
            error: Exception object, present on connection-level retries.
            **kwargs: Forwarded to ``Retry.increment()``.

        Returns:
            Retry: New Retry instance with decremented counter.

        Raises:
            MaxRetryError: When no attempts remain, after logging at ERROR level.
        """
        if response is not None:
            if response.status == 429:
                log.info(
                    "Rate-limited (HTTP 429): %s %s — waiting before retry "
                    "(%d attempt(s) remaining)",
                    method, url, self.total or 0,
                )
            else:
                log.debug(
                    "Retry triggered (HTTP %s): %s %s — waiting before retry "
                    "(%d attempt(s) remaining)",
                    response.status, method, url, self.total or 0,
                )
        elif error is not None:
            log.debug(
                "Retry triggered (%s): %s %s — waiting before retry "
                "(%d attempt(s) remaining)",
                type(error).__name__, method, url, self.total or 0,
            )

        try:
            return super().increment(
                method=method, url=url, response=response, error=error, **kwargs
            )
        except MaxRetryError:
            if response is not None:
                log.error(
                    "Max retries exhausted: %s %s — last status HTTP %s",
                    method, url, response.status,
                )
            else:
                log.error(
                    "Max retries exhausted: %s %s — last error: %s",
                    method, url, error,
                )
            raise


# ── exceptions ────────────────────────────────────────────────────────────────

class APIError(Exception):
    """Exception raised for HTTP errors and response parse failures.

    Attributes:
        status (int): HTTP status code, or ``None`` for parse-only failures.
        body: Parsed response body (dict or list) or raw bytes when the
            body could not be decoded as JSON.
    """

    def __init__(self, message: str, status: int = None, body=None):
        """Initialise APIError.

        Args:
            message (str): Human-readable error description with an
                actionable remediation hint.
            status (int, optional): HTTP status code. Defaults to None.
            body (dict | list | bytes, optional): Raw or parsed response
                body for inspection. Defaults to None.
        """
        self.status = status
        self.body   = body
        super().__init__(message)


# ── client ────────────────────────────────────────────────────────────────────

class APIClient:
    """Minimal REST API client backed by requests.

    Handles rate limiting, retries, JSON serialisation, HTTP error checking,
    and three pagination strategies. All behaviour is configured via explicit
    constructor arguments so no file-loading is required at call sites.

    Attributes:
        base_url (str): Root URL with trailing slash stripped.
        api_key (str): Bearer token; empty string for public APIs.
        api_key_env (str): Env-var name used in authentication error messages.
        rate_delay (float): Minimum seconds between consecutive requests.
        page_size (int): Default items-per-page for all pagination modes.
        timeout (int): HTTP request timeout in seconds.
        pagination_mode (str): Default pagination strategy
            (``"cursor"``, ``"offset"``, or ``"page"``).
        data_key (str): Dict key that wraps the items list in paginated
            responses. Ignored when the API returns a bare JSON array.
        cursor_key (str): Dict key (or dot-notation path) carrying the
            next-page cursor token. Supports nested paths such as
            ``"paging.next.after"`` for HubSpot-style responses.
        cursor_param (str): Query-parameter name used to send the cursor
            on the next GET request in cursor mode. Defaults to ``"cursor"``.
            Set to ``"after"`` for GitHub, ``"page_token"`` for Google APIs,
            ``"starting_after"`` for Stripe, etc.
        cursor_body_key (str): Key in the POST request body used to send
            the cursor token on each page. Used by ``search()`` (POST-based
            cursor pagination). Defaults to ``"after"``.
        total_key (str): Dict key carrying the total record count
            (offset mode only; used to detect the last page early).
        offset_param (str): Query-parameter name for the offset value.
        limit_param (str): Query-parameter name for the page size
            (used by all pagination modes and by ``search()`` in the body).
        max_rows (int | None): Maximum total items to return across all pages.
            ``None`` means no limit (fetch everything).
        session (requests.Session): Session with retry logic and auth headers.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str          = "",
        api_key_env: str      = "API_KEY",
        rate_delay: float     = 6.0,
        max_retries: int      = 4,
        page_size: int        = 100,
        timeout: int          = 30,
        pagination_mode: str  = "cursor",
        data_key: str         = "data",
        cursor_key: str       = "next_cursor",
        cursor_param: str     = "cursor",
        cursor_body_key: str  = "after",
        total_key: str        = "total",
        offset_param: str     = "offset",
        limit_param: str      = "limit",
        max_rows: int         = None,
    ):
        """Initialise the client from explicit keyword arguments.

        Example::

            import os
            client = APIClient(
                base_url = "https://api.example.com",
                api_key  = os.environ.get("API_KEY", ""),
                rate_delay = 0.5,
            )

            # HubSpot CRM search configuration
            hs_client = APIClient(
                base_url        = "https://api.hubapi.com",
                api_key         = os.environ.get("HUBSPOT_API_KEY"),
                data_key        = "results",
                cursor_key      = "paging.next.after",
                cursor_body_key = "after",
            )
        """
        log.debug(
            "Setting up APIClient: base_url=%s  rate_delay=%.1fs  "
            "max_retries=%s  pagination=%s",
            base_url, rate_delay, max_retries, pagination_mode,
        )

        self.base_url        = base_url.rstrip("/")
        self.api_key         = api_key
        self.api_key_env     = api_key_env
        self.rate_delay      = rate_delay
        self.page_size       = page_size
        self.timeout         = timeout
        self.pagination_mode = pagination_mode
        self.data_key        = data_key
        self.cursor_key      = cursor_key
        self.cursor_param    = cursor_param
        self.cursor_body_key = cursor_body_key
        self.total_key       = total_key
        self.offset_param    = offset_param
        self.limit_param     = limit_param
        self.max_rows        = max_rows
        self._last_call      = 0.0
        self.session         = self._build_session(api_key, max_retries)

        log.debug("APIClient ready: %s", self.base_url)

    # ── session setup ─────────────────────────────────────────────────────────

    @staticmethod
    def _build_session(api_key: str, max_retries: int) -> requests.Session:
        """Create a requests Session with exponential-backoff retry.

        Retries automatically on 429 (rate-limited) and 5xx (server error)
        responses. Respects the ``Retry-After`` header when the server
        provides one. Backoff schedule with ``backoff_factor=1``:
        0 s, 2 s, 4 s, 8 s.

        Args:
            api_key (str): Bearer token, or ``""`` for public APIs.
            max_retries (int): Maximum retry attempts before raising.

        Returns:
            requests.Session: Configured session with retry and auth headers.
        """
        retry = _LoggingRetry(
            total                      = max_retries,
            backoff_factor             = 1,
            status_forcelist           = [429, 500, 502, 503, 504],
            allowed_methods            = ["GET", "POST", "PUT", "PATCH", "DELETE"],
            respect_retry_after_header = True,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session = requests.Session()
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        if api_key:
            session.headers["Authorization"] = f"Bearer {api_key}"
        return session

    # ── rate limiting ─────────────────────────────────────────────────────────

    def _throttle(self) -> None:
        """Enforce the minimum inter-request delay.

        Computes elapsed time since the last call and sleeps only for the
        remaining gap. The very first call never sleeps because
        ``_last_call`` starts at ``0.0`` (epoch), so the elapsed time is
        always greater than any realistic ``rate_delay``.
        """
        gap = self.rate_delay - (time.monotonic() - self._last_call)
        if gap > 0:
            log.debug("Rate-limiting: sleeping %.3fs", gap)
            time.sleep(gap)
        self._last_call = time.monotonic()

    # ── error message builder ─────────────────────────────────────────────────

    def _http_error_msg(self, method: str, path: str, status: int, body) -> str:
        """Build an actionable error message for a non-2xx HTTP response.

        Args:
            method (str): HTTP method (e.g. ``"GET"``).
            path (str): Request path (e.g. ``"/users/42"``).
            status (int): HTTP status code.
            body (dict | list | bytes): Parsed or raw response body,
                included in generic 4xx messages for debugging.

        Returns:
            str: Human-readable message with a specific remediation hint.
        """
        tag = f"[{method} {path}] HTTP {status}"
        if status in (401, 403):
            return (
                f"{tag} — Authentication failed. "
                f"Check your API key or token (env var '{self.api_key_env}')."
            )
        if status == 404:
            return f"{tag} — Endpoint not found. Verify the path and resource ID."
        if 500 <= status <= 599:
            return f"{tag} — Server error. Retry later or check the API status page."
        return (
            f"{tag} — Client error. "
            f"Check request parameters or body. Response: {body!r}"
        )

    # ── core HTTP request ─────────────────────────────────────────────────────

    def request(self, method: str, path: str, data=None, params=None):
        """Send one HTTP request and return the parsed response.

        Applies rate limiting before every request. 429 and 5xx retries
        are handled transparently by the session. JSON parsing is attempted
        regardless of status so that error responses with JSON bodies produce
        richer messages; the HTTP status message always takes precedence.

        Prefer the convenience methods ``get()``, ``post()``, ``put()``,
        ``patch()``, and ``delete()`` for cleaner call sites. Use
        ``request()`` directly when the HTTP method is determined at runtime.

        Args:
            method (str): HTTP method: ``"GET"``, ``"POST"``, ``"PUT"``,
                ``"PATCH"``, or ``"DELETE"``.
            path (str): Endpoint path, with or without a leading slash
                (e.g. ``"/users/42"`` or ``"users/42"``).
            data (dict, optional): Request body, serialised to JSON.
                Defaults to None.
            params (dict, optional): Query-string parameters. Defaults to
                None.

        Returns:
            dict | list: Parsed JSON response body, or ``{}`` for HTTP 204
                and responses with an empty body.

        Raises:
            APIError: On any non-2xx response not resolved by automatic
                retry, or when a 2xx response body cannot be decoded as
                valid JSON.

        Example::

            # Low-level — useful when method is a variable
            client.request("GET", "/users/42")
            client.request("POST", "/orders", data={"item_id": 1})

            # Prefer the convenience wrappers:
            client.get("/users/42")
            client.post("/orders", data={"item_id": 1})
        """
        self._throttle()

        url = f"{self.base_url}/{path.lstrip('/')}"
        log.debug("→ %s %s", method, url)

        try:
            r = self.session.request(
                method, url, params=params, json=data, timeout=self.timeout
            )
        except requests.RequestException as e:
            msg = f"[{method} {path}] Request failed — {type(e).__name__}: {e}"
            log.error(msg)
            raise APIError(msg, body=str(e)) from e

        log.info("%s %s → HTTP %s", method, path, r.status_code)

        if r.status_code == 204 or not r.content:
            log.debug("%s %s: empty/no-content response — returning {}", method, path)
            return {}

        try:
            payload = r.json()
        except ValueError as e:
            log.debug("%s %s: JSON parse failed — %s", method, path, e)
            payload = None

        if not (200 <= r.status_code < 300):
            msg = self._http_error_msg(method, path, r.status_code, payload or r.content[:200])
            log.error(msg)
            raise APIError(msg, status=r.status_code, body=payload or r.content)

        if payload is None:
            msg = (
                f"[{method} {path}] HTTP {r.status_code} — "
                f"Response is not valid JSON. "
                f"Got: {r.content[:200]!r}. Check the Content-Type header."
            )
            log.error(msg)
            raise APIError(msg, status=r.status_code, body=r.content)

        log.debug("%s %s: parsed response (%d bytes)", method, path, len(r.content))
        return payload

    # ── HTTP method convenience wrappers ──────────────────────────────────────

    def get(self, path: str, params=None):
        """Send a GET request and return the parsed response.

        Args:
            path (str): Endpoint path (e.g. ``"/users/42"``).
            params (dict, optional): Query-string parameters.

        Returns:
            dict | list: Parsed JSON response body.

        Raises:
            APIError: On HTTP errors or non-JSON response bodies.

        Example::

            user   = client.get("/users/42")
            result = client.get("/search", params={"q": "hello world"})
        """
        return self.request("GET", path, params=params)

    def post(self, path: str, data=None, params=None):
        """Send a POST request and return the parsed response.

        Used for creating resources and for APIs (like HubSpot CRM search)
        that use POST to fetch data.

        Args:
            path (str): Endpoint path (e.g. ``"/orders"``).
            data (dict, optional): Request body, serialised to JSON.
            params (dict, optional): Query-string parameters.

        Returns:
            dict | list: Parsed JSON response body.

        Raises:
            APIError: On HTTP errors or non-JSON response bodies.

        Example::

            order = client.post("/orders", data={"item_id": 1, "qty": 2})

            # HubSpot CRM search — POST to fetch data
            result = client.post("/crm/v3/objects/contacts/search", data={
                "filterGroups": [...],
                "properties": ["email"],
                "limit": 100,
            })
        """
        return self.request("POST", path, data=data, params=params)

    def put(self, path: str, data=None, params=None):
        """Send a PUT request and return the parsed response.

        Args:
            path (str): Endpoint path (e.g. ``"/users/42"``).
            data (dict, optional): Request body, serialised to JSON.
            params (dict, optional): Query-string parameters.

        Returns:
            dict | list: Parsed JSON response body.

        Raises:
            APIError: On HTTP errors or non-JSON response bodies.

        Example::

            updated = client.put("/users/42", data={"name": "Alice"})
        """
        return self.request("PUT", path, data=data, params=params)

    def patch(self, path: str, data=None, params=None):
        """Send a PATCH request and return the parsed response.

        Args:
            path (str): Endpoint path (e.g. ``"/users/42"``).
            data (dict, optional): Partial update body, serialised to JSON.
            params (dict, optional): Query-string parameters.

        Returns:
            dict | list: Parsed JSON response body.

        Raises:
            APIError: On HTTP errors or non-JSON response bodies.

        Example::

            patched = client.patch("/users/42", data={"email": "new@example.com"})
        """
        return self.request("PATCH", path, data=data, params=params)

    def delete(self, path: str, params=None):
        """Send a DELETE request and return the parsed response.

        Args:
            path (str): Endpoint path (e.g. ``"/orders/99"``).
            params (dict, optional): Query-string parameters.

        Returns:
            dict: ``{}`` on HTTP 204 or empty body; parsed JSON otherwise.

        Raises:
            APIError: On HTTP errors.

        Example::

            client.delete("/orders/99")
        """
        return self.request("DELETE", path, params=params)

    def graphql(self, query: str, variables=None, path: str = "/graphql"):
        """Execute a GraphQL query via HTTP POST.

        GraphQL APIs accept queries as POST requests with a JSON body
        containing ``query`` and optionally ``variables``. This method
        wraps ``post()`` with the standard GraphQL envelope.

        Rate limiting, retries, and error handling are inherited from
        ``request()``. For paginated GraphQL responses, call ``graphql()``
        in a loop and extract the cursor from the response yourself.

        Args:
            query (str): GraphQL query or mutation string.
            variables (dict, optional): Variable bindings for the query.
                Defaults to ``{}``.
            path (str, optional): GraphQL endpoint path. Defaults to
                ``"/graphql"``.

        Returns:
            dict: Full GraphQL response, typically ``{"data": {...}}``.
                Errors are surfaced via the ``"errors"`` key; this method
                does not raise on GraphQL-level errors, only HTTP errors.

        Raises:
            APIError: On HTTP errors or non-JSON response bodies.

        Example::

            # Simple query
            result = client.graphql("{ viewer { login } }")
            print(result["data"]["viewer"]["login"])

            # Query with variables
            result = client.graphql(
                query     = "query GetUser($id: ID!) { user(id: $id) { name } }",
                variables = {"id": "42"},
            )

            # Custom endpoint path
            result = client.graphql("{ products { id name } }", path="/api/graphql")
        """
        result = self.post(path, data={"query": query, "variables": variables or {}})
        if isinstance(result, dict) and result.get("errors"):
            log.warning("[GraphQL %s] Response contains errors: %s", path, result["errors"])
        return result

    # ── POST-based search / pagination (HubSpot CRM style) ───────────────────

    def search(self, path: str, body=None, page_size: int = None,
               max_rows: int = None):
        """POST-based cursor pagination for HubSpot-style search APIs.

        HubSpot CRM search endpoints accept filter criteria in the POST body
        and return a cursor in the response (e.g. ``paging.next.after``).
        Each subsequent page sends the cursor back in the POST body using
        ``cursor_body_key`` (default ``"after"``).

        Configure the client for HubSpot::

            client = APIClient(
                base_url        = "https://api.hubapi.com",
                api_key         = os.environ.get("HUBSPOT_API_KEY"),
                data_key        = "results",           # HubSpot wraps items here
                cursor_key      = "paging.next.after", # dot-notation nested path
                cursor_body_key = "after",             # key in the next POST body
            )

            for page in client.search(
                "/crm/v3/objects/contacts/search",
                body = {
                    "filterGroups": [{"filters": [
                        {"propertyName": "email", "operator": "CONTAINS_TOKEN",
                         "value": "example.com"},
                    ]}],
                    "properties": ["email", "firstname", "lastname"],
                },
                max_rows = 500,
            ):
                for contact in page:
                    process(contact)

        Args:
            path (str): Endpoint path (e.g.
                ``"/crm/v3/objects/contacts/search"``).
            body (dict, optional): Initial POST body (filter criteria,
                properties, sorts, etc.). The ``limit`` key is added
                automatically from ``page_size``. Do not include the cursor
                key here; it is managed internally.
            page_size (int, optional): Items per page for this call only.
                Overrides the constructor ``page_size``.
            max_rows (int, optional): Total-item cap for this call only.
                Overrides the constructor ``max_rows``.

        Yields:
            list: One page of items.

        Raises:
            APIError: Propagated from ``post()`` on HTTP errors.
        """
        limit = max_rows if max_rows is not None else self.max_rows
        log.debug(
            "Starting search pagination: path=%s  page_size=%s  max_rows=%s",
            path, page_size or self.page_size, limit,
        )
        yield from self._paginate_search(path, body or {}, page_size, limit)

    # ── pagination helpers ────────────────────────────────────────────────────

    def _items(self, res) -> list:
        """Extract the items list from a response.

        Handles two common API response shapes: a bare JSON array returned
        directly, or a JSON object with a configurable wrapper key
        (``data_key``). Returns an empty list if the key is absent.

        Args:
            res (dict | list): Parsed response returned by ``request()``.

        Returns:
            list: The extracted items, or ``[]`` if the wrapper key is absent.
        """
        if isinstance(res, list):
            return res
        return res.get(self.data_key, [])

    def _meta(self, res, key):
        """Return ``res[key]`` when ``res`` is a dict, else ``None``.

        Args:
            res (dict | list): Parsed response returned by ``request()``.
            key (str): Key to look up.

        Returns:
            Any | None: The value at ``key``, or ``None``.
        """
        return res.get(key) if isinstance(res, dict) else None

    @staticmethod
    def _deep_get(obj, key_path, default=None):
        """Get a value from a nested dict using a dot-notation key path.

        Supports both flat keys (``"next_cursor"``) and nested paths
        (``"paging.next.after"``). Returns ``default`` if any key in the
        path is missing or if a non-dict is encountered mid-path.

        Args:
            obj (dict | any): Starting object to traverse.
            key_path (str): Dot-separated key path, e.g. ``"paging.next.after"``.
            default: Value to return when the path cannot be resolved.
                Defaults to ``None``.

        Returns:
            Any: Value at the path, or ``default``.

        Example::

            _deep_get({"paging": {"next": {"after": "tok"}}}, "paging.next.after")
            # → "tok"

            _deep_get({"next_cursor": "abc"}, "next_cursor")
            # → "abc"  (flat key, same behaviour)
        """
        for key in key_path.split("."):
            if not isinstance(obj, dict):
                return default
            obj = obj.get(key, default)
            if obj is default:
                return default
        return obj

    def _truncate_to_limit(self, items: list, total_rows: int, max_rows) -> tuple:
        """Truncate ``items`` to the ``max_rows`` cap.

        Args:
            items (list): Items from the current page.
            total_rows (int): Items fetched before this page.
            max_rows (int | None): Total-item cap; ``None`` means no limit.

        Returns:
            tuple[list, bool]: Truncated (or unchanged) items, and whether
                the limit was reached so the caller should stop paginating.
        """
        if max_rows is None:
            return items, False
        remaining = max_rows - total_rows
        if len(items) >= remaining:
            return items[:remaining], True
        return items, False

    # ── GET-based pagination ──────────────────────────────────────────────────

    def paginate(self, path: str, params=None, page_size: int = None,
                 mode: str = None, max_rows: int = None):
        """Paginate a GET endpoint and yield one page at a time.

        All three modes stop automatically when the API signals the last
        page: an empty result, a partial page, an exhausted ``total``, or
        a missing cursor. The generator never makes a speculative extra
        request beyond the last real page.

        For POST-based search pagination (HubSpot CRM, etc.), use
        ``search()`` instead.

        Args:
            path (str): Endpoint path (e.g. ``"/orders"``).
            params (dict, optional): Base query parameters merged into
                every page request. Do not include limit/offset/page/cursor
                keys here; those are managed internally. Defaults to None.
            page_size (int, optional): Items per page for this call only.
                Overrides ``page_size`` from config. Defaults to None.
            mode (str, optional): Pagination strategy for this call only:
                ``"cursor"``, ``"offset"``, or ``"page"``. Overrides
                ``pagination_mode`` from config. Defaults to None.
            max_rows (int, optional): Maximum total items to return across
                all pages for this call only. Overrides ``max_rows`` from
                config. The final page is truncated when the limit falls
                mid-page. ``None`` fetches everything. Defaults to None.

        Yields:
            list: One page of items (may be shorter than ``page_size`` on
                the final page when ``max_rows`` truncates it).

        Raises:
            ValueError: If ``mode`` is not one of the three valid values.
            APIError: Propagated from ``request()`` on HTTP errors.

        Example::

            for page in client.paginate("/orders", params={"status": "open"}):
                for order in page:
                    process(order)

            # Fetch at most 500 rows
            for page in client.paginate("/events", max_rows=500):
                ...
        """
        mode  = mode or self.pagination_mode
        limit = max_rows if max_rows is not None else self.max_rows
        log.debug("Starting pagination: path=%s  mode=%s  page_size=%s  max_rows=%s",
                  path, mode, page_size or self.page_size, limit)
        if mode == "cursor":
            yield from self._paginate_cursor(path, params, page_size, limit)
        elif mode == "offset":
            yield from self._paginate_offset(path, params, page_size, limit)
        elif mode == "page":
            yield from self._paginate_pages(path, params, page_size, limit)
        else:
            raise ValueError(
                f"Unknown pagination mode '{mode}'. "
                f"Valid values: 'cursor', 'offset', 'page'."
            )

    def _paginate_cursor(self, path, params, page_size, max_rows):
        """Cursor-based pagination implementation.

        Sends the cursor token returned by each response as a ``cursor``
        query parameter on the next request. Stops when the response
        contains no cursor token or returns an empty items list.

        ``cursor_key`` supports dot-notation for nested response paths
        (e.g. ``"paging.next.after"``).

        Suitable for: HubSpot, Stripe, Twitter/X, Salesforce, and most
        modern APIs that use opaque page tokens instead of numeric offsets.

        Args:
            path (str): Endpoint path.
            params (dict | None): Base query parameters.
            page_size (int | None): Items per page; falls back to
                ``self.page_size``.
            max_rows (int | None): Total-item cap; ``None`` means no limit.

        Yields:
            list: Items from each page.
        """
        p          = {**(params or {}), self.limit_param: page_size or self.page_size}
        page_num   = 0
        total_rows = 0
        try:
            while True:
                page_num += 1
                log.debug("Cursor: fetching page %d from %s", page_num, path)
                res   = self.request("GET", path, params=p)
                items = self._items(res)
                if not items:
                    log.debug("Cursor: empty response on page %d — done", page_num)
                    break
                items, limit_reached = self._truncate_to_limit(items, total_rows, max_rows)
                log.debug("Cursor: page %d → %d items", page_num, len(items))
                total_rows += len(items)
                yield items
                if limit_reached:
                    log.info("max_rows (%d) reached after page %d — stopping", max_rows, page_num)
                    break
                cursor = self._deep_get(res, self.cursor_key)
                if cursor is None or cursor == "":
                    log.debug("Cursor: no cursor after page %d — done", page_num)
                    break
                p[self.cursor_param] = cursor
        finally:
            log.info(
                "Cursor pagination complete: %s — %d page(s), %d rows total",
                path, page_num, total_rows,
            )

    def _paginate_offset(self, path, params, page_size, max_rows):
        """Offset/limit pagination implementation.

        Increments the offset by ``page_size`` after each request. Stops
        when the response is empty, returns fewer items than ``page_size``
        (signals the last page), or the accumulated offset reaches the
        ``total`` value when the API includes it.

        Suitable for: Django REST Framework, SQL-backed APIs, ElasticSearch,
        and any endpoint that accepts a numeric skip/take pair.

        Args:
            path (str): Endpoint path.
            params (dict | None): Base query parameters.
            page_size (int | None): Items per page; falls back to
                ``self.page_size``.
            max_rows (int | None): Total-item cap; ``None`` means no limit.

        Yields:
            list: Items from each page.
        """
        size       = page_size or self.page_size
        offset     = 0
        page_num   = 0
        total_rows = 0
        p          = {**(params or {}), self.limit_param: size, self.offset_param: offset}
        try:
            while True:
                page_num += 1
                log.debug(
                    "Offset: fetching page %d (offset=%d, limit=%d) from %s",
                    page_num, offset, size, path,
                )
                p[self.offset_param] = offset
                res   = self.request("GET", path, params=p)
                items = self._items(res)
                if not items:
                    log.debug("Offset: empty response on page %d — done", page_num)
                    break
                items, limit_reached = self._truncate_to_limit(items, total_rows, max_rows)
                log.debug("Offset: page %d → %d items", page_num, len(items))
                total_rows += len(items)
                yield items
                if limit_reached:
                    log.info("max_rows (%d) reached after page %d — stopping", max_rows, page_num)
                    break
                total = self._deep_get(res, self.total_key)
                if total is not None:
                    try:
                        total = int(total)
                    except (ValueError, TypeError):
                        log.debug("Offset: ignoring non-integer total %r from '%s'", total, self.total_key)
                        total = None
                offset += size
                if total is not None and offset >= total:
                    log.debug(
                        "Offset: reached total (%d) after page %d — done", total, page_num
                    )
                    break
                if len(items) < size:
                    log.debug(
                        "Offset: partial page (%d < %d) on page %d — done",
                        len(items), size, page_num,
                    )
                    break
        finally:
            log.info(
                "Offset pagination complete: %s — %d page(s), %d rows total",
                path, page_num, total_rows,
            )

    def _paginate_pages(self, path, params, page_size, max_rows):
        """Page-number pagination implementation.

        Sends ``page=1``, ``page=2``, and so on until the response
        returns fewer items than ``page_size`` or an empty list.

        Suitable for: GitHub, many older REST APIs, and any framework
        that exposes a ``page`` + ``limit`` (or ``per_page``) interface.

        Args:
            path (str): Endpoint path.
            params (dict | None): Base query parameters.
            page_size (int | None): Items per page; falls back to
                ``self.page_size``.
            max_rows (int | None): Total-item cap; ``None`` means no limit.

        Yields:
            list: Items from each page.
        """
        size       = page_size or self.page_size
        page       = 1
        total_rows = 0
        p          = {**(params or {}), self.limit_param: size, "page": page}
        try:
            while True:
                log.debug("Page: fetching page %d from %s", page, path)
                p["page"] = page
                res   = self.request("GET", path, params=p)
                items = self._items(res)
                if not items:
                    log.debug("Page: empty response on page %d — done", page)
                    break
                items, limit_reached = self._truncate_to_limit(items, total_rows, max_rows)
                log.debug("Page: page %d → %d items", page, len(items))
                total_rows += len(items)
                yield items
                if limit_reached:
                    log.info("max_rows (%d) reached after page %d — stopping", max_rows, page)
                    break
                if len(items) < size:
                    log.debug(
                        "Page: partial page (%d < %d) on page %d — done",
                        len(items), size, page,
                    )
                    break
                page += 1
        finally:
            log.info(
                "Page pagination complete: %s — %d page(s), %d rows total",
                path, page, total_rows,
            )

    def _paginate_search(self, path, body, page_size, max_rows):
        """POST-based cursor pagination implementation (HubSpot CRM search style).

        Each page is a POST request. The cursor token is read from the
        response using ``cursor_key`` (supports dot-notation), and sent
        in the next POST body under ``cursor_body_key``.

        Args:
            path (str): Endpoint path.
            body (dict): Initial POST body (filters, sorts, properties, etc.).
            page_size (int | None): Items per page; falls back to
                ``self.page_size``.
            max_rows (int | None): Total-item cap; ``None`` means no limit.

        Yields:
            list: Items from each page.
        """
        size       = page_size or self.page_size
        b          = {**body, self.limit_param: size}
        page_num   = 0
        total_rows = 0
        try:
            while True:
                page_num += 1
                log.debug("Search: fetching page %d from %s", page_num, path)
                res   = self.post(path, data=b)
                items = self._items(res)
                if not items:
                    log.debug("Search: empty response on page %d — done", page_num)
                    break
                items, limit_reached = self._truncate_to_limit(items, total_rows, max_rows)
                log.debug("Search: page %d → %d items", page_num, len(items))
                total_rows += len(items)
                yield items
                if limit_reached:
                    log.info("max_rows (%d) reached after page %d — stopping", max_rows, page_num)
                    break
                cursor = self._deep_get(res, self.cursor_key)
                if cursor is None or cursor == "":
                    log.debug("Search: no cursor after page %d — done", page_num)
                    break
                b[self.cursor_body_key] = cursor
        finally:
            log.info(
                "Search pagination complete: %s — %d page(s), %d rows total",
                path, page_num, total_rows,
            )
