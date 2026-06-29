"""
llm.py -- Sends transcript and video frames to an LLM and extracts a structured recipe.

Uses the OpenAI-compatible SDK pointed at OpenRouter, so the model can be swapped
to anything OpenRouter supports (GPT-4o, Claude, Gemini, etc.) without code changes.
"""
import json
import logging

from openai import OpenAI

from app.config import settings
from app.errors import ExtractionError
from app.models import RecipeInstruction, RecipeSchema

logger = logging.getLogger(__name__)

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=settings.openrouter_api_key,
    default_headers={
        # OpenRouter uses these headers for usage tracking and rate-limit attribution.
        "HTTP-Referer": "https://github.com/local/sousvid",
        "X-Title": "SousVid",
    },
)

SYSTEM_PROMPT = (
    "You are a recipe extraction assistant specializing in short-form cooking videos. "
    "Extract recipes from the provided video transcript and frames. "
    "Always return valid, raw JSON -- no markdown fences, no commentary. "
    "If information is unclear, make a reasonable culinary assumption rather than omitting it."
)

USER_PROMPT_TEMPLATE = """\
Extract the complete recipe from this cooking video.

VIDEO TRANSCRIPT:
{transcript}

Key frames from the video are attached as images. \
If on-screen text shows measurements or ingredients, prefer those over spoken values when they differ.

Return a single JSON object with these fields (omit a field only if truly undetectable):
{{
  "name": "string -- recipe name",
  "description": "string -- 1-2 sentence summary",
  "recipeIngredient": ["array of strings formatted as 'quantity unit ingredient'. For solid/dry ingredients prefer grams (g) -- e.g. '250 g flour', '15 g butter'. For liquids, use fl oz or cups -- e.g. '8 fl oz water', '1 cup heavy cream'. Use volumetric or descriptive units only when weight is truly impractical (e.g. 'a pinch of salt', '1 clove garlic', 'to taste')."],
  "recipeInstructions": [{{"text": "string -- one discrete step"}}],
  "recipeYield": "string -- e.g. '4 servings'",
  "prepTime": "ISO 8601 duration, e.g. 'PT10M'",
  "cookTime": "ISO 8601 duration, e.g. 'PT25M'",
  "totalTime": "ISO 8601 duration",
  "keywords": ["array of 3-6 relevant tags"]
}}"""


def _normalize_instructions(raw: list) -> list[RecipeInstruction]:
    result = []
    for step in raw:
        if isinstance(step, str):
            result.append(RecipeInstruction(text=step))
        elif isinstance(step, dict):
            text = (
                step.get("text")
                or step.get("text_content")  # some models use this key
                or step.get("description")
                or step.get("name")
                or str(step)
            )
            # schema.org HowToStep uses 'name' as the step title; ignore empty values.
            title = step.get("name") or step.get("title") or ""
            result.append(RecipeInstruction(text=text, title=title))
        else:
            result.append(RecipeInstruction(text=str(step)))
    return result


def extract_recipe(transcript: str, frames: list[str]) -> RecipeSchema:
    """
    Call the configured LLM with the transcript and video frames.

    Args:
        transcript: The video's spoken text (or caption, if description-first path was taken).
        frames:     Base64-encoded JPEG strings to attach as vision inputs.

    Returns:
        A validated RecipeSchema.

    Raises:
        ExtractionError: if the LLM response cannot be parsed as a recipe.
    """
    content: list[dict] = [
        {
            "type": "text",
            "text": USER_PROMPT_TEMPLATE.format(
                transcript=transcript or "(no transcript available)"
            ),
        }
    ]

    for frame_b64 in frames:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}"},
        })

    logger.info(f"Calling {settings.openrouter_model} with {len(frames)} frames...")

    response = client.chat.completions.create(
        model=settings.openrouter_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        temperature=0.2,  # low temperature keeps the structured output consistent
        max_tokens=2048,
    )

    raw_text = response.choices[0].message.content.strip()

    # Some models wrap their output in markdown fences despite being told not to.
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        raw_text = "\n".join(lines[1:-1]).strip()

    try:
        data: dict = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ExtractionError(
            f"LLM returned non-JSON output: {raw_text[:200]!r}"
        ) from exc

    if "recipeInstructions" in data:
        data["recipeInstructions"] = _normalize_instructions(data["recipeInstructions"])

    # schema.org keys that Pydantic doesn't expect
    data.pop("@type", None)
    data.pop("@context", None)

    recipe = RecipeSchema(**data)
    logger.info(f"Extracted '{recipe.name}' with {len(recipe.recipeIngredient)} ingredients.")
    return recipe
