import base64
import io
import json
import logging
import httpx
from PIL import Image
from typing import Tuple, Optional
from app.services.base import BaseService
from app.models import RecipeSchema

logger = logging.getLogger(__name__)

class MealieService(BaseService):
    def test_connection(self) -> Tuple[bool, str]:
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json",
        }
        try:
            with httpx.Client(timeout=10.0, verify=self.ssl_verify) as client:
                resp = client.get(f"{self.url}/api/users/self", headers=headers)
            if resp.is_success:
                user_data = resp.json()
                username = user_data.get("username", "Unknown User")
                return True, f"Successfully authenticated as {username}"
            else:
                return False, f"HTTP Error {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"

    def _get_group_slug(self) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json",
        }
        try:
            with httpx.Client(timeout=30.0, verify=self.ssl_verify) as client:
                resp = client.get(f"{self.url}/api/users/self", headers=headers)
            if resp.is_success:
                return resp.json().get("groupSlug", "")
        except Exception as e:
            logger.warning(f"Failed to fetch group slug: {e}")
        return ""

    def _normalize_tag(self, tag: str) -> str:
        if not tag:
            return ""
        return " ".join(tag.strip().split()).title()

    def _ensure_tags_exist(self, keywords: list[str]) -> list[dict]:
        if not keywords:
            return []

        seen_tags = set()
        normalized_keywords = []
        for kw in keywords:
            norm = self._normalize_tag(kw)
            if norm and norm.lower() not in seen_tags:
                seen_tags.add(norm.lower())
                normalized_keywords.append(norm)

        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json",
        }

        existing_tags = {}
        with httpx.Client(timeout=30.0, verify=self.ssl_verify) as client:
            for tag in normalized_keywords:
                try:
                    resp = client.get(f"{self.url}/api/organizers/tags?search={tag}", headers=headers)
                    if resp.is_success:
                        items = resp.json().get("items", [])
                        for item in items:
                            if item.get("name", "").strip().lower() == tag.lower():
                                existing_tags[tag] = item["id"]
                                break
                except Exception as e:
                    logger.warning(f"Tag search error for '{tag}': {e}")

            for tag in normalized_keywords:
                if tag not in existing_tags:
                    try:
                        resp = client.post(f"{self.url}/api/organizers/tags", json={"name": tag}, headers=headers)
                        if resp.is_success:
                            existing_tags[tag] = resp.json().get("id", "")
                    except Exception as e:
                        logger.warning(f"Tag create error for '{tag}': {e}")

        result = []
        for tag in normalized_keywords:
            if tag in existing_tags:
                result.append({"id": existing_tags[tag], "name": tag})
        return result

    def _build_schema_org(self, recipe: RecipeSchema, source_url: str | None = None) -> dict:
        description = recipe.description or ""
        if source_url:
            separator = "\n\n" if description else ""
            description = f"{description}{separator}Source: {source_url}"

        obj = {
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

    def push_recipe(self, recipe: RecipeSchema, source_url: str | None, frames: list[str] | None) -> dict:
        """Create a recipe in Mealie and return its status details."""
        try:
            # Ensure tags exist in Mealie
            tag_results = []
            if recipe.keywords:
                tag_results = self._ensure_tags_exist(recipe.keywords)

            schema_obj = self._build_schema_org(recipe, source_url=source_url)

            payload = {
                "data": json.dumps(schema_obj),
                "includeTags": False,
            }
            if source_url:
                payload["url"] = source_url

            auth_headers = {
                "Authorization": f"Bearer {self.api_token}",
                "Accept": "application/json",
            }

            # Create recipe
            with httpx.Client(timeout=30.0, verify=self.ssl_verify) as client:
                resp = client.post(
                    f"{self.url}/api/recipes/create/html-or-json",
                    json=payload,
                    headers=auth_headers,
                )
            
            if not resp.is_success:
                logger.error(f"Mealie returned {resp.status_code}: {resp.text[:500]}")
                return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

            slug = resp.text.strip().strip('"')
            group_slug = self._get_group_slug()
            mealie_url = f"{self.url}/g/{group_slug}/r/{slug}" if group_slug else f"{self.url}/r/{slug}"

            # Upload recipe photo if available
            if (
                frames
                and recipe.recipe_photo_idx is not None
                and 0 <= recipe.recipe_photo_idx < len(frames)
            ):
                logger.info(f"Uploading optimized recipe photo (frame {recipe.recipe_photo_idx}) to Mealie...")
                img_bytes = crop_and_optimize_image(frames[recipe.recipe_photo_idx])
                if img_bytes:
                    try:
                        img_headers = {
                            "Authorization": f"Bearer {self.api_token}",
                            "Accept": "application/json",
                        }
                        img_files = {
                            "image": ("recipe_image.jpg", img_bytes, "image/jpeg")
                        }
                        img_data = {
                            "extension": "jpg"
                        }
                        with httpx.Client(timeout=30.0, verify=self.ssl_verify) as client:
                            img_resp = client.put(
                                f"{self.url}/api/recipes/{slug}/image",
                                headers=img_headers,
                                files=img_files,
                                data=img_data,
                            )
                        if not img_resp.is_success:
                            logger.warning(f"Mealie image upload failed: {img_resp.status_code}")
                    except Exception as img_exc:
                        logger.warning(f"Mealie image upload failed: {img_exc}")

            return {
                "success": True,
                "url": mealie_url,
                "slug": slug,
                "error": None
            }

        except Exception as e:
            logger.error(f"Push to Mealie failed: {e}")
            return {
                "success": False,
                "url": None,
                "slug": None,
                "error": str(e)
            }

def crop_and_optimize_image(frame_b64: str) -> Optional[bytes]:
    try:
        image_data = base64.b64decode(frame_b64)
        img = Image.open(io.BytesIO(image_data))

        width, height = img.size
        if height > width:
            left = 0
            top = (height - width) // 2
            right = width
            bottom = top + width
            img = img.crop((left, top, right, bottom))

        output_io = io.BytesIO()
        img.convert("RGB").save(output_io, format="JPEG", quality=85, optimize=True)
        return output_io.getvalue()
    except Exception as e:
        logger.warning(f"Failed to crop/optimize image: {e}")
        return None

