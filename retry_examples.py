"""
retry_examples.py — Examples of using tenacity for API resilience.

Tenacity is a mature library for retry logic with exponential backoff,
jitter, and flexible exception handling. See https://tenacity.readthedocs.io/
"""

import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    wait_fixed,
    retry_if_exception_type,
    retry_if_result,
)


# ── Example 1: Basic exponential backoff on connection errors ─────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(requests.ConnectionError),
)
def fetch_with_retry(url):
    """Fetch a URL with automatic retry on connection failures.

    - Retries up to 3 times
    - Waits 2^attempt seconds, clamped to [1, 10] seconds
    - Only retries on connection errors, not 404s or auth failures
    """
    response = requests.get(url, timeout=5)
    response.raise_for_status()  # raise on 4xx/5xx
    return response.json()


# ── Example 2: Retry on specific HTTP status codes ──────────────────────────

def is_retryable_status(response):
    """Return True if the response should be retried (5xx or 429)."""
    return response.status_code in (429, 500, 502, 503, 504)


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=0.5, min=1, max=30),
    retry=retry_if_result(is_retryable_status),
)
def api_call_with_status_retry(endpoint):
    """Call an API, retrying on rate limit (429) and server errors (5xx).

    - Retries up to 4 times
    - Uses exponential backoff: 0.5s, 1s, 2s, 4s (multiplied by 2^attempt)
    - Does NOT retry on 4xx errors (client's fault)
    """
    response = requests.get(endpoint, timeout=10)
    return response


# ── Example 3: Combining multiple exception types ───────────────────────────

@retry(
    stop=stop_after_attempt(5),
    wait=wait_fixed(2),  # fixed 2-second delay between retries
    retry=retry_if_exception_type((
        requests.ConnectionError,
        requests.Timeout,
        requests.HTTPError,
    )),
)
def robust_api_call(url):
    """Retry on connection, timeout, and HTTP errors with fixed delay.

    - Retries up to 5 times
    - Waits 2 seconds before each retry
    - Retries on connection/timeout issues AND HTTP errors (unlike example 2)
    """
    response = requests.get(url, timeout=5)
    response.raise_for_status()
    return response.json()


# ── Example 4: Using with APIClient from this project ──────────────────────

from api_client import APIClient, APIError


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(APIError),
)
def fetch_data_with_client(base_url, path):
    """Fetch data using APIClient with automatic retry.

    The retry logic wraps the client call, retrying on APIError
    (which covers network failures and transient server errors).
    """
    client = APIClient(base_url=base_url)
    return client.call("GET", path)


# ── Example 5: Retry with custom logging/callbacks ─────────────────────────

from tenacity import before_log, after_log
import logging

log = logging.getLogger(__name__)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    before=before_log(log, logging.DEBUG),
    after=after_log(log, logging.DEBUG),
)
def logged_api_call(url):
    """Retry with detailed logging of each attempt and result.

    before_log: logs before each attempt - for example for debugging or info
    after_log: logs after each attempt
    """
    response = requests.get(url, timeout=5)
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    # Example usage (will fail since these are demo URLs)
    try:
        result = fetch_with_retry("https://httpbin.org/get")
        print("Success:", result)
    except Exception as e:
        print("Final error:", type(e).__name__, e)
