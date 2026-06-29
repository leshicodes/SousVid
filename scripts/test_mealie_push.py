"""
test_mealie_push.py

Standalone test script to push a recipe to Mealie using the
POST /api/recipes/create/html-or-json endpoint.

This endpoint accepts a schema.org/Recipe object as a JSON *string* (not an
object) in the `data` field, and creates the full recipe in one shot  no
separate PATCH call needed.

Usage:
    python test_mealie_push.py
    python test_mealie_push.py path/to/your-recipe.json

Reads MEALIE_URL and MEALIE_API_TOKEN from .env (or environment).
"""

import json
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

MEALIE_URL = os.getenv("MEALIE_URL", "").rstrip("/")
MEALIE_API_TOKEN = os.getenv("MEALIE_API_TOKEN", "")

ENDPOINT = f"{MEALIE_URL}/api/recipes/create/html-or-json"

# ── Load recipe JSON ──────────────────────────────────────────────────────────

recipe_file = sys.argv[1] if len(sys.argv) > 1 else "west-african-inspired-one-pot-chicken-and-rice.json"

with open(recipe_file, "r", encoding="utf-8") as f:
    recipe_obj = json.load(f)

print(f"Loaded recipe: {recipe_obj.get('name', '(no name)')}")
print(f"Pushing to:    {ENDPOINT}")
print()

# ── Build payload ─────────────────────────────────────────────────────────────
# The `data` field must be the recipe as a JSON *string*, not an object.
payload = {
    "data": json.dumps(recipe_obj),
    "includeTags": False,
}

headers = {
    "Authorization": f"Bearer {MEALIE_API_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# ── Send request ──────────────────────────────────────────────────────────────

print("--- Request payload (data field shown as parsed for readability) ---")
preview = dict(payload)
preview["data"] = recipe_obj  # show as object for readability
print(json.dumps(preview, indent=2))
print()

resp = requests.post(ENDPOINT, json=payload, headers=headers, timeout=30)

print(f"--- Response: {resp.status_code} {resp.reason} ---")
try:
    body = resp.json()
    print(json.dumps(body, indent=2))
except Exception:
    print(resp.text)

print()

# ── Result ────────────────────────────────────────────────────────────────────

if resp.ok:
    slug = None
    if isinstance(body, dict):
        slug = body.get("slug")
    elif isinstance(body, str):
        slug = body.strip().strip('"')

    if slug:
        print(f"SUCCESS! Recipe created.")
        print(f"   Slug:       {slug}")
        print(f"   Mealie URL: {MEALIE_URL}/r/{slug}")
    else:
        print(f"SUCCESS! (slug not found in response body)")
else:
    print(f"FAILED with status {resp.status_code}")
    print("   Check the response above for details.")
