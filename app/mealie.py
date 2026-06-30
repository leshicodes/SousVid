"""
mealie.py -- Pushes extracted recipes to a self-hosted Mealie instance.

API flow:
    POST /api/recipes/create/html-or-json
        body: { "data": "<schema.org/Recipe JSON>", "includeTags": false, "url": "<source url>" }
    response: the new recipe slug as a plain string

The source video URL is passed in two places so Mealie reliably populates the
"Original URL" field (org_url): once inside the schema.org JSON as the `url`
property, and once as the top-level `url` field in the ScrapeRecipeData payload.
"""
import base64
import io
import json
import logging

import httpx
from PIL import Image

from app.config import settings
from app.models import RecipeSchema

logger = logging.getLogger(__name__)

# Module-level cache for group slug (lazy-initialized)
_group_slug_cache: str | None = None


def _get_group_slug() -> str:
    """
    Fetch the user's group slug from Mealie and cache it.

    Returns:
        The group slug string, or "" if the API call fails.
    """
    global _group_slug_cache

    # Return cached value if available
    if _group_slug_cache is not None:
        return _group_slug_cache

    try:
        headers = {
            "Authorization": f"Bearer {settings.mealie_api_token}",
            "Accept": "application/json",
        }

        with httpx.Client(timeout=30.0) as client:
            resp = client.get(
                f"{settings.mealie_base_url}/api/users/self",
                headers=headers,
            )

        if not resp.is_success:
            logger.warning(f"Failed to fetch group slug from Mealie: {resp.status_code}")
            _group_slug_cache = ""
            return _group_slug_cache

        user_data = resp.json()
        group_slug = user_data.get("groupSlug", "")

        if not group_slug:
            logger.warning("No groupSlug found in /api/users/self response")
            _group_slug_cache = ""
            return _group_slug_cache

        # Cache the result for future use
        _group_slug_cache = group_slug
        logger.info(f"Resolved group slug: {group_slug}")
        return group_slug

    except Exception as exc:
        logger.warning(f"Failed to fetch group slug from Mealie: {exc}")
        _group_slug_cache = ""
        return _group_slug_cache


def crop_and_optimize_image(frame_b64: str) -> bytes | None:
    """
    Decodes a base64 frame, center-crops it to a 1:1 square if it's in portrait orientation,
    and returns compressed JPEG bytes.
    """
    try:
        image_data = base64.b64decode(frame_b64)
        img = Image.open(io.BytesIO(image_data))

        width, height = img.size
        if height > width:
            # Portrait: center-crop vertically to make it a square
            left = 0
            top = (height - width) // 2
            right = width
            bottom = top + width
            img = img.crop((left, top, right, bottom))

        # Save as compressed JPEG
        output_io = io.BytesIO()
        img.convert("RGB").save(output_io, format="JPEG", quality=85, optimize=True)
        return output_io.getvalue()
    except Exception as e:
        logger.warning(f"Failed to crop/optimize image: {e}")
        return None


def _normalize_tag(tag: str) -> str:
    """Normalize a tag by stripping whitespace and title-casing."""
    if not tag:
        return ""
    # Strip, lowercase for comparison, then title-case for display
    normalized = " ".join(tag.strip().split())  # Normalize spaces
    return normalized.title()


def _ensure_tags_exist(keywords: list[str]) -> list[dict]:
    """
    Ensure all tags exist in Mealie before recipe creation.

    For each keyword:
      - Normalize: strip, title-case (e.g., "comfort food" → "Comfort Food")
      - GET /api/organizers/tags?search=<name> — check for existing tag
      - If found, use the existing ID; if not, POST to create it

    Returns:
        A list of {"id": "...", "name": "..."} dicts for all tags.
    """
    if not keywords:
        return []

    # Normalize and deduplicate keywords first (to avoid creating duplicate tags)
    seen_tags = set()
    normalized_keywords = []
    for kw in keywords:
        norm = _normalize_tag(kw)
        if norm and norm.lower() not in seen_tags:  # Track by lowercase to be case-insensitive
            seen_tags.add(norm.lower())
            normalized_keywords.append(norm)

    logger.info(f"Processing {len(normalized_keywords)} tags for recipe")

    headers = {
        "Authorization": f"Bearer {settings.mealie_api_token}",
        "Accept": "application/json",
    }

    # First, check which tags already exist (batch search) and create missing ones.
    existing_tags: dict[str, str] = {}  # name -> id mapping

    client = httpx.Client(timeout=30.0)
    try:
        for tag in normalized_keywords:
            resp = client.get(
                f"{settings.mealie_base_url}/api/organizers/tags?search={tag}",
                headers=headers,
            )

            if resp.is_success:
                data = resp.json()
                items = data.get("items", [])
                # Look for a case-insensitive name match in the returned items
                for item in items:
                    if item.get("name", "").strip().lower() == tag.lower():
                        existing_tags[tag] = item["id"]
                        logger.debug(f"Tag '{tag}' found as '{item.get('name')}' (id={item['id']})")
                        break

        # For tags not found, create them while client is still open
        for tag in normalized_keywords:
            if tag not in existing_tags:
                payload = {"name": tag}
                logger.debug(f"Tag '{tag}' not found — creating it")

                resp = client.post(
                    f"{settings.mealie_base_url}/api/organizers/tags",
                    json=payload,
                    headers=headers,
                )

                if resp.is_success:
                    created_data = resp.json()
                    tag_id = created_data.get("id", "")
                    existing_tags[tag] = tag_id
                    logger.info(f"Tag '{tag}' created (id={tag_id})")
                else:
                    logger.warning(f"Failed to create tag '{tag}': {resp.status_code} {resp.text[:200]}")

    finally:
        client.close()  # Close the shared HTTP client after all operations are done

    # Build the final list of tags with IDs, skipping any that failed to be found or created
    result = []
    for tag in normalized_keywords:
        if tag in existing_tags:
            result.append({"id": existing_tags[tag], "name": tag})
        else:
            logger.warning(f"Tag '{tag}' will not be added to the recipe because it could not be resolved or created in Mealie.")
    return result


def _build_schema_org(recipe: RecipeSchema, source_url: str | None = None) -> dict:
    description = recipe.description or ""
    if source_url:
        separator = "\n\n" if description else ""
        description = f"{description}{separator}Source: {source_url}"

    obj: dict = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": recipe.name,
    }

    if source_url:
        obj["url"] = source_url

    if description:
        obj["description"] = description

    if recipe.recipeYield:
        obj["recipeYield"] = recipe.recipeYield

    for time_field in ("prepTime", "cookTime", "totalTime"):
        val = getattr(recipe, time_field, None)
        if val:
            obj[time_field] = val

    if recipe.keywords:
        obj["keywords"] = ", ".join(recipe.keywords)

    if recipe.recipeIngredient:
        obj["recipeIngredient"] = recipe.recipeIngredient

    if recipe.recipeInstructions:
        obj["recipeInstructions"] = [
            {
                "@type": "HowToStep",
                "text": step.text,
                **({"name": step.title} if step.title else {}),
            }
            for step in recipe.recipeInstructions
        ]

    return obj


def post_to_mealie(
    recipe: RecipeSchema,
    source_url: str | None = None,
    frames: list[str] | None = None,
) -> dict:
    """
    Create a recipe in Mealie and return its slug and URL.

    This is a best-effort operation -- failure is logged and reported to the
    caller but never re-raised, so a Mealie outage cannot break recipe extraction.

    Returns:
        {"slug": "...", "mealie_url": "..."} on success.
        {"skipped": True, "reason": "..."} when Mealie is not configured or the
        request fails.
    """
    if not settings.mealie_configured:
        logger.warning("Mealie not configured -- skipping upload.")
        return {"skipped": True, "reason": "MEALIE_URL or MEALIE_API_TOKEN not set"}

    try:
        # Ensure tags exist before building the schema.org payload
        if recipe.keywords:
            tag_results = _ensure_tags_exist(recipe.keywords)
            logger.info(f"Tags processed for '{recipe.name}': {tag_results}")

        schema_obj = _build_schema_org(recipe, source_url=source_url)

        # Build tags array in Mealie's native format (not schema.org keywords)
        tags_payload: list[dict] | None = None
        if recipe.keywords and tag_results:
            tags_payload = [{"id": t["id"], "name": t["name"]} for t in tag_results]

        payload: dict = {
            "data": json.dumps(schema_obj),
            "includeTags": False,  # We handle tags ourselves via the tags array
        }
        if source_url:
            # Also pass the URL at the request level so Mealie populates org_url
            # even if the schema.org parser doesn't pick it up from the JSON.
            payload["url"] = source_url

        logger.info(f"Pushing to Mealie: '{recipe.name}'")

        # Build auth headers (same pattern as used elsewhere in this file)
        auth_headers = {
            "Authorization": f"Bearer {settings.mealie_api_token}",
            "Accept": "application/json",
        }

        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{settings.mealie_base_url}/api/recipes/create/html-or-json",
                json=payload,
                headers=auth_headers,
            )

        if not resp.is_success:
            logger.error(f"Mealie returned {resp.status_code}: {resp.text[:500]}")
        resp.raise_for_status()

        # Mealie returns the slug as a plain quoted string, e.g. "marry-me-gnocchi-soup"
        slug = resp.text.strip().strip('"')
        logger.info(f"Created in Mealie with slug: {slug}")

        # Build the correct URL using the cached group slug (or empty if not available)
        group_slug = _get_group_slug()  # Returns "" if cache miss or error
        mealie_url = f"{settings.mealie_base_url}/g/{group_slug}/r/{slug}" if group_slug else f"{settings.mealie_base_url}/r/{slug}"

        # If a recipe photo index is provided, try to upload the image
        if (
            frames
            and recipe.recipe_photo_idx is not None
            and 0 <= recipe.recipe_photo_idx < len(frames)
        ):
            logger.info(f"Optimizing and uploading recipe photo (frame {recipe.recipe_photo_idx}) to Mealie...")
            img_bytes = crop_and_optimize_image(frames[recipe.recipe_photo_idx])
            if img_bytes:
                try:
                    img_headers = {
                        "Authorization": f"Bearer {settings.mealie_api_token}",
                        "Accept": "application/json",
                    }
                    img_files = {
                        "image": ("recipe_image.jpg", img_bytes, "image/jpeg")
                    }
                    img_data = {
                        "extension": "jpg"
                    }
                    with httpx.Client(timeout=30.0) as client:
                        img_resp = client.put(
                            f"{settings.mealie_base_url}/api/recipes/{slug}/image",
                            headers=img_headers,
                            files=img_files,
                            data=img_data,
                        )
                    if not img_resp.is_success:
                        logger.warning(
                            f"Mealie photo upload returned {img_resp.status_code}: {img_resp.text[:500]}"
                        )
                    else:
                        logger.info("Recipe photo successfully uploaded to Mealie.")
                except Exception as img_exc:
                    logger.warning(f"Recipe photo upload failed (non-fatal): {img_exc}")

        return {  # The client was closed by the context manager automatically.
            "slug": slug,
            "mealie_url": mealie_url,
        }

    except Exception as exc:
        logger.error(f"Mealie upload failed (non-fatal): {exc}")
        return {"skipped": True, "reason": str(exc)}
