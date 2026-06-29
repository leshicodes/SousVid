"""
test_health.py -- Tests for the /health endpoint.

Uses FastAPI's TestClient (backed by httpx) so no running server is needed.
We just verify the response shape.
"""
import os


# Set required env var before the app imports config.
# Force Mealie to be unconfigured so the test assertions are deterministic
# regardless of any local .env file.
os.environ.setdefault("OPENROUTER_API_KEY", "test-key-for-tests")
os.environ["MEALIE_URL"] = ""
os.environ["MEALIE_API_TOKEN"] = ""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_health_returns_200():
    response = client.get("/health")
    assert response.status_code == 200


def test_health_response_shape():
    response = client.get("/health")
    data = response.json()
    assert data["status"] == "ok"
    assert "mealie" in data
    assert "llm" in data
    assert "queue" in data


def test_health_queue_section():
    response = client.get("/health")
    queue = response.json()["queue"]
    assert "broker" in queue


def test_health_mealie_section():
    response = client.get("/health")
    mealie = response.json()["mealie"]
    assert "configured" in mealie
    # In test environment MEALIE_URL is not set, so should be False
    assert mealie["configured"] is False


def test_health_llm_section():
    response = client.get("/health")
    llm = response.json()["llm"]
    assert "model" in llm
