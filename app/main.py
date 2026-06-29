"""
main.py -- FastAPI application entry point for SousVid.

Endpoints:
    GET  /                -> Web UI
    GET  /health          -> Liveness and readiness check
    GET  /share           -> Mobile share-sheet redirect (pre-fills the UI with ?url=)
    POST /extract/submit  -> Enqueue a recipe extraction job and return a job ID
    GET  /jobs/{job_id}   -> Poll the status/result of a submitted job"""
import logging

from celery.result import AsyncResult
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.models import ExtractRequest, JobStatusResponse, JobSubmitResponse
from app.worker import celery_app, extract_task

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
    description="Convert cooking videos from Instagram, TikTok, and YouTube into Mealie recipes.",
    version="1.0.0",
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse("static/index.html")


@app.get("/share", include_in_schema=False)
def share(request: Request, url: str = Query(default=None)):
    """
    Mobile share-sheet endpoint.

    iOS and Android share sheets POST or GET a target URL via a configurable
    action URL.  Registering this app's share-sheet action as::

        https://<host>/share?url={url}

    will land here.  We redirect to the main UI with the URL pre-filled so the
    user can review and tap "Extract" -- no pipeline work is done here.

    Uses an absolute Location header (scheme + host included) to prevent mobile
    browsers from mangling the ``?url=`` param when resolving relative redirects.

    Returns 400 if no ``url`` query param is provided (misconfigured share sheet).
    """
    if not url:
        raise HTTPException(
            status_code=400,
            detail="Missing required query parameter: url",
        )
    from urllib.parse import quote
    # Build absolute redirect so Safari / Chrome on mobile don't drop the '?'
    # when resolving a relative-path Location header.
    base = str(request.base_url).rstrip("/")
    return RedirectResponse(url=f"{base}/?url={quote(url, safe='')}", status_code=302)


@app.get("/health", tags=["Meta"])
def health():
    """
    Liveness and readiness check.

    Reports the status of each subsystem so you can tell at a glance whether
    the app is fully operational or missing configuration.
    """
    return {
        "status": "ok",
        "mealie": {
            "configured": settings.mealie_configured,
            "url": settings.mealie_url or None,
        },
        "llm": {
            "model": settings.openrouter_model,
        },
        "queue": {
            "broker": settings.redis_url,
        },
    }


@app.post("/extract/submit", response_model=JobSubmitResponse, tags=["Recipe"])
async def submit_extract(request: ExtractRequest):
    """Enqueue a recipe extraction job and return a job ID immediately."""
    logger.info(
        f"Enqueuing extract job: {request.url!r} "
        f"(push_to_mealie={request.push_to_mealie})"
    )
    task = extract_task.delay(str(request.url), request.push_to_mealie)
    return JobSubmitResponse(job_id=task.id)


@app.get("/jobs/{job_id}", response_model=JobStatusResponse, tags=["Recipe"])
async def job_status(job_id: str):
    """
    Poll the status of a previously submitted extraction job.

    States: queued | running | done | failed
    """
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
