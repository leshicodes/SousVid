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
def extract_task(self, url: str, push_to_mealie: bool) -> dict:
    from app.pipeline import run_pipeline

    self.update_state(state="PROGRESS", meta={"step": "downloading"})
    result = run_pipeline(url, push_to_mealie=push_to_mealie)
    return result.model_dump()
