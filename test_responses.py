# file: test_responses.py
# The 'responses' library allows you to simulate API responses without making actual network calls, which is useful for testing purposes.
# Run this script either directly or using a test runner like pytest to see the mocked responses in action.
# `pytest test_responses.py`


import responses
import requests


# The '@responses.activate' decorator is used to enable the mocking of requests within the test function.
@responses.activate
def test_simple():
    
    # Register a mock API endpoint via 'Response' object
    mock_api = responses.Response(
        method="PUT",
        url="http://example.com",
    )
    responses.add(mock_api)
    
    # Register a mock API endpoint via 'responses.add' method
    responses.add(
        responses.GET,
        "http://twitter.com/api/1/foobar",
        json={"error": "not found"},
        status=404,
    )

    # Make actual HTTP requests to the mocked endpoints. This will not hit the real endpoints but will return the mocked responses instead.
    resp1 = requests.get("http://twitter.com/api/1/foobar")
    resp2 = requests.put("http://example.com")

    # Assert that the responses match the expected values.
    assert resp1.json() == {"error": "not found"}
    assert resp1.status_code == 403

    assert resp2.status_code == 200
    assert resp2.request.method == "PUT"
    

if __name__ == "__main__":
    test_simple()