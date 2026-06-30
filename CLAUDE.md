# api-client — Codebase Guide for AI Assistants

A minimal, self-contained HTTP client library for REST APIs designed for reliable API calls with rate limiting, automatic retries, and flexible pagination.

## Quick Facts

- **Language:** Python 3.x
- **Main Module:** `api_client.py` (self-contained, single file)
- **Entry Point:** `main.py` (example usage)
- **Tests:** `test_api_client.py` (unit tests using `unittest` + mocks)
- **Dependencies:** `requests`, `responses`, `pytest`, `tenacity`, `python-dotenv`
- **License:** Not specified

## Repository Structure

```
api-client/
├── api_client.py          # Core library (APIClient, APIError, _LoggingRetry)
├── main.py                # Example usage against JSONPlaceholder API
├── test_api_client.py     # Unit tests covering all major code paths
├── test_responses.py      # Additional response testing (mock-based)
├── retry_examples.py      # Examples of retry strategies
├── requirements.txt       # Python dependencies
└── README.md              # User-facing documentation (comprehensive)
```

## Architecture Overview

The library is organized around three layers:

### 1. **Initialization Layer** (`_build_session`)
- Creates a `requests.Session` with a custom `_LoggingRetry` adapter
- Mounts adapters for both HTTP and HTTPS
- Sets Bearer token authorization header if `api_key` is provided
- Configured with exponential backoff: 0s, 2s, 4s, 8s

### 2. **Request Layer** (`call` method)
- **Rate limiting:** `_throttle()` enforces minimum inter-request delay
- **HTTP execution:** Sends request via session with retry handling
- **Response parsing:** Attempts JSON parsing regardless of status code
- **Error handling:** Checks status and raises `APIError` on non-2xx responses
- **Best-effort body reading:** Parses JSON for richer error messages on 4xx/5xx

### 3. **Pagination Layer** (three implementations)
- **Cursor mode** (`_paginate_cursor`): Token-based, stops when no cursor or empty items
- **Offset mode** (`_paginate_offset`): Skip/take numeric offsets, uses optional `total` count
- **Page mode** (`_paginate_pages`): Incremental page numbers starting at 1

All three share common helpers:
- `_items(res)`: Extracts items list from bare array or dict wrapper
- `_meta(res, key)`: Safe dict-key lookup (returns None for arrays)
- `_truncate_to_limit(items, total_rows, max_rows)`: Applies per-call row cap

## Key Design Patterns

### Configuration via Constructor
All behavior is controlled via explicit keyword arguments to `APIClient()`:
```python
client = APIClient(
    base_url = "https://api.example.com",
    api_key  = os.environ.get("API_KEY", ""),
    rate_delay = 6.0,           # 10 req/min default
    max_retries = 4,            # Exponential backoff
    page_size = 100,            # Items per page
    pagination_mode = "cursor", # cursor | offset | page
    data_key = "data",          # Dict wrapper key for items
    cursor_key = "next_cursor", # Cursor token key
    total_key = "total",        # Total count key (offset mode)
    offset_param = "offset",    # Offset query-param name
    limit_param = "limit",      # Limit query-param name
    max_rows = None,            # Total-rows cap
    timeout = 30,               # HTTP timeout in seconds
)
```

No config files are read. This design ensures:
- **IDE completion:** All options visible inline
- **Single source of truth:** Config lives at the call site
- **Testability:** No file I/O during initialization

### Error Handling
All failures raise `APIError` (never bare `Exception`):
```python
try:
    data = client.call("GET", "/users/42")
except APIError as e:
    print(e.status)   # int or None (None = network/parse failure)
    print(e.body)     # dict/list/bytes/str
    print(str(e))     # Human-readable with remediation hint
```

Errors are **always logged at ERROR level before raising**, ensuring failures are auditable even if the caller silently catches them.

### Rate Limiting
`_throttle()` computes remaining gap since last call and sleeps only if needed:
```
gap = rate_delay - (time.monotonic() - _last_call)
if gap > 0:
    sleep(gap)
_last_call = time.monotonic()
```

First call never sleeps (since `_last_call` starts at 0.0).

### Retry Strategy
Uses `urllib3.Retry` subclass `_LoggingRetry` to hook into the connection pool's retry logic:
- **HTTP 429** (rate-limited) → logs at `INFO` level
- **5xx and connection errors** → logs at `DEBUG` level
- **Retries exhausted** → logs at `ERROR` before re-raising

This avoids losing transient-success retries that would be invisible if wrapped at the `call()` level.

### Pagination Generality
All three pagination modes share:
- Same entry point: `client.paginate(path, params, mode, max_rows)`
- Same generator interface: yields one page at a time
- Same helpers: `_items()`, `_meta()`, `_truncate_to_limit()`
- Same stop logic: empty page, partial page, exhausted total/cursor, or `max_rows` hit

Users select the mode that matches their API:
- **Cursor:** HubSpot, Stripe, Twitter/X, Salesforce (modern APIs with opaque tokens)
- **Offset:** Django REST, FastAPI, SQL-backed services (skip/take pattern)
- **Page:** GitHub, older Rails/Django (page-number interface)

### Logging
Module logger name is `api_client`. No handlers configured by the library—that's the caller's responsibility:
```python
import logging
logging.basicConfig(level=logging.INFO, ...)
logging.getLogger("api_client").setLevel(logging.DEBUG)  # debug just this module
```

Log levels:
- **DEBUG:** Throttle sleeps, request URLs, parse failures, pagination progress
- **INFO:** Each HTTP request/response, rate-limit events, pagination summaries
- **ERROR:** All failures before raising `APIError`

## Code Organization

### Top-Level Exports
- `APIError` — Exception class
- `APIClient` — Main client class
- `_LoggingRetry` — Internal retry class (prefix indicates private)

### Public Methods
| Method | Purpose |
|--------|---------|
| `__init__(...)` | Constructor; initializes session and stores config |
| `call(method, path, data, params)` | Send one HTTP request, return parsed JSON |
| `paginate(path, params, page_size, mode, max_rows)` | Generator that yields pages |

### Private Methods (prefixed with `_`)
| Method | Purpose |
|--------|---------|
| `_build_session(api_key, max_retries)` | Create requests.Session with retry adapter |
| `_throttle()` | Enforce minimum inter-request delay |
| `_http_error_msg(method, path, status, body)` | Build actionable error message |
| `_items(res)` | Extract items list from response (bare array or dict) |
| `_meta(res, key)` | Safe dict-key lookup (None for non-dicts) |
| `_truncate_to_limit(items, total_rows, max_rows)` | Apply per-call row cap |
| `_paginate_cursor(path, params, page_size, max_rows)` | Cursor-based pagination impl |
| `_paginate_offset(path, params, page_size, max_rows)` | Offset-based pagination impl |
| `_paginate_pages(path, params, page_size, max_rows)` | Page-number pagination impl |

## Testing

### Test Patterns
Tests use `unittest` + `unittest.mock` only—no third-party test frameworks or network calls:

```python
from unittest.mock import MagicMock, patch
from api_client import APIClient, APIError

def make_client(**overrides):
    """Factory that builds a test client with mocked session."""
    defaults = {
        "base_url": "https://api.test.com",
        "api_key": "test-key",
        "rate_delay": 0.5,
        "page_size": 3,
        ...
    }
    defaults.update(overrides)
    client = APIClient(**defaults)
    client.session = MagicMock()  # ← Replace real session with mock
    return client

def mock_response(status: int, body):
    """Build a fake requests response."""
    r = MagicMock()
    r.status_code = status
    r.content = json.dumps(body).encode("utf-8")
    r.json.return_value = body
    return r

# Usage in a test:
client = make_client()
client.session.request.return_value = mock_response(200, {"id": 1})
result = client.call("GET", "/posts/1")
assert result == {"id": 1}
```

### Running Tests
```bash
python -m unittest test_api_client.py -v
```

### Test Coverage Areas
| Class | Scope |
|-------|-------|
| `TestRateLimiting` | First call doesn't sleep; subsequent calls sleep remaining gap |
| `TestUrlEncoding` | Params dict passed intact to requests (spaces, ampersands, etc.) |
| `TestHttpErrors` | 401/403/404/422/500 each raise APIError with correct message |
| `TestPagination` | All three modes stop correctly; cursor forwarded on subsequent calls |
| `TestJsonParseFailure` | Non-JSON and invalid UTF-8 bodies raise APIError with preview |
| `TestMaxRows` | max_rows cap stops early and truncates final page correctly |

## Development Workflows

### Adding a Feature
1. **Understand the intent:** Read the README.md Architecture section
2. **Identify the layer:** Is it initialization, request, or pagination?
3. **Add tests first:** Extend an existing test class or create a new one
4. **Implement:** Keep logic in the appropriate method (avoid new public methods)
5. **Log:** Add appropriate DEBUG/INFO/ERROR messages
6. **Update README.md:** Document the feature if it affects the public API

### Fixing a Bug
1. **Write a failing test** that reproduces the bug
2. **Identify the method** from the stack trace and code paths
3. **Fix in the minimum scope:** Prefer fixing the specific method over refactoring
4. **Verify the test passes:** No silent fixes
5. **Scan for side effects:** Do other tests still pass? Do edge cases still work?

### Modifying Configuration
The library is configuration-heavy by design. When adding a new parameter:
1. **Add the kwarg** to `__init__()` with a sensible default
2. **Store as `self.param`** (lowercase, snake_case)
3. **Document in README.md** Constructor Arguments table
4. **Use in appropriate methods** (rate_delay in `_throttle()`, pagination params in `_paginate_*`)
5. **Add tests** covering the new parameter in action

### Backward Compatibility
There is no versioning or compatibility shim in the codebase. Changes are made directly:
- Renaming a parameter? Update all call sites and tests.
- Removing a method? Delete it—no deprecation stub.
- Changing default behavior? Update the README and tests.

This is acceptable for a small, stable library. The public surface area is intentionally minimal (one class, one exception, two methods).

## Common Patterns & Conventions

### Naming Conventions
- **Public:** `APIClient`, `APIError` (PascalCase classes)
- **Private:** `_LoggingRetry`, `_build_session`, `_throttle` (underscore prefix)
- **Parameters:** `base_url`, `api_key`, `page_size` (snake_case)
- **Local variables:** `client`, `response`, `items`, `offset` (lowercase, descriptive)

### Code Style
- **No type hints:** Python 3.x without formal annotations (acceptable for small codebases)
- **Docstrings:** Module and class docstrings present; method docstrings concise but complete
- **Comments:** Minimal; code is self-documenting. Added only for non-obvious logic.
- **Line length:** No strict limit, but kept reasonable (under 100 chars where practical)
- **Imports:** Standard library first, then third-party (`requests`, `urllib3`)

### Error Messages
Always actionable with a specific remediation hint:
- **401/403:** "Check your API key or token (env var '...')."
- **404:** "Verify the path and resource ID."
- **5xx:** "Retry later or check the API status page."
- **Other 4xx:** Include response preview for debugging

### Logging Best Practices
- **Secrets never logged:** API keys are never printed, only env-var names
- **Request audit trail:** Every HTTP request/response logged at INFO
- **Errors always logged:** At ERROR level before raising, so callers can silently catch without losing auditability
- **Pagination summaries:** At INFO level (page count, row count, mode)
- **Rate-limiting visible:** HTTP 429 at INFO, other transient errors at DEBUG

## Performance Considerations

### Rate Limiting
The `_throttle()` method is precise: it only sleeps the remaining gap, not a fixed delay each time. For `rate_delay=6.0` (10 req/min), if a request takes 2 seconds, the next sleep is only 4 seconds.

### Pagination Efficiency
- **Cursor mode:** Stops as soon as the API signals no next cursor; never makes a speculative empty request
- **Offset mode:** Uses optional `total` count from API to stop early without a final empty request
- **Page mode:** Stops on partial page (< `page_size` items) without a speculative next request

### Memory
Pagination yields one page at a time. For large datasets, iterate and process each page immediately rather than collecting all pages in a list.

### Connection Pooling
Uses `requests.Session` with persistent HTTP connection pooling. The session is created once in `_build_session()` and reused for all requests in the client's lifetime.

## Extending the Library

### Safe Extensions
These can be added without breaking the public API:

1. **New pagination mode:** Add `_paginate_<mode>()` and update the `mode` check in `paginate()`
2. **New error-message pattern:** Add condition to `_http_error_msg()`
3. **Token refresh on 401:** Subclass `APIClient` and override `call()` to catch 401 and refresh
4. **Async support:** Create `APIClientAsync` using `httpx.AsyncClient` (parallel, not replacement)
5. **Structured logging:** Configure a JSON formatter on `logging.getLogger("api_client")` at the call site

### Unsafe Changes
These break backward compatibility:
- Removing a constructor parameter
- Changing a method signature
- Renaming public methods
- Removing pagination modes

## Key Files to Know

### Main Implementation
- **`api_client.py`:** Everything—423 lines of documented code
  - `_LoggingRetry` (lines 36–95): Retry logging hook
  - `APIError` (lines 100–121): Exception class
  - `APIClient.__init__()` (lines 156–206): Initialization
  - `APIClient.call()` (lines 289–364): Single HTTP request
  - `APIClient.paginate()` (lines 418–474): Pagination entry point
  - `_paginate_*()` (lines 476–647): Three pagination implementations

### Examples & Tests
- **`main.py`:** Usage example with JSONPlaceholder (public API)
- **`test_api_client.py`:** Unit tests covering all code paths
- **`test_responses.py`:** Additional response handling tests
- **`retry_examples.py`:** Retry strategy demonstrations
- **`README.md`:** Comprehensive user-facing documentation

### Configuration
- **`requirements.txt`:** Direct dependencies (requests, responses, pytest, tenacity, python-dotenv)

## Future Improvements (from README.md)

1. **API Key Rotation:** Round-robin or retry with next key on 429
2. **File/Sink Logging:** JSON-structured output for Datadog/CloudWatch
3. **OAuth 2.0 / Token Refresh:** Catch 401 in a subclass, refresh token, retry
4. **Async Support:** Replace `requests` with `httpx.AsyncClient`, make `call()` and `paginate()` async

## Quick Reference: Common Tasks

### Set up for development
```bash
pip install -r requirements.txt
python -m unittest test_api_client.py -v
```

### Add a new test
```python
# In test_api_client.py
class TestMyFeature(unittest.TestCase):
    def test_case_name(self):
        client = make_client()
        client.session.request.return_value = mock_response(200, {...})
        # Assert behavior
```

### Debug with logs
```python
import logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")
client = APIClient(...)  # logs DEBUG messages during init
for page in client.paginate(...):  # logs pagination progress
    ...
```

### Override pagination mode per-call
```python
# Use offset mode for this one call, ignoring constructor default
for page in client.paginate("/legacy", mode="offset", max_rows=500):
    ...
```

### Catch specific errors
```python
from api_client import APIError

try:
    client.call("GET", "/protected")
except APIError as e:
    if e.status == 401:
        # Refresh token
    elif e.status is None:
        # Network failure
    else:
        # HTTP error
```

## AI Assistant Notes

When working on this codebase:

1. **Respect the single-file design.** `api_client.py` is intentionally self-contained with no separate modules. Keep it that way.

2. **Configuration over code.** Features are controlled by constructor kwargs, not feature flags or config files. If adding a behavior, make it configurable.

3. **Test-first for changes.** Write a failing test in `test_api_client.py` first, then implement. Use mocks (no network calls).

4. **Logging is a feature, not an afterthought.** Every non-trivial operation has a log statement at DEBUG (details) or INFO (audit trail) level. Errors are always logged at ERROR before raising.

5. **Error handling is explicit.** Every code path that can fail has a try/except and raises `APIError` (never bare Exception). Messages include remediation hints.

6. **Pagination is abstracted.** All three modes (cursor, offset, page) share the same generator interface. When fixing one, check if the fix applies to the others.

7. **Backwards compatibility is not maintained.** Changes are made directly; no deprecation stubs or version shims.

8. **Keep it small.** The library is intentionally minimal. Resist adding new public methods; instead, make existing ones more configurable.

9. **Documentation is in README.md.** Keep the README in sync with the code. Update the architecture diagram, tables, and examples when making changes.

10. **No external test runners.** Tests use only `unittest` from the standard library. No pytest, nose, or other third-party runners (though requirements.txt includes pytest for potential future use).
