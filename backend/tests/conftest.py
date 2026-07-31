from pathlib import Path
import re

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
BASE_URL = (frontend_env.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
if not BASE_URL:
    raise RuntimeError("REACT_APP_BACKEND_URL is missing from /app/frontend/.env")


@pytest.fixture(scope="session")
def credentials():
    """Read shared credentials without hardcoding them in tests."""
    path = Path("/app/memory/test_credentials.md")
    if not path.exists():
        pytest.skip("Missing /app/memory/test_credentials.md")
    content = path.read_text(encoding="utf-8")
    email = re.search(
        r"(?im)^\s*(?:[-*]\s*)?(?:\*\*)?email(?:\*\*)?\s*:\s*`?([^`\s]+)",
        content,
    )
    password = re.search(
        r"(?im)^\s*(?:[-*]\s*)?(?:\*\*)?password(?:\*\*)?\s*:\s*`?([^`\s]+)",
        content,
    )
    if not email or not password:
        pytest.skip("Admin credentials are not present in test_credentials.md")
    return {"email": email.group(1), "password": password.group(1)}


@pytest.fixture(scope="session")
def authenticated_client(credentials):
    """Login once and retain the production-facing secure auth cookies."""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    response = session.post(f"{BASE_URL}/api/auth/login", json=credentials, timeout=20)
    if response.status_code != 200:
        pytest.fail(f"Authentication failed: {response.status_code} {response.text[:300]}")
    return session


@pytest.fixture
def created_intake_ids():
    return []
