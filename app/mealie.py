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
import json
import logging

import httpx

from app.config import settings
from app.models import RecipeSchema

logger = logging.getLogger(__name__)


def _auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.mealie_api_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


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


def post_to_mealie(recipe: RecipeSchema, source_url: str | None = None) -> dict:
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
        schema_obj = _build_schema_org(recipe, source_url=source_url)

        payload: dict = {
            "data": json.dumps(schema_obj),
            "includeTags": False,
        }
        if source_url:
            # Also pass the URL at the request level so Mealie populates org_url
            # even if the schema.org parser doesn't pick it up from the JSON.
            payload["url"] = source_url

        logger.info(f"Pushing to Mealie: '{recipe.name}'")
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{settings.mealie_base_url}/api/recipes/create/html-or-json",
                json=payload,
                headers=_auth_headers(),
            )

        if not resp.is_success:
            logger.error(f"Mealie returned {resp.status_code}: {resp.text[:500]}")
        resp.raise_for_status()

        # Mealie returns the slug as a plain quoted string, e.g. "marry-me-gnocchi-soup"
        slug = resp.text.strip().strip('"')
        logger.info(f"Created in Mealie with slug: {slug}")

        return {
            "slug": slug,
            "mealie_url": f"{settings.mealie_base_url}/r/{slug}",
        }

    except Exception as exc:
        logger.error(f"Mealie upload failed (non-fatal): {exc}")
        return {"skipped": True, "reason": str(exc)}
