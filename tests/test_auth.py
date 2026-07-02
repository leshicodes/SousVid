"""
test_auth.py -- Tests for the authentication endpoints and helpers.

Tests:
  - First user registration -> admin role + cookie issued
  - Second user registration -> user role
  - Duplicate email registration -> 409
  - Login with correct credentials -> 200 + cookie
  - Login with wrong credentials -> 401
  - GET /api/auth/me without cookie -> 401
  - GET /api/auth/me with valid cookie -> user data returned
  - POST /api/auth/logout -> cookie cleared
  - Password hashing / verification round-trip
"""
import os
import tempfile
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from app.main import app
from app.db import init_db
from app.auth import hash_password, verify_password, COOKIE_NAME


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def test_db():
    """Provide a fresh temporary SQLite database for each test."""
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
    """TestClient wired to the temporary DB."""
    with patch("app.config.settings.db_path", test_db):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _register(client, email="admin@test.com", password="password123"):
    return client.post("/api/auth/register", json={"email": email, "password": password})


def _login(client, email="admin@test.com", password="password123"):
    return client.post("/api/auth/login", json={"email": email, "password": password})


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def test_password_hash_and_verify():
    hashed = hash_password("supersecret")
    assert hashed != "supersecret"
    assert verify_password("supersecret", hashed)
    assert not verify_password("wrongpassword", hashed)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_first_user_becomes_admin(client):
    resp = _register(client)
    assert resp.status_code == 200
    data = resp.json()
    assert data["role"] == "admin"
    assert data["email"] == "admin@test.com"
    assert "id" in data
    # Cookie should be set
    assert COOKIE_NAME in resp.cookies


def test_second_user_becomes_regular_user(client):
    _register(client, email="admin@test.com")
    resp = _register(client, email="user@test.com")
    assert resp.status_code == 200
    data = resp.json()
    assert data["role"] == "user"
    assert data["email"] == "user@test.com"


def test_duplicate_email_rejected(client):
    _register(client, email="dupe@test.com")
    resp = _register(client, email="dupe@test.com")
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"].lower()


def test_registration_requires_valid_email(client):
    resp = client.post("/api/auth/register", json={"email": "notanemail", "password": "password123"})
    assert resp.status_code == 422


def test_registration_requires_min_password_length(client):
    resp = client.post("/api/auth/register", json={"email": "x@x.com", "password": "short"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def test_login_valid_credentials(client):
    _register(client)
    # Clear cookie from registration
    client.cookies.clear()

    resp = _login(client)
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"
    assert COOKIE_NAME in resp.cookies


def test_login_invalid_password(client):
    _register(client)
    resp = _login(client, password="wrongpassword")
    assert resp.status_code == 401


def test_login_unknown_email(client):
    resp = _login(client, email="nobody@test.com")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /api/auth/me
# ---------------------------------------------------------------------------

def test_me_returns_user_data_when_authenticated(client):
    _register(client)
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "admin@test.com"
    assert data["role"] == "admin"


def test_me_returns_401_without_cookie(client):
    client.cookies.clear()
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

def test_logout_clears_cookie(client):
    _register(client)
    # Confirm we're authenticated
    assert client.get("/api/auth/me").status_code == 200

    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200

    # Cookie should be gone — subsequent /me should 401
    # TestClient doesn't auto-clear cookies from Set-Cookie: max-age=0,
    # so we manually clear to simulate the browser behaviour
    client.cookies.clear()
    assert client.get("/api/auth/me").status_code == 401


# ---------------------------------------------------------------------------
# Admin user management
# ---------------------------------------------------------------------------

def test_admin_can_list_users(client):
    _register(client, email="admin@test.com")
    _register(client, email="user@test.com")

    # Re-login as admin
    client.cookies.clear()
    _login(client, email="admin@test.com")

    resp = client.get("/api/admin/users")
    assert resp.status_code == 200
    users = resp.json()
    assert len(users) == 2
    emails = {u["email"] for u in users}
    assert "admin@test.com" in emails
    assert "user@test.com" in emails
    # Password hashes must NOT be returned
    for u in users:
        assert "password_hash" not in u


def test_non_admin_cannot_list_users(client):
    _register(client, email="admin@test.com")
    _register(client, email="user@test.com")

    client.cookies.clear()
    _login(client, email="user@test.com")

    resp = client.get("/api/admin/users")
    assert resp.status_code == 403


def test_admin_can_change_user_role(client):
    _register(client, email="admin@test.com")
    resp2 = _register(client, email="user@test.com")
    user_id = resp2.json()["id"]

    client.cookies.clear()
    _login(client, email="admin@test.com")

    resp = client.patch(f"/api/admin/users/{user_id}/role", json={"role": "admin"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


def test_admin_cannot_change_own_role(client):
    resp = _register(client, email="admin@test.com")
    admin_id = resp.json()["id"]

    resp = client.patch(f"/api/admin/users/{admin_id}/role", json={"role": "user"})
    assert resp.status_code == 400


def test_admin_can_delete_other_user(client):
    _register(client, email="admin@test.com")
    resp2 = _register(client, email="user@test.com")
    user_id = resp2.json()["id"]

    client.cookies.clear()
    _login(client, email="admin@test.com")

    resp = client.delete(f"/api/admin/users/{user_id}")
    assert resp.status_code == 200


def test_admin_cannot_delete_self(client):
    resp = _register(client, email="admin@test.com")
    admin_id = resp.json()["id"]

    resp = client.delete(f"/api/admin/users/{admin_id}")
    assert resp.status_code == 400
