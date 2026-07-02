import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.db import init_db

@pytest.fixture
def test_db():
    """Temporary database for each test."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    with patch("app.config.settings.db_path", path):
        init_db()
        yield path
    try:
        os.unlink(path)
    except OSError:
        pass

@pytest.fixture
def authed_client(test_db):
    """TestClient with a registered and logged-in admin user."""
    with patch("app.config.settings.db_path", test_db):
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post("/api/auth/register", json={
            "email": "admin@test.com",
            "password": "password123"
        })
        assert resp.status_code == 200, f"Registration failed: {resp.text}"
        yield client

@patch("app.services.mealie.MealieService.test_connection")
def test_service_api_flow(mock_test_conn, authed_client):
    # Setup test connection mock
    mock_test_conn.return_value = (True, "Successfully authenticated")
    
    # 1. Test connection endpoint (success)
    test_payload = {
        "type": "mealie",
        "url": "http://mealie.local",
        "api_token": "dummy-token",
        "ssl_verify": True
    }
    resp = authed_client.post("/api/services/test", json=test_payload)
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "message": "Successfully authenticated"}
    
    # 2. Add service (success) - admin creates global service (owner_id=NULL)
    create_payload = {
        "name": "My Mealie",
        "type": "mealie",
        "url": "http://mealie.local",
        "api_token": "dummy-token",
        "is_active": True,
        "ssl_verify": True
    }
    resp = authed_client.post("/api/services", json=create_payload)
    assert resp.status_code == 200
    created = resp.json()
    assert created["name"] == "My Mealie"
    assert created["url"] == "http://mealie.local"
    assert "id" in created
    
    # 3. List services
    resp = authed_client.get("/api/services")
    assert resp.status_code == 200
    services = resp.json()
    assert len(services) == 1
    assert services[0]["id"] == created["id"]
    
    # 4. Edit service (success)
    edit_payload = {
        "name": "Updated Mealie",
        "type": "mealie",
        "url": "http://updated.local",
        "api_token": "new-token",
        "is_active": True,
        "ssl_verify": False
    }
    resp = authed_client.put(f"/api/services/{created['id']}", json=edit_payload)
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["name"] == "Updated Mealie"
    assert updated["url"] == "http://updated.local"
    assert updated["ssl_verify"] == 0
    
    # 5. Delete service
    resp = authed_client.delete(f"/api/services/{created['id']}")
    assert resp.status_code == 200
    assert resp.json() == {"detail": "Service deleted"}
    
    # 6. Verify empty list
    resp = authed_client.get("/api/services")
    assert len(resp.json()) == 0

@patch("app.services.mealie.MealieService.test_connection")
def test_add_service_connection_failure(mock_test_conn, authed_client):
    # Setup test connection mock to fail
    mock_test_conn.return_value = (False, "Connection failed: HTTP 401 Unauthorized")
    
    create_payload = {
        "name": "Failing Mealie",
        "type": "mealie",
        "url": "http://mealie.local",
        "api_token": "bad-token",
        "is_active": True,
        "ssl_verify": True
    }
    # Connectivity check failure should block save and return HTTP 400
    resp = authed_client.post("/api/services", json=create_payload)
    assert resp.status_code == 400
    assert "Connection test failed" in resp.json()["detail"]
    
    # Check that it wasn't saved
    resp = authed_client.get("/api/services")
    assert len(resp.json()) == 0

def test_services_require_auth():
    """Unauthenticated requests to service endpoints must return 401."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        with patch("app.config.settings.db_path", path):
            init_db()
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.get("/api/services")
            assert resp.status_code == 401
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
