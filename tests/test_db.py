import os
import tempfile
import pytest
from unittest.mock import patch
from app.db import (
    init_db,
    # User functions
    create_user,
    get_user_by_id,
    get_user_by_email,
    get_all_users,
    update_user_role,
    delete_user,
    count_users,
    assign_orphaned_extractions,
    # Service functions
    create_service,
    get_service,
    get_services,
    update_service,
    delete_service,
    # Extraction functions
    save_extraction,
    get_extraction,
    get_extractions,
    update_extraction,
    delete_extraction,
)

@pytest.fixture
def temp_db():
    # Create a temporary file for the database
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    
    with patch("app.config.settings.db_path", path):
        init_db()
        yield path
        
    try:
        os.unlink(path)
    except OSError:
        pass

# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

def test_user_crud(temp_db):
    # Initially no users
    assert count_users() == 0

    # Create first user (should be admin per convention)
    u = create_user(email="admin@test.com", password_hash="hashed", role="admin")
    assert u["email"] == "admin@test.com"
    assert u["role"] == "admin"
    assert "id" in u
    assert count_users() == 1

    # Email normalisation (lowercase + strip)
    u2 = create_user(email="  User@Test.Com  ", password_hash="hashed2", role="user")
    assert u2["email"] == "user@test.com"
    assert count_users() == 2

    # get_user_by_id
    fetched = get_user_by_id(u["id"])
    assert fetched is not None
    assert fetched["email"] == "admin@test.com"

    # get_user_by_email (case-insensitive)
    fetched_by_email = get_user_by_email("ADMIN@test.com")
    assert fetched_by_email is not None
    assert fetched_by_email["id"] == u["id"]

    # get_all_users returns both
    all_users = get_all_users()
    assert len(all_users) == 2

    # update_user_role
    updated = update_user_role(u2["id"], "admin")
    assert updated["role"] == "admin"

    # delete_user
    assert delete_user(u2["id"]) is True
    assert get_user_by_id(u2["id"]) is None
    assert count_users() == 1

    # Deleting non-existent user returns False
    assert delete_user("nonexistent-id") is False


def test_assign_orphaned_extractions(temp_db):
    # Create extractions without a user_id
    save_extraction("orphan-1", "http://v1.com", "done")
    save_extraction("orphan-2", "http://v2.com", "done")

    assert get_extraction("orphan-1")["user_id"] is None

    user = create_user("admin@test.com", "hash", "admin")
    rows_updated = assign_orphaned_extractions(user["id"])
    assert rows_updated == 2

    assert get_extraction("orphan-1")["user_id"] == user["id"]
    assert get_extraction("orphan-2")["user_id"] == user["id"]


# ---------------------------------------------------------------------------
# Service CRUD
# ---------------------------------------------------------------------------

def test_service_crud(temp_db):
    # Test Create
    srv = create_service(
        name="Test Service",
        type_="mealie",
        url="http://test.local",
        api_token="token123",
        is_active=True,
        ssl_verify=True
    )
    assert srv["name"] == "Test Service"
    assert srv["type"] == "mealie"
    assert srv["url"] == "http://test.local"
    assert srv["api_token"] == "token123"
    assert srv["is_active"] == 1
    assert srv["ssl_verify"] == 1
    assert srv["owner_id"] is None  # global by default
    
    # Test Get
    fetched = get_service(srv["id"])
    assert fetched is not None
    assert fetched["name"] == "Test Service"
    
    # Test List (no auth scoping -- admin view)
    all_srvs = get_services()
    assert len(all_srvs) == 1
    assert all_srvs[0]["id"] == srv["id"]
    
    # Test scoped listing -- user should see global services
    user = create_user("user@test.com", "hash", "user")
    user_srvs = get_services(user_id=user["id"], is_admin=False)
    assert len(user_srvs) == 1  # global service is visible

    # Personal service only visible to owner
    personal = create_service(
        name="Personal Svc",
        type_="mealie",
        url="http://personal.local",
        api_token="tok",
        owner_id=user["id"],
    )
    user_srvs = get_services(user_id=user["id"], is_admin=False)
    assert len(user_srvs) == 2  # global + personal

    # Another user sees only global
    other_user = create_user("other@test.com", "hash", "user")
    other_srvs = get_services(user_id=other_user["id"], is_admin=False)
    assert len(other_srvs) == 1  # only global

    # Test Update
    updated = update_service(
        service_id=srv["id"],
        name="Updated Name",
        type_="mealie",
        url="http://updated.local",
        api_token="newtoken",
        is_active=False,
        ssl_verify=False
    )
    assert updated["name"] == "Updated Name"
    assert updated["url"] == "http://updated.local"
    assert updated["api_token"] == "newtoken"
    assert updated["is_active"] == 0
    assert updated["ssl_verify"] == 0
    
    # Test Delete
    deleted = delete_service(srv["id"])
    assert deleted is True
    assert get_service(srv["id"]) is None


# ---------------------------------------------------------------------------
# Extraction CRUD
# ---------------------------------------------------------------------------

def test_extraction_crud(temp_db):
    user = create_user("test@test.com", "hash", "user")

    # Test Save (with user_id)
    ext = save_extraction(
        job_id="job-123",
        url="http://video.url",
        status="queued",
        step="starting",
        user_id=user["id"],
    )
    assert ext["job_id"] == "job-123"
    assert ext["url"] == "http://video.url"
    assert ext["status"] == "queued"
    assert ext["step"] == "starting"
    assert ext["user_id"] == user["id"]
    
    # Test Get
    fetched = get_extraction("job-123")
    assert fetched is not None
    assert fetched["status"] == "queued"
    
    # Test scoped listing
    scoped = get_extractions(user_id=user["id"], is_admin=False)
    assert len(scoped) == 1

    # Another user sees nothing
    other = create_user("other@test.com", "hash", "user")
    other_items = get_extractions(user_id=other["id"], is_admin=False)
    assert len(other_items) == 0

    # Admin sees all
    admin_items = get_extractions(user_id=other["id"], is_admin=True)
    assert len(admin_items) == 1

    # Test Update
    result_data = {"recipe": {"name": "Pasta"}}
    updated = update_extraction(
        job_id="job-123",
        status="done",
        step="complete",
        recipe_name="Pasta",
        result=result_data,
        thumbnail="base64str",
        mealie_url="http://mealie/r/pasta",
        mealie_slug="pasta"
    )
    assert updated["status"] == "done"
    assert updated["step"] == "complete"
    assert updated["recipe_name"] == "Pasta"
    assert updated["result"] == result_data
    assert updated["thumbnail"] == "base64str"
    assert updated["mealie_url"] == "http://mealie/r/pasta"
    assert updated["mealie_slug"] == "pasta"
    
    # Test Delete
    deleted = delete_extraction("job-123")
    assert deleted is True
    assert get_extraction("job-123") is None
