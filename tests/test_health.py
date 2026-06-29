"""
test_health.py -- Tests for the /health endpoint.

Uses FastAPI's TestClient (backed by httpx) so no running server is needed.
The Whisper model is not loaded in tests; we just verify the response shape.
"""
import os

import pytest

# Set required env var before the app imports config
os.environ.setdefault("OPENROUTER_API_KEY", "test-key-for-tests")

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
    assert "whisper" in data
    assert "mealie" in data
    assert "llm" in data


def test_health_whisper_section():
    response = client.get("/health")
    whisper = response.json()["whisper"]
    assert "loaded" in whisper
    assert "model" in whisper
    assert "device" in whisper


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
