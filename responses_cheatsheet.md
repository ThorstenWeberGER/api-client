# responses Library Cheat Sheet

Mock HTTP requests in tests without hitting the network • `pip install responses`

---

## Basic Pattern

```python
import responses
import requests

@responses.activate
def test_my_api():
    responses.add(
        responses.GET,
        "https://api.example.com/data",
        json={"id": 1},
        status=200
    )
    result = requests.get("https://api.example.com/data")
    assert result.json()["id"] == 1
```

---

## HTTP Methods

| Method | Code |
|--------|------|
| GET | `responses.GET` |
| POST | `responses.POST` |
| PUT | `responses.PUT` |
| DELETE | `responses.DELETE` |
| PATCH | `responses.PATCH` |

---

## Status Codes

| Status | Use Case |
|--------|----------|
| `200` | Success |
| `400` | Bad Request |
| `401` | Unauthorized |
| `403` | Forbidden |
| `404` | Not Found |
| `429` | Rate Limited |
| `500` | Server Error |
| `503` | Service Unavailable |

---

## Response Types

### JSON Response
```python
responses.add(
    responses.GET,
    "https://api.example.com/data",
    json={"id": 1, "name": "test"},
    status=200
)
```

### Plain Text Response
```python
responses.add(
    responses.GET,
    "https://api.example.com/data",
    body="plain text response",
    status=200
)
```

### HTML Response
```python
responses.add(
    responses.GET,
    "https://api.example.com/data",
    body="<html><body>Not JSON</body></html>",
    status=200
)
```

### Custom Headers
```python
responses.add(
    responses.GET,
    "https://api.example.com/data",
    json={"data": "value"},
    headers={"X-Rate-Limit": "100", "X-RateLimit-Remaining": "99"},
    status=200
)
```

---

## Error Scenarios

### Timeout
```python
@responses.activate
def test_timeout():
    responses.add(
        responses.GET,
        "https://api.example.com/data",
        body=requests.exceptions.Timeout()
    )
    with pytest.raises(requests.exceptions.Timeout):
        requests.get("https://api.example.com/data", timeout=2)
```

### Connection Error
```python
@responses.activate
def test_connection_error():
    responses.add(
        responses.GET,
        "https://api.example.com/data",
        body=requests.exceptions.ConnectionError("Connection failed")
    )
    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get("https://api.example.com/data")
```

### JSON Decode Error
```python
@responses.activate
def test_invalid_json():
    responses.add(
        responses.GET,
        "https://api.example.com/data",
        body="not valid json",
        status=200
    )
    with pytest.raises(requests.exceptions.JSONDecodeError):
        requests.get("https://api.example.com/data").json()
```

---

## URL Matching

### Exact Match
```python
responses.add(
    responses.GET,
    "https://api.example.com/data?id=1",
    json={"found": True}
)
```

### Regex Match
```python
import re

responses.add(
    responses.GET,
    url=re.compile(r"https://api\.example\.com/data\?.*"),
    json={"found": True}
)
```

---

## Request Body Validation (POST/PUT)

```python
from responses import matchers

@responses.activate
def test_post_with_json():
    responses.add(
        responses.POST,
        "https://api.example.com/create",
        json={"id": 123},
        match=[responses.matchers.json_params_matcher({"name": "John"})]
    )
    result = requests.post(
        "https://api.example.com/create",
        json={"name": "John"}
    )
    assert result.json()["id"] == 123
```

---

## Multiple Responses (Sequence/Retries)

```python
@responses.activate
def test_retry_logic():
    # First call fails, second succeeds
    responses.add(responses.GET, "https://api.example.com/data", status=500)
    responses.add(responses.GET, "https://api.example.com/data", 
                  json={"status": "ok"}, status=200)
    
    # Your retry logic should handle both calls
    result = your_api_with_retry()
    assert result["status"] == "ok"
```

---

## Verify Calls

```python
@responses.activate
def test_call_verification():
    responses.add(
        responses.GET,
        "https://api.example.com/data",
        json={"id": 1}
    )
    
    requests.get("https://api.example.com/data")
    requests.get("https://api.example.com/data")
    
    # Assertions
    assert len(responses.calls) == 2
    assert responses.calls[0].request.url == "https://api.example.com/data"
    assert responses.calls[0].request.body is None
    assert responses.calls[1].request.method == "GET"
```

---

## Context Manager Alternative

```python
def test_api():
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, "https://api.example.com/data",
                 json={"id": 1}, status=200)
        result = requests.get("https://api.example.com/data")
        assert result.json()["id"] == 1
```

---

## Common Test Patterns

### Test Happy Path
```python
@responses.activate
def test_successful_api_call():
    responses.add(
        responses.GET,
        "https://api.example.com/contacts",
        json={"contacts": [{"id": 1, "name": "Alice"}]},
        status=200
    )
    contacts = fetch_contacts()
    assert len(contacts) == 1
    assert contacts[0]["name"] == "Alice"
```

### Test Error Handling
```python
@responses.activate
def test_api_error_handling():
    responses.add(
        responses.GET,
        "https://api.example.com/contacts",
        json={"error": "Not found"},
        status=404
    )
    with pytest.raises(ValueError, match="Not found"):
        fetch_contacts()
```

### Test Rate Limiting
```python
@responses.activate
def test_rate_limit_handling():
    responses.add(
        responses.GET,
        "https://api.example.com/contacts",
        json={"error": "Rate limited"},
        status=429,
        headers={"Retry-After": "60"}
    )
    with pytest.raises(RateLimitError):
        fetch_contacts()
```

---

## Tips & Best Practices

- **Decorator vs Context Manager**: Use `@responses.activate` for simple tests, context manager for complex setup
- **Test error messages**: Don't just mock errors—verify your code logs/retries/handles them gracefully
- **Use matchers for POST/PUT**: Validate request body with `responses.matchers.json_params_matcher()`
- **Test sequences**: Add multiple responses for retry/pagination logic
- **Combine with httpbin.org**: Use httpbin for manual testing, responses for automated tests
- **Keep mocks focused**: Mock only the endpoints you're testing, not entire API chains
- **Verify calls**: Assert that your code called the API with correct params/headers

---

## Quick Reference

```python
# Setup
import responses, requests, pytest

# Decorator
@responses.activate

# Add mock
responses.add(responses.GET, "url", json={}, status=200)

# Make request
requests.get("url")

# Assert
assert len(responses.calls) == 1
```
