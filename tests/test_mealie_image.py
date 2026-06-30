import base64
import io
from unittest.mock import MagicMock, patch
from PIL import Image

from app.mealie import crop_and_optimize_image, post_to_mealie
from app.models import RecipeSchema, RecipeInstruction


def _create_test_image_b64(width: int, height: int) -> str:
    """Helper to create a base64 encoded test image of given dimensions."""
    img = Image.new("RGB", (width, height), color="red")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def test_crop_and_optimize_portrait():
    # 640x960 is portrait (vertical) -> should be cropped to 640x640 square
    b64_in = _create_test_image_b64(640, 960)
    bytes_out = crop_and_optimize_image(b64_in)

    assert bytes_out is not None
    img_out = Image.open(io.BytesIO(bytes_out))
    assert img_out.format == "JPEG"
    assert img_out.size == (640, 640)


def test_crop_and_optimize_landscape():
    # 960x640 is landscape -> should retain original aspect ratio (960x640)
    b64_in = _create_test_image_b64(960, 640)
    bytes_out = crop_and_optimize_image(b64_in)

    assert bytes_out is not None
    img_out = Image.open(io.BytesIO(bytes_out))
    assert img_out.format == "JPEG"
    assert img_out.size == (960, 640)


def test_crop_and_optimize_corrupted_base64():
    # Corrupted base64 should log warning and return None rather than crashing
    assert crop_and_optimize_image("not-a-valid-base64-string!!") is None


@patch("httpx.Client")
@patch("app.mealie.settings")
def test_post_to_mealie_uploads_image(mock_settings, mock_client_class):
    mock_settings.mealie_configured = True
    mock_settings.mealie_base_url = "http://mealie.local"
    mock_settings.mealie_api_token = "dummy-token"

    # Set up client mock
    mock_client = MagicMock()
    mock_client_class.return_value.__enter__.return_value = mock_client

    # Mock post (recipe create)
    mock_post_resp = MagicMock()
    mock_post_resp.is_success = True
    mock_post_resp.text = '"test-recipe-slug"'

    # Mock put (image upload)
    mock_put_resp = MagicMock()
    mock_put_resp.is_success = True

    # Configure mock client behavior based on endpoint path
    def mock_request(method, url, **kwargs):
        if method == "POST" and "/api/recipes/create/html-or-json" in url:
            return mock_post_resp
        if method == "PUT" and "/api/recipes/test-recipe-slug/image" in url:
            return mock_put_resp
        raise ValueError(f"Unexpected request: {method} {url}")

    mock_client.post.side_effect = lambda url, **kwargs: mock_request("POST", url, **kwargs)
    mock_client.put.side_effect = lambda url, **kwargs: mock_request("PUT", url, **kwargs)

    recipe = RecipeSchema(
        name="Test Upload Image Recipe",
        recipeIngredient=["1 egg"],
        recipeInstructions=[RecipeInstruction(text="Fry it")],
        recipe_photo_idx=1
    )

    frames = [
        _create_test_image_b64(100, 100),
        _create_test_image_b64(200, 300),  # This is index 1, portrait (vertical) -> will be cropped to 200x200
    ]

    res = post_to_mealie(recipe, source_url="http://example.com/video", frames=frames)

    assert res == {
        "slug": "test-recipe-slug",
        "mealie_url": "http://mealie.local/r/test-recipe-slug"
    }

    # Verify post was called for the recipe
    mock_client.post.assert_called_once()

    # Verify put was called for the image
    mock_client.put.assert_called_once()
    put_args, put_kwargs = mock_client.put.call_args
    assert "/api/recipes/test-recipe-slug/image" in put_args[0]
    assert put_kwargs["headers"]["Authorization"] == "Bearer dummy-token"
    assert put_kwargs["data"]["extension"] == "jpg"
    assert "image" in put_kwargs["files"]
