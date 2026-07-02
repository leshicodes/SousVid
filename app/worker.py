"""
worker.py -- Celery application and task definitions.
"""
from celery import Celery

from app.config import settings

celery_app = Celery(
    "sousvid",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    worker_concurrency=1,
    task_track_started=True,
    result_expires=3600,
)


@celery_app.task(bind=True, name="sousvid.extract")
def extract_task(self, url: str, service_ids: list[str]) -> dict:
    from app.pipeline import run_pipeline
    from app.db import update_extraction

    job_id = self.request.id
    logger_context = f"[Job {job_id}]"

    # Mark as running in SQLite
    update_extraction(job_id=job_id, status="running", step="downloading")
    self.update_state(state="PROGRESS", meta={"step": "downloading"})

    try:
        result = run_pipeline(url, service_ids=service_ids, job_id=job_id)
        
        # Get primary Mealie details for simple history links if available
        mealie_url = None
        mealie_slug = None
        for s_id, res in result.pushed_results.items():
            if res.get("success") and res.get("url"):
                mealie_url = res.get("url")
                mealie_slug = res.get("slug")
                break
        
        # Mark as done in SQLite
        update_extraction(
            job_id=job_id,
            status="done",
            step="complete",
            recipe_name=result.recipe.name,
            result=result.model_dump(),
            thumbnail=result.recipe_photo,
            mealie_url=mealie_url,
            mealie_slug=mealie_slug
        )
        return result.model_dump()
        
    except Exception as exc:
        # Mark as failed in SQLite
        update_extraction(
            job_id=job_id,
            status="failed",
            step="failed",
            recipe_name="Failed Extraction"
        )
        raise exc
