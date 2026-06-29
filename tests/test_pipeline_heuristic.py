"""
test_pipeline_heuristic.py -- Tests for _description_looks_like_recipe().

These document the intentional behavior of the heuristic and guard against
accidental changes to the thresholds.
"""

from app.pipeline import _description_looks_like_recipe


# ── True cases: descriptions that should be detected as recipes ─────────────

def test_detects_ingredient_keyword_with_newlines():
    text = "Here is what you need:\ningredients: flour, eggs, butter"
    assert _description_looks_like_recipe(text) is True


def test_detects_measurement_words_with_newlines():
    text = "Mix 2 cups flour with 1 tbsp salt\nadd the eggs and stir"
    assert _description_looks_like_recipe(text) is True


def test_detects_two_measurement_words_as_sufficient():
    # Should not need 'ingredient' if there are at least two measurement words.
    text = "combine 200 grams butter with 2 cups sugar\nmix well"
    assert _description_looks_like_recipe(text) is True


def test_detects_realistic_tiktok_caption():
    caption = (
        "Marry Me Gnocchi Soup 🍲\n"
        "Ingredients:\n"
        "- 1 tbsp olive oil\n"
        "- 1 cup heavy cream\n"
        "- 500 grams gnocchi\n"
        "Instructions: cook on medium heat for 15 min"
    )
    assert _description_looks_like_recipe(caption) is True


# ── False cases: descriptions that should NOT trigger the shortcut ───────────

def test_rejects_none():
    assert _description_looks_like_recipe(None) is False


def test_rejects_empty_string():
    assert _description_looks_like_recipe("") is False


def test_rejects_single_line_with_ingredient():
    # Single line + 'ingredient' should not be enough.
    assert _description_looks_like_recipe("ingredient list is in my bio") is False


def test_rejects_multiline_generic_caption():
    # Multi-line but no recipe vocabulary.
    text = "New video is out!\nCheck my profile for more.\nLike and subscribe."
    assert _description_looks_like_recipe(text) is False


def test_rejects_one_measurement_word_without_ingredient_keyword():
    # One measurement word is below the threshold of 2.
    text = "Add 2 cups of love\nand enjoy your day"
    assert _description_looks_like_recipe(text) is False
