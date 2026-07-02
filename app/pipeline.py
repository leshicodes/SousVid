"""
pipeline.py -- Orchestrates the full video-to-recipe workflow.

The pipeline runs these steps in order:
    1. Download the video via yt-dlp (also captures the post description/caption)
    2. Extract key frames with ffmpeg
    3. Transcribe audio with Whisper -- or skip if the description already has the recipe
    4. Extract the structured recipe via the LLM
    5. Push to Mealie (optional, user-controlled)
    6. Clean up temp files

The description-first shortcut in step 3 is the main cost-saving optimization:
many creators paste their full recipe in the video caption, so we check that
before spending time and money on Whisper.
"""
import logging
import os
import shutil

from app.downloader import download_video
from app.frame_extractor import extract_frames
from app.llm import extract_recipe
from app.models import ExtractResponse
from app.transcriber import transcribe

logger = logging.getLogger(__name__)


def _description_looks_like_recipe(text: str | None) -> bool:
    if not text:
        return False

    text_lower = text.lower()
    has_multiple_lines = "\n" in text
    has_ingredient_keyword = "ingredient" in text_lower

    measurement_words = [
        "tbsp", "tsp", "cup", "gram", "oz", "tablespoon",
        "teaspoon", "ml", "lb", "kg",
    ]
    # The trailing-'s' check covers plurals (e.g. "cups" / "grams") without
    # double-counting them against their singular form.
    measurement_count = sum(
        1 for word in measurement_words
        if f" {word}" in text_lower or f"{word} " in text_lower or f"{word}s" in text_lower
    )

    return has_multiple_lines and (has_ingredient_keyword or measurement_count >= 2)


def _update_step(job_id: str | None, step: str):
    if job_id:
        try:
            from app.db import update_extraction
            update_extraction(job_id=job_id, status="running", step=step)
        except Exception as e:
            logger.warning(f"Failed to update step '{step}' in DB: {e}")


def run_pipeline(url: str, service_ids: list[str] = None, job_id: str = None) -> ExtractResponse:
    """
    Run the full video-to-recipe pipeline and return the result.

    Args:
        url:          The video URL (Instagram, TikTok, YouTube Shorts, etc.).
        service_ids:  List of service IDs to push the extracted recipe to.
        job_id:       Task / Job ID for database updates.

    Returns:
        ExtractResponse with the recipe, push details, and debug transcript.

    Raises:
        SousVidError: on download, transcription, or extraction failure.
        Any unexpected exception propagates to the API layer.
    """
    video_path: str | None = None
    video_dir: str | None = None

    try:
        # 1. Download ---------------------------------------------------------
        _update_step(job_id, "downloading")
        video_path, description = download_video(url)
        video_dir = os.path.dirname(video_path)

        # 2. Frames -----------------------------------------------------------
        logger.info("Extracting frames...")
        _update_step(job_id, "frames")
        frames = extract_frames(video_path)

        # 3. Transcribe (or skip) ---------------------------------------------
        _update_step(job_id, "transcribing")
        if _description_looks_like_recipe(description):
            logger.info("Caption looks like a full recipe -- skipping Whisper.")
            transcript = f"Creator's caption:\n{description}"
        else:
            logger.info("Transcribing audio...")
            audio_text = transcribe(video_path)
            transcript = (
                f"Video caption:\n{description or '(none)'}\n\n"
                f"Audio transcript:\n{audio_text}"
            )

        # 4. LLM extraction ---------------------------------------------------
        logger.info("Extracting recipe via LLM...")
        _update_step(job_id, "llm")
        recipe = extract_recipe(transcript, frames)

        # 4.5. Photo Selection Retry (if initial frames yielded no photo) ------
        if recipe.recipe_photo_idx is None:
            logger.info("No recipe photo selected from the first batch of frames. Retrying with a shifted batch...")
            try:
                # Extract new frames targeting the first and last 25% of the video
                second_batch_frames = extract_frames(video_path, segment="hook_and_plating")
                if second_batch_frames:
                    from app.llm import select_recipe_photo
                    new_idx = select_recipe_photo(recipe.name, second_batch_frames)
                    if new_idx is not None:
                        logger.info(f"Successfully selected recipe photo from shifted batch at index {new_idx}.")
                        recipe.recipe_photo_idx = new_idx
                        # Swap out the frames list used for Mealie upload and UI display
                        frames = second_batch_frames
            except Exception as retry_exc:
                logger.warning(f"Photo selection retry failed (non-fatal): {retry_exc}")

        # 5. Push to configured services ---------------------------------------
        pushed_results = {}
        if service_ids:
            logger.info("Pushing to services...")
            _update_step(job_id, "mealie")
            from app.db import get_service
            from app.services import get_service_instance
            for s_id in service_ids:
                try:
                    s_data = get_service(s_id)
                    if not s_data:
                        logger.warning(f"Service {s_id} not found in database.")
                        pushed_results[s_id] = {"success": False, "error": "Service not found"}
                        continue
                    if not s_data.get("is_active", 1):
                        logger.info(f"Skipping inactive service: {s_data['name']}")
                        pushed_results[s_id] = {"success": False, "error": "Service is inactive"}
                        continue
                    srv = get_service_instance(s_data)
                    res = srv.push_recipe(recipe, source_url=url, frames=frames)
                    pushed_results[s_id] = res
                except Exception as e:
                    logger.error(f"Failed to push to service {s_id}: {e}")
                    pushed_results[s_id] = {"success": False, "error": str(e)}

        # 6. Extract optimized recipe photo for UI response --------------------
        recipe_photo_b64 = None
        if (
            frames
            and recipe.recipe_photo_idx is not None
            and 0 <= recipe.recipe_photo_idx < len(frames)
        ):
            from app.services.mealie import crop_and_optimize_image
            import base64
            logger.info(f"Optimizing recipe photo (frame {recipe.recipe_photo_idx}) for UI display...")
            img_bytes = crop_and_optimize_image(frames[recipe.recipe_photo_idx])
            if img_bytes:
                recipe_photo_b64 = base64.b64encode(img_bytes).decode("utf-8")

        return ExtractResponse(
            recipe=recipe,
            pushed_results=pushed_results,
            transcript=transcript,
            recipe_photo=recipe_photo_b64,
        )

    finally:
        if video_dir and os.path.isdir(video_dir):
            shutil.rmtree(video_dir, ignore_errors=True)
            logger.info("Temp files cleaned up.")
