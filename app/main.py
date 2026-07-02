"""
main.py -- FastAPI application entry point for SousVid.

Endpoints:
    GET  /                        -> Web UI
    GET  /health                  -> Liveness and readiness check
    GET  /share                   -> Mobile share-sheet redirect (pre-fills the UI with ?url=)

    POST /api/auth/register       -> Register a new user (first user becomes admin)
    POST /api/auth/login          -> Login and receive an httpOnly session cookie
    POST /api/auth/logout         -> Clear the session cookie
    GET  /api/auth/me             -> Return current authenticated user profile

    GET  /api/admin/users         -> [Admin] List all users
    PATCH /api/admin/users/{id}/role -> [Admin] Change a user's role
    DELETE /api/admin/users/{id}  -> [Admin] Delete a user

    GET    /api/services          -> List services (scoped by auth)
    POST   /api/services          -> Add a new service
    PUT    /api/services/{id}     -> Edit a service (ownership enforced)
    DELETE /api/services/{id}     -> Delete a service (ownership enforced)
    POST   /api/services/test     -> Test connection without saving

    GET    /api/history           -> List extraction history (scoped by auth)
    DELETE /api/history/{job_id}  -> Delete a history item (ownership enforced)
    POST   /api/history/migrate   -> Migrate localStorage history into DB

    POST /extract/submit          -> Enqueue a recipe extraction job
    GET  /jobs/{job_id}           -> Poll the status/result of a submitted job
"""
import logging
from typing import Optional, List

from pydantic import BaseModel, field_validator
from fastapi import FastAPI, HTTPException, Query, Request, Depends, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.models import ExtractRequest, JobStatusResponse, JobSubmitResponse
from app.worker import celery_app, extract_task
from app.auth import (
    COOKIE_NAME,
    get_current_user,
    require_admin,
    hash_password,
    verify_password,
    create_access_token,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# Reduce noise from chatty third-party libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("faster_whisper").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="SousVid",
    description="Convert cooking videos from Instagram, TikTok, and YouTube into recipes.",
    version="2.0.0",
)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.on_event("startup")
def on_startup():
    from app.db import init_db
    init_db()

# -- Pydantic Schemas --------------------------------------------------------

class RegisterSchema(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def email_must_not_be_empty(cls, v: str) -> str:
        v = v.strip().lower()
        if not v or "@" not in v:
            raise ValueError("A valid email address is required.")
        return v

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters.")
        return v


class LoginSchema(BaseModel):
    email: str
    password: str


class UserRoleSchema(BaseModel):
    role: str

    @field_validator("role")
    @classmethod
    def role_must_be_valid(cls, v: str) -> str:
        if v not in ("admin", "user"):
            raise ValueError("Role must be 'admin' or 'user'.")
        return v


class ServiceCreateSchema(BaseModel):
    name: str
    type: str
    url: str
    api_token: str
    is_active: bool = True
    ssl_verify: bool = True


class ServiceTestSchema(BaseModel):
    type: str
    url: str
    api_token: str
    ssl_verify: bool = True


class MigrateItem(BaseModel):
    job_id: str
    url: str
    recipeName: Optional[str] = None
    status: str
    step: Optional[str] = None
    result: Optional[dict] = None
    thumbnail: Optional[str] = None
    timestamp: int
    mealie_url: Optional[str] = None
    mealie_slug: Optional[str] = None


# -- API Routes: Auth --------------------------------------------------------

@app.post("/api/auth/register", tags=["Auth"])
def register(body: RegisterSchema, response: Response):
    """
    Register a new user. The very first user registered becomes an admin;
    all subsequent registrations default to the 'user' role.
    On first-admin bootstrap, all orphaned (pre-auth) extractions and services
    are retroactively assigned to this admin.
    """
    from app.db import (
        get_user_by_email, create_user, count_users,
        assign_orphaned_extractions, assign_orphaned_services,
    )

    if get_user_by_email(body.email):
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    is_first_user = count_users() == 0
    role = "admin" if is_first_user else "user"

    user = create_user(
        email=body.email,
        password_hash=hash_password(body.password),
        role=role,
    )

    # Retroactive migration: assign pre-auth data to the first admin
    if is_first_user:
        assign_orphaned_extractions(user["id"])
        assign_orphaned_services(user["id"])

    token = create_access_token(user["id"], user["role"])
    _set_auth_cookie(response, token)

    return {"id": user["id"], "email": user["email"], "role": user["role"]}


@app.post("/api/auth/login", tags=["Auth"])
def login(body: LoginSchema, response: Response):
    """Validate credentials and issue a session cookie."""
    from app.db import get_user_by_email

    user = get_user_by_email(body.email)
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    token = create_access_token(user["id"], user["role"])
    _set_auth_cookie(response, token)

    return {"id": user["id"], "email": user["email"], "role": user["role"]}


@app.post("/api/auth/logout", tags=["Auth"])
def logout(response: Response):
    """Clear the session cookie."""
    response.delete_cookie(
        key=COOKIE_NAME,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return {"detail": "Logged out"}


@app.get("/api/auth/me", tags=["Auth"])
def me(user: dict = Depends(get_current_user)):
    """Return the current authenticated user's profile."""
    return {"id": user["id"], "email": user["email"], "role": user["role"]}


def _set_auth_cookie(response: Response, token: str) -> None:
    """Helper to set the auth cookie with consistent security flags."""
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=settings.jwt_expire_hours * 3600,
    )


# -- API Routes: Admin User Management ---------------------------------------

@app.get("/api/admin/users", tags=["Admin"])
def list_users(admin: dict = Depends(require_admin)):
    """[Admin] List all registered users."""
    from app.db import get_all_users
    users = get_all_users()
    # Never return password hashes to the client
    return [{"id": u["id"], "email": u["email"], "role": u["role"], "created_at": u["created_at"]} for u in users]


@app.patch("/api/admin/users/{user_id}/role", tags=["Admin"])
def change_user_role(user_id: str, body: UserRoleSchema, admin: dict = Depends(require_admin)):
    """[Admin] Change a user's role. Admins cannot demote themselves."""
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="You cannot change your own role.")
    from app.db import update_user_role
    updated = update_user_role(user_id, body.role)
    if not updated:
        raise HTTPException(status_code=404, detail="User not found.")
    return {"id": updated["id"], "email": updated["email"], "role": updated["role"]}


@app.delete("/api/admin/users/{user_id}", tags=["Admin"])
def remove_user(user_id: str, admin: dict = Depends(require_admin)):
    """[Admin] Delete a user account. Admins cannot delete themselves."""
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")
    from app.db import delete_user
    deleted = delete_user(user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found.")
    return {"detail": "User deleted"}


# -- API Routes: Services ----------------------------------------------------

@app.get("/api/services", tags=["Services"])
def list_services(user: dict = Depends(get_current_user)):
    from app.db import get_services
    return get_services(user_id=user["id"], is_admin=(user["role"] == "admin"))


@app.post("/api/services", tags=["Services"])
def add_service(service: ServiceCreateSchema, user: dict = Depends(get_current_user)):
    from app.services import get_service_instance
    from app.db import create_service

    # Validate connection first
    temp_data = {
        "id": "temp",
        "name": service.name,
        "type": service.type,
        "url": service.url,
        "api_token": service.api_token,
        "ssl_verify": service.ssl_verify
    }
    try:
        srv = get_service_instance(temp_data)
        success, msg = srv.test_connection()
        if not success:
            raise HTTPException(status_code=400, detail=f"Connection test failed: {msg}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid service setup: {str(e)}")

    # Admins create global services (owner_id=None); regular users own their services
    owner_id = None if user["role"] == "admin" else user["id"]

    return create_service(
        name=service.name,
        type_=service.type,
        url=service.url,
        api_token=service.api_token,
        is_active=service.is_active,
        ssl_verify=service.ssl_verify,
        owner_id=owner_id,
    )


@app.put("/api/services/{service_id}", tags=["Services"])
def edit_service(service_id: str, service: ServiceCreateSchema, user: dict = Depends(get_current_user)):
    from app.services import get_service_instance
    from app.db import update_service, get_service

    existing = get_service(service_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Service not found")

    _assert_service_ownership(existing, user)

    # Validate connection
    temp_data = {
        "id": service_id,
        "name": service.name,
        "type": service.type,
        "url": service.url,
        "api_token": service.api_token,
        "ssl_verify": service.ssl_verify
    }
    try:
        srv = get_service_instance(temp_data)
        success, msg = srv.test_connection()
        if not success:
            raise HTTPException(status_code=400, detail=f"Connection test failed: {msg}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid service setup: {str(e)}")

    updated = update_service(
        service_id=service_id,
        name=service.name,
        type_=service.type,
        url=service.url,
        api_token=service.api_token,
        is_active=service.is_active,
        ssl_verify=service.ssl_verify,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Service not found")
    return updated


@app.delete("/api/services/{service_id}", tags=["Services"])
def remove_service(service_id: str, user: dict = Depends(get_current_user)):
    from app.db import delete_service, get_service

    existing = get_service(service_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Service not found")

    _assert_service_ownership(existing, user)

    deleted = delete_service(service_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Service not found")
    return {"detail": "Service deleted"}


@app.post("/api/services/test", tags=["Services"])
def test_service_connection(service: ServiceTestSchema, user: dict = Depends(get_current_user)):
    from app.services import get_service_instance
    temp_data = {
        "id": "temp",
        "name": "Test Connection",
        "type": service.type,
        "url": service.url,
        "api_token": service.api_token,
        "ssl_verify": service.ssl_verify
    }
    try:
        srv = get_service_instance(temp_data)
        success, msg = srv.test_connection()
        return {"success": success, "message": msg}
    except Exception as e:
        return {"success": False, "message": str(e)}


def _assert_service_ownership(service: dict, user: dict) -> None:
    """
    Raise HTTP 403 if the user does not own the service and is not an admin.
    Global services (owner_id IS NULL) can only be modified by admins.
    """
    if user["role"] == "admin":
        return
    if service.get("owner_id") != user["id"]:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to modify this service."
        )


# -- API Routes: History -----------------------------------------------------

@app.get("/api/history", tags=["History"])
def get_history(user: dict = Depends(get_current_user)):
    from app.db import get_extractions
    return get_extractions(user_id=user["id"], is_admin=(user["role"] == "admin"))


@app.delete("/api/history/{job_id}", tags=["History"])
def delete_history_item(job_id: str, user: dict = Depends(get_current_user)):
    from app.db import delete_extraction, get_extraction

    item = get_extraction(job_id)
    if not item:
        raise HTTPException(status_code=404, detail="Job not found in history")

    # Ownership check: users can only delete their own history; admins can delete any
    if user["role"] != "admin" and item.get("user_id") != user["id"]:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to delete this history item."
        )

    deleted = delete_extraction(job_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Job not found in history")
    return {"detail": "History item deleted"}


@app.post("/api/history/migrate", tags=["History"])
def migrate_history(items: List[MigrateItem], user: dict = Depends(get_current_user)):
    from app.db import get_extraction, save_extraction, update_extraction
    migrated_count = 0
    for item in items:
        existing = get_extraction(item.job_id)
        if not existing:
            # Save base record, tagging with current user's ID
            ts = item.timestamp // 1000 if item.timestamp > 2000000000 else item.timestamp
            save_extraction(
                job_id=item.job_id,
                url=item.url,
                status=item.status,
                step=item.step,
                timestamp=ts,
                user_id=user["id"],
            )
            # Save details if completed
            if item.status in ("done", "failed"):
                update_extraction(
                    job_id=item.job_id,
                    status=item.status,
                    step=item.step,
                    recipe_name=item.recipeName,
                    result=item.result,
                    thumbnail=item.thumbnail,
                    mealie_url=item.mealie_url,
                    mealie_slug=item.mealie_slug
                )
            migrated_count += 1
    return {"migrated": migrated_count}


# -- API Routes: Recipe Extraction -------------------------------------------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse("static/index.html")


@app.get("/share", include_in_schema=False)
def share(request: Request, url: str = Query(default=None)):
    if not url:
        raise HTTPException(
            status_code=400,
            detail="Missing required query parameter: url",
        )
    from urllib.parse import quote
    base = str(request.base_url).rstrip("/")
    return RedirectResponse(url=f"{base}/?url={quote(url, safe='')}", status_code=302)


@app.get("/health", tags=["Meta"])
def health():
    from app.db import get_services
    try:
        services_count = len(get_services())
    except Exception:
        services_count = 0

    return {
        "status": "ok",
        "services": {
            "count": services_count,
        },
        "llm": {
            "model": settings.openrouter_model,
        },
        "queue": {
            "broker": settings.redis_url,
        },
    }


@app.post("/extract/submit", response_model=JobSubmitResponse, tags=["Recipe"])
async def submit_extract(request_body: ExtractRequest, user: dict = Depends(get_current_user)):
    """Enqueue a recipe extraction job and return a job ID immediately."""
    clean_url = request_body.url.strip().strip("'\"")
    logger.info(f"Enqueuing extract job: {clean_url!r} (service_ids={request_body.service_ids}, user={user['id']})")

    # Start the Celery task
    task = extract_task.delay(clean_url, request_body.service_ids)

    # Save to history immediately as queued, scoped to the submitting user
    from app.db import save_extraction
    save_extraction(
        job_id=task.id,
        url=clean_url,
        status="queued",
        step="starting",
        user_id=user["id"],
    )

    return JobSubmitResponse(job_id=task.id)


@app.get("/jobs/{job_id}", response_model=JobStatusResponse, tags=["Recipe"])
async def job_status(job_id: str, user: dict = Depends(get_current_user)):
    """Poll the status of a previously submitted extraction job from the database."""
    from celery.result import AsyncResult
    from app.db import get_extraction

    job = get_extraction(job_id)
    if not job:
        result = AsyncResult(job_id, app=celery_app)
        if result.state == "PENDING":
            return JobStatusResponse(status="queued")
        if result.state == "STARTED":
            return JobStatusResponse(status="running", step="starting")
        if result.state == "PROGRESS":
            return JobStatusResponse(status="running", step=result.info.get("step"))
        if result.state == "SUCCESS":
            return JobStatusResponse(status="done", result=result.result)
        if result.state == "FAILURE":
            return JobStatusResponse(status="failed", error=str(result.info))
        return JobStatusResponse(status=result.state.lower())

    # Ownership check for job polling (users can only poll their own jobs, admins can poll any)
    if user["role"] != "admin" and job.get("user_id") and job["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    error_msg = None
    if job["status"] == "failed":
        result = AsyncResult(job_id, app=celery_app)
        if result.state == "FAILURE":
            error_msg = str(result.info)
        else:
            error_msg = "An error occurred during extraction."

    return JobStatusResponse(
        status=job["status"],
        step=job["step"],
        result=job["result"],
        error=error_msg
    )
