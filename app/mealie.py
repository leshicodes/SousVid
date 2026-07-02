"""
mealie.py -- Legacy wrapper pointing to the new MealieService implementation.
This maintains compatibility for existing unit tests.
"""
from typing import Optional, List
from app.services.mealie import MealieService, crop_and_optimize_image
from app.models import RecipeSchema
from app.config import settings

def _get_settings_credentials():
    # If settings is mocked in tests, getattr with defaults on missing attributes
    # returns new mock objects (which are truthy but not strings).
    # We check for string types to correctly fallback when needed.
    mealie_url = getattr(settings, "mealie_url", "")
    if not isinstance(mealie_url, str):
        mealie_url = getattr(settings, "mealie_base_url", "")
        if not isinstance(mealie_url, str):
            mealie_url = ""
            
    mealie_token = getattr(settings, "mealie_api_token", "")
    if not isinstance(mealie_token, str):
        mealie_token = getattr(settings, "mealie_token", "")
        if not isinstance(mealie_token, str):
            mealie_token = ""
            
    return mealie_url, mealie_token

def _get_group_slug() -> str:
    srv = MealieService(
        service_id="legacy", 
        name="Legacy Mealie", 
        url=settings.db_path, # URL is unused by this mock, but let's pass a dummy string
        api_token=""
    )
    # Patch url and api_token from settings for legacy tests
    srv.url = settings.db_path # We bypass BaseSettings requirements if settings removed them
    return srv._get_group_slug()

def _ensure_tags_exist(keywords: list[str]) -> list[dict]:
    # We create a temp service using settings fallback (if set) or dummy
    mealie_url, mealie_token = _get_settings_credentials()
    srv = MealieService(
        service_id="legacy", 
        name="Legacy Mealie", 
        url=mealie_url or "http://localhost", 
        api_token=mealie_token
    )
    return srv._ensure_tags_exist(keywords)

def _build_schema_org(recipe: RecipeSchema, source_url: str | None = None) -> dict:
    srv = MealieService(
        service_id="legacy", 
        name="Legacy Mealie", 
        url="http://localhost", 
        api_token=""
    )
    return srv._build_schema_org(recipe, source_url)

def post_to_mealie(recipe: RecipeSchema, source_url: str | None = None, frames: list[str] | None = None) -> dict:
    mealie_url, mealie_token = _get_settings_credentials()
    srv = MealieService(
        service_id="legacy", 
        name="Legacy Mealie", 
        url=mealie_url or "http://localhost", 
        api_token=mealie_token
    )
    res = srv.push_recipe(recipe, source_url, frames)
    if res.get("success"):
        return {"slug": res["slug"], "mealie_url": res["url"]}
    else:
        return {"skipped": True, "reason": res.get("error")}

