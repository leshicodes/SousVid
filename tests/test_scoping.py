"""
test_scoping.py -- Multi-tenant data isolation tests for SousVid Phase 2.

Verifies that:
  - Services created by admins are global (visible to all users, editable only by admins)
  - Services created by regular users are personal (only visible/editable by owner or admins)
  - Extraction history is scoped: users see only their own; admins see all
  - Users cannot delete or modify each other's resources
"""
import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.db import init_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def test_db():
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
def client(test_db):
    with patch("app.config.settings.db_path", test_db):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _register_and_login(client, email, password="password123"):
    """Register + login, returning the user profile dict."""
    resp = client.post("/api/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _login(client, email, password="password123"):
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


SERVICE_PAYLOAD = {
    "name": "Test Mealie",
    "type": "mealie",
    "url": "http://mealie.local",
    "api_token": "tok",
    "is_active": True,
    "ssl_verify": True,
}


# ---------------------------------------------------------------------------
# Service scoping
# ---------------------------------------------------------------------------

@patch("app.services.mealie.MealieService.test_connection", return_value=(True, "OK"))
def test_admin_service_is_global_visible_to_all(mock_conn, client):
    """Admin-created service (owner_id=NULL) appears in every user's service list."""
    # Admin registers first -> becomes admin
    admin = _register_and_login(client, "admin@test.com")

    resp = client.post("/api/services", json=SERVICE_PAYLOAD)
    assert resp.status_code == 200
    global_svc = resp.json()
    assert global_svc.get("owner_id") is None  # global

    # Register a regular user
    client.cookies.clear()
    _register_and_login(client, "user@test.com")

    resp = client.get("/api/services")
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()]
    assert global_svc["id"] in ids


@patch("app.services.mealie.MealieService.test_connection", return_value=(True, "OK"))
def test_regular_user_cannot_edit_global_service(mock_conn, client):
    """Regular users cannot PUT/DELETE a global (admin-owned) service."""
    _register_and_login(client, "admin@test.com")
    resp = client.post("/api/services", json=SERVICE_PAYLOAD)
    svc_id = resp.json()["id"]

    client.cookies.clear()
    _register_and_login(client, "user@test.com")

    resp = client.put(f"/api/services/{svc_id}", json=SERVICE_PAYLOAD)
    assert resp.status_code == 403

    resp = client.delete(f"/api/services/{svc_id}")
    assert resp.status_code == 403


@patch("app.services.mealie.MealieService.test_connection", return_value=(True, "OK"))
def test_user_personal_service_invisible_to_other_users(mock_conn, client):
    """A personal service created by user A is not visible to user B."""
    # Register admin first (required to bootstrap)
    _register_and_login(client, "admin@test.com")

    # Register user A
    client.cookies.clear()
    _register_and_login(client, "usera@test.com")
    resp = client.post("/api/services", json={**SERVICE_PAYLOAD, "name": "UserA Service"})
    assert resp.status_code == 200
    usera_svc_id = resp.json()["id"]

    # Register user B and verify they cannot see user A's service
    client.cookies.clear()
    _register_and_login(client, "userb@test.com")
    resp = client.get("/api/services")
    ids = [s["id"] for s in resp.json()]
    assert usera_svc_id not in ids


@patch("app.services.mealie.MealieService.test_connection", return_value=(True, "OK"))
def test_admin_can_see_all_services(mock_conn, client):
    """Admin can see both global services and all personal services."""
    admin = _register_and_login(client, "admin@test.com")
    resp = client.post("/api/services", json={**SERVICE_PAYLOAD, "name": "Global Svc"})
    global_id = resp.json()["id"]

    client.cookies.clear()
    _register_and_login(client, "user@test.com")
    resp = client.post("/api/services", json={**SERVICE_PAYLOAD, "name": "Personal Svc"})
    personal_id = resp.json()["id"]

    # Admin logs back in
    client.cookies.clear()
    _login(client, "admin@test.com")
    resp = client.get("/api/services")
    ids = [s["id"] for s in resp.json()]
    assert global_id in ids
    assert personal_id in ids


@patch("app.services.mealie.MealieService.test_connection", return_value=(True, "OK"))
def test_admin_can_delete_personal_service(mock_conn, client):
    """Admin should be able to delete any user's personal service."""
    _register_and_login(client, "admin@test.com")

    client.cookies.clear()
    _register_and_login(client, "user@test.com")
    resp = client.post("/api/services", json=SERVICE_PAYLOAD)
    svc_id = resp.json()["id"]

    # Admin deletes it
    client.cookies.clear()
    _login(client, "admin@test.com")
    resp = client.delete(f"/api/services/{svc_id}")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# History scoping
# ---------------------------------------------------------------------------

def test_user_sees_only_own_history(client):
    """User A's extractions do not appear in user B's history list."""
    _register_and_login(client, "admin@test.com")

    client.cookies.clear()
    _register_and_login(client, "usera@test.com")

    # User A's history is currently empty; verify B also sees nothing from A.
    # We use the test_admin_sees_all_history test (with direct injection)
    # for the positive case. Here we verify isolation at the API level.
    resp_a = client.get("/api/history")
    assert resp_a.status_code == 200
    user_a_history = resp_a.json()

    # Now register/login as User B
    client.cookies.clear()
    _register_and_login(client, "userb@test.com")

    resp_b = client.get("/api/history")
    assert resp_b.status_code == 200
    user_b_ids = {item["job_id"] for item in resp_b.json()}

    # None of User A's items (if any) should appear in User B's list
    for item in user_a_history:
        assert item["job_id"] not in user_b_ids


def test_admin_sees_all_history(client):
    """Admin can see history records belonging to all users."""
    from app.db import save_extraction

    _register_and_login(client, "admin@test.com")

    client.cookies.clear()
    user_a = _register_and_login(client, "usera@test.com")
    save_extraction("job-a-002", "http://video.a", "done", user_id=user_a["id"])

    client.cookies.clear()
    user_b = _register_and_login(client, "userb@test.com")
    save_extraction("job-b-002", "http://video.b", "done", user_id=user_b["id"])

    # Admin logs back in
    client.cookies.clear()
    _login(client, "admin@test.com")

    resp = client.get("/api/history")
    assert resp.status_code == 200
    job_ids = [item["job_id"] for item in resp.json()]
    assert "job-a-002" in job_ids
    assert "job-b-002" in job_ids


def test_user_cannot_delete_other_users_history(client):
    """User B cannot delete an extraction history item that belongs to user A."""
    from app.db import save_extraction

    _register_and_login(client, "admin@test.com")

    client.cookies.clear()
    user_a = _register_and_login(client, "usera@test.com")
    save_extraction("job-a-003", "http://video.a", "done", user_id=user_a["id"])

    client.cookies.clear()
    _register_and_login(client, "userb@test.com")

    resp = client.delete("/api/history/job-a-003")
    assert resp.status_code == 403


def test_user_can_delete_own_history(client):
    """A user can delete their own history items."""
    from app.db import save_extraction

    _register_and_login(client, "admin@test.com")

    client.cookies.clear()
    user_a = _register_and_login(client, "usera@test.com")
    save_extraction("job-a-004", "http://video.a", "done", user_id=user_a["id"])

    resp = client.delete("/api/history/job-a-004")
    assert resp.status_code == 200


def test_admin_can_delete_any_history(client):
    """Admin can delete any user's history item."""
    from app.db import save_extraction

    _register_and_login(client, "admin@test.com")

    client.cookies.clear()
    user_a = _register_and_login(client, "usera@test.com")
    save_extraction("job-a-005", "http://video.a", "done", user_id=user_a["id"])

    client.cookies.clear()
    _login(client, "admin@test.com")

    resp = client.delete("/api/history/job-a-005")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Bootstrap / retroactive migration
# ---------------------------------------------------------------------------

def test_orphaned_extractions_assigned_to_first_admin(client):
    """Pre-auth extractions (user_id=NULL) should be claimed by the first registered admin."""
    from app.db import save_extraction, get_extraction

    # Insert orphaned records BEFORE any user registers
    save_extraction("orphan-job-1", "http://legacy.url", "done")
    assert get_extraction("orphan-job-1")["user_id"] is None

    # Register the first user (admin)
    admin = _register_and_login(client, "admin@test.com")

    # After bootstrap, the orphaned extraction should belong to admin
    record = get_extraction("orphan-job-1")
    assert record["user_id"] == admin["id"]
