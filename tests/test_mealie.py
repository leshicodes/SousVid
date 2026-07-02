"""
test_mealie.py -- Tests for _build_schema_org().

Verifies that the schema.org payload is built correctly in all combinations
of input fields, with and without a source URL.
"""

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


# ── Tag processing tests ─────────────────────────────────────────────────────

from unittest.mock import MagicMock, patch
from app.mealie import _ensure_tags_exist

@patch("httpx.Client")
@patch("app.mealie.settings")
def test_ensure_tags_exist_case_insensitive_and_failure(mock_settings, mock_client_class):
    mock_settings.mealie_base_url = "http://mealie.local"
    mock_settings.mealie_api_token = "dummy-token"

    mock_client = MagicMock()
    mock_client_class.return_value.__enter__.return_value = mock_client

    # We check two keywords: "Baking" and "NewTag"
    # "Baking" matches an existing Mealie tag "baking" case-insensitively.
    # "NewTag" is not found and fails to create with a 500 error.
    def mock_get(url, **kwargs):
        resp = MagicMock()
        if "search=Baking" in url:
            resp.is_success = True
            resp.json.return_value = {
                "items": [
                    {"id": "baking-id", "name": "baking"}
                ]
            }
        elif "search=NewTag" in url:
            resp.is_success = True
            resp.json.return_value = {"items": []}
        else:
            resp.is_success = False
        return resp

    def mock_post(url, json, **kwargs):
        resp = MagicMock()
        if "organizers/tags" in url and json.get("name") == "NewTag":
            resp.is_success = False
            resp.status_code = 500
            resp.text = "Internal Server Error"
        else:
            resp.is_success = False
        return resp

    mock_client.get.side_effect = mock_get
    mock_client.post.side_effect = mock_post

    tags = _ensure_tags_exist(["Baking", "NewTag"])

    # "Baking" should be matched case-insensitively and resolved.
    # "NewTag" creation failure should not throw a KeyError and instead be skipped.
    assert len(tags) == 1
    assert tags[0] == {"id": "baking-id", "name": "Baking"}

