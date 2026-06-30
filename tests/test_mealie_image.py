import base64
import io
from unittest.mock import MagicMock, patch
from PIL import Image

from app.mealie import crop_and_optimize_image, post_to_mealie
from app.models import RecipeSchema, RecipeInstruction
from app.llm import select_recipe_photo
from app.config import settings


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
@patch("app.mealie._get_group_slug")
@patch("app.mealie.settings")
def test_post_to_mealie_uploads_image(mock_settings, mock_get_group_slug, mock_client_class):
    mock_settings.mealie_configured = True
    mock_settings.mealie_base_url = "http://mealie.local"
    mock_settings.mealie_api_token = "dummy-token"
    mock_get_group_slug.return_value = ""

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


def test_select_recipe_photo_api_call():
    # Test that select_recipe_photo calls openrouter with correct payload
    with patch("app.llm.client.chat.completions.create") as mock_create:
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content='{ "recipe_photo_idx": 2 }'))
        ]
        mock_create.return_value = mock_response

        frames = ["frame0", "frame1", "frame2"]
        idx = select_recipe_photo("Pasta", frames)

        assert idx == 2
        mock_create.assert_called_once()
        args, kwargs = mock_create.call_args
        assert kwargs["model"] == settings.openrouter_model
        # Check messages structure
        messages = kwargs["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        # The user message has one text and 3 image_url dicts
        user_content = messages[1]["content"]
        assert len(user_content) == 4
        assert user_content[0]["type"] == "text"
        assert user_content[1]["type"] == "image_url"


@patch("app.pipeline.download_video")
@patch("app.pipeline.extract_frames")
@patch("app.pipeline.transcribe")
@patch("app.pipeline.extract_recipe")
@patch("app.pipeline.post_to_mealie")
@patch("app.llm.client.chat.completions.create")
def test_pipeline_photo_retry(
    mock_chat_create,
    mock_post_to_mealie,
    mock_extract_recipe,
    mock_transcribe,
    mock_extract_frames,
    mock_download_video,
):
    mock_download_video.return_value = ("/tmp/video.mp4", "Caption")

    # First extract_frames call (default offset) vs second call (segment="hook_and_plating")
    def side_effect_extract_frames(video_path, num_frames=None, offset=0.5, segment="all"):
        if segment == "all" and offset == 0.5:
            return ["frame0", "frame1"]
        elif segment == "hook_and_plating":
            return ["frame2", "frame3", "frame4"]
        return []
    mock_extract_frames.side_effect = side_effect_extract_frames

    mock_transcribe.return_value = "Transcript text"

    # LLM extraction returns recipe with recipe_photo_idx = None
    mock_recipe = RecipeSchema(
        name="Test Retry Recipe",
        recipeIngredient=["1 egg"],
        recipeInstructions=[RecipeInstruction(text="Fry it")],
        recipe_photo_idx=None
    )
    mock_extract_recipe.return_value = mock_recipe

    # Mock Mealie push
    mock_post_to_mealie.return_value = {"slug": "test-retry", "mealie_url": "http://mealie/r/test-retry"}

    # Mock LLM photo-only selection response (should return index 1 of the new batch, which is frame3)
    mock_chat_resp = MagicMock()
    mock_chat_resp.choices = [
        MagicMock(message=MagicMock(content='{ "recipe_photo_idx": 1 }'))
    ]
    mock_chat_create.return_value = mock_chat_resp

    from app.pipeline import run_pipeline
    res = run_pipeline("http://example.com/video", push_to_mealie=True)

    # Verify that extract_recipe was called with initial frames
    mock_extract_recipe.assert_called_once_with("Video caption:\nCaption\n\nAudio transcript:\nTranscript text", ["frame0", "frame1"])

    # Verify that extract_frames was called twice: once for initial frames, once for retry
    assert mock_extract_frames.call_count == 2
    mock_extract_frames.assert_any_call("/tmp/video.mp4", segment="hook_and_plating")

    # Verify that post_to_mealie was called with the second batch of frames and the new photo index (1)
    mock_post_to_mealie.assert_called_once()
    called_recipe = mock_post_to_mealie.call_args[0][0]
    assert called_recipe.recipe_photo_idx == 1
    assert mock_post_to_mealie.call_args[1]["frames"] == ["frame2", "frame3", "frame4"]

