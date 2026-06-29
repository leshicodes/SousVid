"""
test_mealie.py -- Tests for _build_schema_org().

Verifies that the schema.org payload is built correctly in all combinations
of input fields, with and without a source URL.
"""
import pytest

from app.mealie import _build_schema_org
from app.models import RecipeInstruction, RecipeSchema


def _minimal_recipe(**kwargs) -> RecipeSchema:
    defaults = {
        "name": "Test Recipe",
        "recipeIngredient": [],
        "recipeInstructions": [],
    }
    return RecipeSchema(**{**defaults, **kwargs})


# ── Required fields ──────────────────────────────────────────────────────────

def test_always_includes_context_and_type():
    obj = _build_schema_org(_minimal_recipe())
    assert obj["@context"] == "https://schema.org"
    assert obj["@type"] == "Recipe"


def test_always_includes_name():
    obj = _build_schema_org(_minimal_recipe(name="Shakshuka"))
    assert obj["name"] == "Shakshuka"


# ── Source URL handling ───────────────────────────────────────────────────────

def test_source_url_sets_schema_org_url_property():
    obj = _build_schema_org(_minimal_recipe(), source_url="https://example.com/video")
    assert obj["url"] == "https://example.com/video"


def test_source_url_appended_to_description():
    obj = _build_schema_org(
        _minimal_recipe(description="A great soup."),
        source_url="https://example.com/video",
    )
    assert "Source: https://example.com/video" in obj["description"]
    assert obj["description"].startswith("A great soup.")


def test_source_url_as_only_description_when_none():
    obj = _build_schema_org(_minimal_recipe(), source_url="https://example.com/video")
    assert obj["description"] == "Source: https://example.com/video"


def test_no_url_field_when_source_url_is_none():
    obj = _build_schema_org(_minimal_recipe())
    assert "url" not in obj


# ── Optional fields ───────────────────────────────────────────────────────────

def test_omits_optional_fields_when_empty():
    obj = _build_schema_org(_minimal_recipe())
    for key in ("description", "recipeYield", "prepTime", "cookTime", "totalTime", "keywords"):
        assert key not in obj


def test_includes_recipe_yield():
    obj = _build_schema_org(_minimal_recipe(recipeYield="4 servings"))
    assert obj["recipeYield"] == "4 servings"


def test_keywords_joined_as_comma_string():
    obj = _build_schema_org(_minimal_recipe(keywords=["soup", "easy", "vegetarian"]))
    assert obj["keywords"] == "soup, easy, vegetarian"


def test_instructions_converted_to_how_to_steps():
    recipe = _minimal_recipe(
        recipeInstructions=[
            RecipeInstruction(text="Boil water."),
            RecipeInstruction(text="Add pasta.", title="Step 2"),
        ]
    )
    obj = _build_schema_org(recipe)
    steps = obj["recipeInstructions"]
    assert steps[0] == {"@type": "HowToStep", "text": "Boil water."}
    assert steps[1] == {"@type": "HowToStep", "text": "Add pasta.", "name": "Step 2"}


def test_step_without_title_omits_name_key():
    recipe = _minimal_recipe(
        recipeInstructions=[RecipeInstruction(text="Stir gently.")]
    )
    obj = _build_schema_org(recipe)
    assert "name" not in obj["recipeInstructions"][0]
