"""
auth.py -- Authentication helpers for SousVid.

Provides:
  - Password hashing / verification via bcrypt (direct, no passlib wrapper)
  - JWT creation / decoding via python-jose
  - JWT secret persistence (auto-generated on first run, stored in data dir)
  - FastAPI dependency injectors: get_current_user, require_admin
"""
import os
import secrets
import logging
from typing import Optional

import bcrypt
from fastapi import Request, HTTPException, Depends
from jose import JWTError, jwt

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Password hashing                                                             #
# --------------------------------------------------------------------------- #


def hash_password(password: str) -> str:
    """Return a bcrypt hash of the given plaintext password."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Return True if the plaintext password matches the stored hash."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# JWT secret management                                                        #
# --------------------------------------------------------------------------- #

_ALGORITHM = "HS256"
_secret_cache: Optional[str] = None


def _get_jwt_secret() -> str:
    """
    Return the JWT signing secret.

    Resolution order:
    1. Module-level cache (avoids repeated disk reads in one process lifetime).
    2. `settings.jwt_secret` if non-empty (set via environment / .env).
    3. A persisted file at <data_dir>/.jwt_secret; created on first run with a
       cryptographically random 64-hex-char secret.

    This design ensures the secret survives container restarts without requiring
    a manual .env entry, while still allowing explicit override via env var.
    """
    global _secret_cache
    if _secret_cache:
        return _secret_cache

    from app.config import settings

    if settings.jwt_secret:
        _secret_cache = settings.jwt_secret
        return _secret_cache

    # Determine persisted secret file location alongside the DB
    db_dir = os.path.dirname(settings.db_path) or "."
    secret_file = os.path.join(db_dir, ".jwt_secret")

    if os.path.exists(secret_file):
        with open(secret_file, "r") as f:
            _secret_cache = f.read().strip()
    else:
        os.makedirs(db_dir, exist_ok=True)
        _secret_cache = secrets.token_hex(32)
        with open(secret_file, "w") as f:
            f.write(_secret_cache)
        logger.info("Generated new JWT secret and persisted to %s", secret_file)

    return _secret_cache


# --------------------------------------------------------------------------- #
# Token creation / decoding                                                    #
# --------------------------------------------------------------------------- #

def create_access_token(user_id: str, role: str) -> str:
    """
    Create a signed JWT containing the user ID and role.
    Expiry is controlled by settings.jwt_expire_hours (default 168 = 7 days).
    """
    from datetime import datetime, timezone, timedelta
    from app.config import settings

    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    payload = {
        "sub": user_id,
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """
    Decode and verify a JWT. Returns the payload dict on success.
    Raises HTTPException 401 on any failure (expired, invalid signature, etc.).
    """
    try:
        payload = jwt.decode(token, _get_jwt_secret(), algorithms=[_ALGORITHM])
        return payload
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc


# --------------------------------------------------------------------------- #
# FastAPI dependency injectors                                                 #
# --------------------------------------------------------------------------- #

COOKIE_NAME = "sousvid_token"


def get_current_user(request: Request) -> dict:
    """
    FastAPI dependency: extract and validate the auth cookie.

    Reads the `sousvid_token` httpOnly cookie, decodes the JWT, fetches the
    user record from the database, and returns it.

    Raises:
        HTTP 401 — cookie missing, token invalid / expired, user not found.
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = decode_access_token(token)
    user_id: Optional[str] = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    from app.db import get_user_by_id
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """
    FastAPI dependency: ensure the caller has the 'admin' role.

    Raises:
        HTTP 403 — authenticated but not an admin.
    """
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
