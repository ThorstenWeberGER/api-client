# Using MagicMock in API unittests

## Major Concepts and Libraries Used

**Libraries:**

* unittest: Python's built-in unit testing framework. It provides the base TestCase class, test runner structures, and assertion toolsets (like assertEqual and assertRaises).
* unittest.mock.MagicMock: A specialized class used to mimic real Python objects. It records how it was called and allows developers to pre-program its return values, removing the need for a live internet connection during testing.
* json: Python’s built-in library for parsing and creating JSON structures, used here to turn standard python dictionaries into mock server payloads.

**Concepts:**

* Mocking: Replacing slow, unpredictable, or external dependencies (like an HTTP internet session) with a predictable substitute object.
* Context Managers (with statement): Used in conjunction with assertRaises to isolate the line of code expected to fail, allowing tests to safely inspect the exception data immediately afterward.
* Exception Handling Testing: The pattern of deliberately inducing a failure condition to confirm that the software fails safely and transparently rather than silently breaking.

---

## How MagicMock Mimics Real Objects

> `MagicMock` does not actually know anything about your specific API client or its expected structure beforehand. Instead, it relies on a Python concept called **dynamic attribute creation**. When you call a method or access an attribute on a `MagicMock` object that hasn't been explicitly defined, it automatically creates a new mock object on the fly and returns it to you.

The reason `client.session = MagicMock()` works so seamlessly comes down to Python's highly dynamic nature and how `MagicMock` is engineered.

### 1. Dynamic Attribute Creation (On-Demand Generation)

In standard Python, trying to access a variable or method that does not exist will crash your program with an `AttributeError`. `MagicMock` overrides this behavior completely.

If your underlying `APIClient` code calls `self.session.request()`, Python looks at the `session` object (which is now a `MagicMock`) and asks for the `request` attribute. `MagicMock` intercept this request, notices that `request` doesn't exist yet, creates a *new* mock object to represent that method, attaches it under the name `request`, and returns it.

### 2. Duck Typing

Python operates on the principle of **duck typing**: *"If it walks like a duck and quacks like a duck, it's a duck."* Your `APIClient` code does not check if `self.session` is a genuine instance of a `requests.Session()` object. It only cares that the object has a `.request()` method it can call. Because `MagicMock` automatically supplies a callable mock object whenever `.request()` is accessed, the `APIClient` code continues executing without realizing it is talking to a fake object.

---

## Behind the Scenes: The Execution Flow

To see how this works in practice, let's look at how your test code and your client code interact with that single line:

### Step A: The Setup (In your Test File)

```python
client.session = MagicMock()
client.session.request.return_value = mock_response(400, body)

```

1. `client.session` is assigned a blank `MagicMock`.
2. You look up `.request`. `MagicMock` instantly creates a new child mock for the `request` method.
3. You set `.return_value` on that child mock. You are telling it: *"Whenever someone eventually executes the `request()` function, give them this fake 400 response."*

### Step B: The Execution (In your `api_client.py` Source Code)

When your test triggers `self.client.get("/items")`, the internal code of your actual `APIClient` likely looks something like this:

```python
def get(self, endpoint):
    # This calls the mock 'request' method we set up in Step A
    response = self.session.request("GET", f"{self.base_url}{endpoint}")
    
    if response.status_code >= 400:
        raise APIError(response)
    return response.json()

```

Because `self.session.request` is a mock, calling it with `"GET"` and the URL doesn't fire an internet request. It simply notes that it was called, looks at its pre-programmed `.return_value`, and hands back your fake 400 response object.

---

### Summary of the Illusion

`MagicMock` succeeds because it is entirely reactive. It does not look at your source code to figure out how to behave. Instead, it waits for your code to ask for an attribute or a method, creates that piece of the puzzle instantly in the background, and seamlessly acts as a placeholder so your application code can run its logic uninterrupted.

`MagicMock` works like a master impressionist or an improv actor. It doesn't actually know how your API works in advance. Instead, its rule is: **"Whatever you ask me for, I will say yes and pretend I have it."** Because Python allows objects to change on the fly, your code is completely fooled by the act.

---

## The Simple Analogy: The "Yes-Man" Actor

Imagine you hire an actor to pretend to be a world-class Butler for a play.

* You don't give the actor a list of everything a butler owns.
* Instead, whenever the script says, *"The butler pulls out a silver platter,"* the actor instantly pulls an imaginary silver platter out of thin air.
* If the script says, *"The butler opens the hidden safe,"* the actor points to the wall and pretends a safe is opening.

`MagicMock` is that actor. It doesn't actually have a `.request()` method until your code asks for it. The moment your code asks for it, `MagicMock` creates it on the spot.

---

## Code Examples

### 1. What normal Python does

Normally, if you try to use a method that doesn't exist, Python crashes instantly.

```python
class NormalObject:
    pass

stub = NormalObject()
stub.charge_credit_card()  # ❌ CRASH! AttributeError: 'NormalObject' object has no attribute 'charge_credit_card'

```

### 2. What MagicMock does

`MagicMock` never crashes. It automatically `invents` whatever you ask for; the butler opening the safe.

```python
from unittest.mock import MagicMock

fake_session = MagicMock()

# This method doesn't exist, but MagicMock invents it instantly when called
fake_session.charge_credit_card()  #   Works perfectly! No crash.
fake_session.launch_rocket_ship()  #   Works perfectly! No crash.

```

### 3. How we use it in your code

Instead of letting `MagicMock` simple inventing something and doing nothing, we tell it exactly what its invented methods should return.

```python
from unittest.mock import MagicMock

# 1. Create the blank fake object
fake_session = MagicMock()

# 2. Create mocked 'request' method and tell what to return when it gets called
fake_session.request.return_value = "I am a fake 400 error response"

# 3. When your real APIClient runs, it calls the method:
result = fake_session.request()

print(result)  # Outputs: "I am a fake 400 error response"

```

### Final Takeaway

`MagicMock` doesn't need to know how a real API client behaves. It just waits for your code to call a method (like `.request()`), creates that method instantly out of thin air, and hands back whatever fake data you told it to return.

---

## How to setup mock tests

> **Mock only what your code directly touches.** > You do not need to mock the entire internet or every feature of the `requests` library. You only look at what your `APIClient` code calls, and you simulate the specific response it expects to receive.

To know which methods and return values to mock, you must look directly at the **internal source code of the component you are testing** (the `APIClient`), not the external API documentation. You need to identify the exact line where your client talks to the outside world—usually `requests.get()` or `session.request()`—and make your mock mimic what a real server would return in that exact scenario.

### Step-by-Step Example: Testing a 429 Retry

Let's say you want to test that your client automatically retries when it hits an HTTP `429 Too Many Requests` status code.

#### Step 1: Look at your actual source code

First, open up your `api_client.py` and see how it is written. Imagine your actual client code looks like this:

```python
# Inside api_client.py
def get_data(self, url):
    # 1. Look at the method called: 'self.session.request'
    response = self.session.request("GET", url)
    
    # 2. Look at the attributes it checks: 'status_code' (integer) and 'headers' (dictionary)
    if response.status_code == 429:
        retry_after = int(response.headers.get("Retry-After", 1))
        time.sleep(retry_after)
        return self.session.request("GET", url) # Tries again!
        
    return response.json()

```

By reading this code, you now have your target blueprint. You know exactly what `MagicMock` needs to pretend to have.

#### Step 2: Determine the Methods and Return Values

Based on the code above, your mock needs to provide:

1. A **method** named `request`.
2. A **return value** for that request that has a `.status_code` property equal to `429`.
3. A `.headers` dictionary containing a `"Retry-After"` key.

#### Step 3: Write the Test Code

Now you configure your mock to match those exact requirements. Because you want to test a *retry*, your mock needs to return a `429` error on the first call, and a successful `200` on the second call. `MagicMock` lets you do this using `side_effect`.

```python
import unittest
from unittest.mock import MagicMock
from api_client import APIClient

class TestAPIRetry(unittest.TestCase):
    def test_retry_on_429_error(self):
        # 1. Setup the client
        client = APIClient(base_url="https://api.example.com")
        client.session = MagicMock()

        # 2. Build the fake 429 response
        mock_429_response = MagicMock()
        mock_429_response.status_code = 429
        mock_429_response.headers = {"Retry-After": "0"} # 0 seconds so tests run fast

        # 3. Build the fake successful 200 response for the retry attempt
        mock_200_response = MagicMock()
        mock_200_response.status_code = 200
        mock_200_response.json.return_value = {"data": "success"}

        # 4. Use 'side_effect' to give a list of consecutive return values
        # First call gets the 429, second call gets the 200
        client.session.request.side_effect = [mock_429_response, mock_200_response]

        # 5. Act & Assert
        result = client.get_data("/dashboard")
        self.assertEqual(result, {"data": "success"})
        
        # Verify it actually tried twice
        self.assertEqual(client.session.request.call_count, 2)

```

---

### Cheat Sheet: Common API Scenarios to Mock

When writing API tests, you can usually rely on this quick reference guide for what to mock based on what you want to test:

| If you want to test... | Look at these properties in your source code | What your mock return value needs |
| --- | --- | --- |
| **Successful Data Fetch** | `response.status_code`, `response.json()` | `.status_code = 200`<br>

<br>`.json.return_value = {"your": "data"}` |
| **Server Errors (500/400)** | `response.status_code`, `response.text` | `.status_code = 500`<br>

<br>`.text = "Internal Server Error"` |
| **Rate Limiting (429)** | `response.status_code`, `response.headers` | `.status_code = 429`<br>

<br>`.headers = {"Retry-After": "2"}` |
| **Network Timeout** | `self.session.request(...)` | Use `side_effect = requests.exceptions.Timeout` instead of a return value |

---

### Conclusion

To know what to mock, treat your source code like an open-book exam. Look at the properties (`.status_code`, `.headers`, `.json()`) that your client extracts from the network response, and configure your `MagicMock` to supply those exact pieces of data.

How does your actual `APIClient` handle retries or errors in its code?