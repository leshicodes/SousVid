"""
main.py -- FastAPI application entry point for SousVid.

Endpoints:
    GET  /          -> Web UI
    GET  /health    -> Liveness and readiness check
    GET  /share     -> Mobile share-sheet redirect (pre-fills the UI with ?url=)
    POST /extract   -> Run the recipe extraction pipeline"""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.errors import DownloadError, ExtractionError, SousVidError
from app.models import ExtractRequest, ExtractResponse
from app.pipeline import run_pipeline
from app.transcriber import get_model

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# Reduce noise from chatty third-party libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("faster_whisper").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Single-worker executor: Whisper is not thread-safe for concurrent inference,
# and video processing is CPU-bound anyway, so parallelism doesn't help here.
executor = ThreadPoolExecutor(max_workers=1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Pre-load the Whisper model at startup so the first request isn't slow."""
    logger.info("Pre-loading Whisper model...")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(executor, get_model)
    logger.info("Ready.")
    yield
    executor.shutdown(wait=False)


app = FastAPI(
    title="SousVid",
    description="Convert cooking videos from Instagram, TikTok, and YouTube into Mealie recipes.",
    version="1.0.0",
    lifespan=lifespan,
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
    from app.transcriber import _model as whisper_model
    return {
        "status": "ok",
        "whisper": {
            "loaded": whisper_model is not None,
            "model": settings.whisper_model,
            "device": settings.whisper_device,
        },
        "mealie": {
            "configured": settings.mealie_configured,
            "url": settings.mealie_url or None,
        },
        "llm": {
            "model": settings.openrouter_model,
        },
    }


@app.post("/extract", response_model=ExtractResponse, tags=["Recipe"])
async def extract(request: ExtractRequest):
    """
    Submit a cooking video URL and receive a structured recipe.

    The recipe is automatically pushed to Mealie if the Mealie toggle is enabled
    and Mealie is configured. Processing typically takes 30-120 seconds depending
    on video length and Whisper model size.
    """
    logger.info(
        f"Extract request: {request.url!r} "
        f"(push_to_mealie={request.push_to_mealie})"
    )
    try:
        loop = asyncio.get_event_loop()
        result: ExtractResponse = await loop.run_in_executor(
            executor,
            lambda: run_pipeline(request.url, push_to_mealie=request.push_to_mealie),
        )
        return result

    except DownloadError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except SousVidError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected pipeline error")
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred. Check the container logs for details.",
        )
