import os
import sqlite3
import json
import uuid
import time
from typing import Optional, List, Any

def get_db_connection() -> sqlite3.Connection:
    """Open a connection to the SQLite database and enable WAL mode for concurrency."""
    from app.config import settings
    db_dir = os.path.dirname(settings.db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    
    conn = sqlite3.connect(settings.db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def init_db() -> None:
    """Initialize the database tables if they do not exist, then run migrations."""
    with get_db_connection() as conn:
        # -- Users table (Phase 2) -------------------------------------------
        conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at INTEGER NOT NULL
        )
        """)

        # -- Services table --------------------------------------------------
        conn.execute("""
        CREATE TABLE IF NOT EXISTS services (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            url TEXT NOT NULL,
            api_token TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            ssl_verify INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL
        )
        """)

        # -- Extractions table -----------------------------------------------
        conn.execute("""
        CREATE TABLE IF NOT EXISTS extractions (
            job_id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            recipe_name TEXT,
            status TEXT NOT NULL,
            step TEXT,
            result TEXT,
            thumbnail TEXT,
            timestamp INTEGER NOT NULL,
            mealie_url TEXT,
            mealie_slug TEXT
        )
        """)
        conn.commit()

    # Run non-destructive column additions after tables are guaranteed to exist
    _run_migrations()


def _run_migrations() -> None:
    """Add new columns to existing tables without dropping any data."""
    with get_db_connection() as conn:
        # Add owner_id to services if missing
        existing_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(services)").fetchall()
        }
        if "owner_id" not in existing_cols:
            conn.execute("ALTER TABLE services ADD COLUMN owner_id TEXT REFERENCES users(id)")

        # Add user_id to extractions if missing
        existing_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(extractions)").fetchall()
        }
        if "user_id" not in existing_cols:
            conn.execute("ALTER TABLE extractions ADD COLUMN user_id TEXT REFERENCES users(id)")

        conn.commit()


# --- Users CRUD -------------------------------------------------------------

def count_users() -> int:
    """Return total number of registered users."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
        return row[0] if row else 0


def create_user(email: str, password_hash: str, role: str = "user") -> dict:
    """Create a new user record and return it."""
    user_id = str(uuid.uuid4())
    created_at = int(time.time())
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO users (id, email, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, email.lower().strip(), password_hash, role, created_at)
        )
        conn.commit()
    return get_user_by_id(user_id)


def get_user_by_id(user_id: str) -> Optional[dict]:
    """Retrieve a user by their ID."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_email(email: str) -> Optional[dict]:
    """Retrieve a user by email (case-insensitive)."""
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
        ).fetchone()
        return dict(row) if row else None


def get_all_users() -> List[dict]:
    """Retrieve all users ordered by registration date."""
    with get_db_connection() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY created_at ASC").fetchall()
        return [dict(row) for row in rows]


def update_user_role(user_id: str, role: str) -> Optional[dict]:
    """Update a user's role. Returns updated user or None if not found."""
    with get_db_connection() as conn:
        cursor = conn.execute(
            "UPDATE users SET role = ? WHERE id = ?", (role, user_id)
        )
        conn.commit()
        if cursor.rowcount == 0:
            return None
    return get_user_by_id(user_id)


def delete_user(user_id: str) -> bool:
    """Delete a user by ID. Returns True if deleted."""
    with get_db_connection() as conn:
        cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        return cursor.rowcount > 0


def assign_orphaned_extractions(user_id: str) -> int:
    """
    Assign all extractions without a user_id to the given user.
    Called during first-admin bootstrap to retroactively claim pre-auth history.
    Returns number of rows updated.
    """
    with get_db_connection() as conn:
        cursor = conn.execute(
            "UPDATE extractions SET user_id = ? WHERE user_id IS NULL", (user_id,)
        )
        conn.commit()
        return cursor.rowcount


def assign_orphaned_services(user_id: str) -> int:
    """
    Assign all services without an owner_id to the given user (as global/admin).
    Returns number of rows updated.
    """
    with get_db_connection() as conn:
        cursor = conn.execute(
            "UPDATE services SET owner_id = ? WHERE owner_id IS NULL", (user_id,)
        )
        conn.commit()
        return cursor.rowcount


# --- Services CRUD ---

def get_services(user_id: Optional[str] = None, is_admin: bool = False) -> List[dict]:
    """
    Retrieve services visible to the caller.
    - Admins see all services.
    - Regular users see global services (owner_id IS NULL) + their own personal services.
    - If user_id is None (unauthenticated / legacy call), return all services.
    """
    with get_db_connection() as conn:
        if user_id is None or is_admin:
            rows = conn.execute("SELECT * FROM services ORDER BY created_at ASC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM services WHERE owner_id IS NULL OR owner_id = ? ORDER BY created_at ASC",
                (user_id,)
            ).fetchall()
        return [dict(row) for row in rows]

def get_service(service_id: str) -> Optional[dict]:
    """Retrieve a single service by ID."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM services WHERE id = ?", (service_id,)).fetchone()
        return dict(row) if row else None

def get_active_services() -> List[dict]:
    """Retrieve all active services (for pipeline use, no scoping)."""
    with get_db_connection() as conn:
        rows = conn.execute("SELECT * FROM services WHERE is_active = 1 ORDER BY created_at ASC").fetchall()
        return [dict(row) for row in rows]

def create_service(
    name: str,
    type_: str,
    url: str,
    api_token: str,
    is_active: bool = True,
    ssl_verify: bool = True,
    owner_id: Optional[str] = None,
) -> dict:
    """
    Create a new service configuration.
    owner_id=None means global (admin-created, visible to all users).
    """
    service_id = str(uuid.uuid4())
    created_at = int(time.time())
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO services (id, name, type, url, api_token, is_active, ssl_verify, created_at, owner_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (service_id, name, type_, url, api_token, 1 if is_active else 0, 1 if ssl_verify else 0, created_at, owner_id)
        )
        conn.commit()
    return get_service(service_id)

def update_service(service_id: str, name: str, type_: str, url: str, api_token: str, is_active: bool, ssl_verify: bool) -> Optional[dict]:
    """Update an existing service configuration."""
    with get_db_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE services 
            SET name = ?, type = ?, url = ?, api_token = ?, is_active = ?, ssl_verify = ?
            WHERE id = ?
            """,
            (name, type_, url, api_token, 1 if is_active else 0, 1 if ssl_verify else 0, service_id)
        )
        conn.commit()
        if cursor.rowcount == 0:
            return None
    return get_service(service_id)

def delete_service(service_id: str) -> bool:
    """Delete a service configuration by ID."""
    with get_db_connection() as conn:
        cursor = conn.execute("DELETE FROM services WHERE id = ?", (service_id,))
        conn.commit()
        return cursor.rowcount > 0

# --- Extractions History CRUD ---

def get_extractions(
    user_id: Optional[str] = None,
    is_admin: bool = False,
    limit: int = 50
) -> List[dict]:
    """
    Retrieve extraction history, scoped by user.
    - Admins see all extractions.
    - Regular users see only their own.
    - If user_id is None, return all (legacy / unauthenticated context).
    """
    with get_db_connection() as conn:
        if user_id is None or is_admin:
            rows = conn.execute(
                "SELECT * FROM extractions ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM extractions WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()
        
        result_list = []
        for row in rows:
            d = dict(row)
            if d.get("result"):
                try:
                    d["result"] = json.loads(d["result"])
                except Exception:
                    pass
            result_list.append(d)
        return result_list

def get_extraction(job_id: str) -> Optional[dict]:
    """Retrieve a single extraction history item by job ID."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM extractions WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("result"):
            try:
                d["result"] = json.loads(d["result"])
            except Exception:
                pass
        return d

def save_extraction(
    job_id: str,
    url: str,
    status: str,
    step: Optional[str] = None,
    timestamp: Optional[int] = None,
    user_id: Optional[str] = None,
) -> dict:
    """Create a new extraction job history record."""
    if timestamp is None:
        timestamp = int(time.time())
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO extractions (job_id, url, status, step, timestamp, user_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (job_id, url, status, step, timestamp, user_id)
        )
        conn.commit()
    return get_extraction(job_id)

def update_extraction(
    job_id: str,
    status: str,
    step: Optional[str] = None,
    recipe_name: Optional[str] = None,
    result: Optional[dict] = None,
    thumbnail: Optional[str] = None,
    mealie_url: Optional[str] = None,
    mealie_slug: Optional[str] = None
) -> Optional[dict]:
    """Update properties of an existing extraction record."""
    with get_db_connection() as conn:
        fields = {"status": status}
        if step is not None:
            fields["step"] = step
        if recipe_name is not None:
            fields["recipe_name"] = recipe_name
        if result is not None:
            fields["result"] = json.dumps(result)
        if thumbnail is not None:
            fields["thumbnail"] = thumbnail
        if mealie_url is not None:
            fields["mealie_url"] = mealie_url
        if mealie_slug is not None:
            fields["mealie_slug"] = mealie_slug

        query_parts = []
        params = []
        for key, val in fields.items():
            query_parts.append(f"{key} = ?")
            params.append(val)
        
        params.append(job_id)
        sql = f"UPDATE extractions SET {', '.join(query_parts)} WHERE job_id = ?"
        
        cursor = conn.execute(sql, params)
        conn.commit()
        if cursor.rowcount == 0:
            return None
    return get_extraction(job_id)

def delete_extraction(job_id: str) -> bool:
    """Delete an extraction history record."""
    with get_db_connection() as conn:
        cursor = conn.execute("DELETE FROM extractions WHERE job_id = ?", (job_id,))
        conn.commit()
        return cursor.rowcount > 0
