from unittest.mock import MagicMock

# 1. Create the blank fake object
fake_session = MagicMock()

# 2. Create mocked 'request' method and tell what to return when it gets called
fake_session.request.return_value = "I am a fake 400 error response"

# 3. When your real APIClient runs, it calls the method:
result = fake_session.request()

print(result)  # Outputs: "I am a fake 400 error response"
