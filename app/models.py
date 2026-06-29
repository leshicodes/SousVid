from pydantic import BaseModel, Field
from typing import Optional


class ExtractRequest(BaseModel):
    url: str = Field(..., description="URL of the Instagram Reel, TikTok, or YouTube Shorts video")
    push_to_mealie: bool = Field(True, description="Whether to push the extracted recipe to Mealie (default: on)")


class RecipeInstruction(BaseModel):
    text: str
    title: str = ""


class RecipeSchema(BaseModel):
    """Loosely mirrors schema.org/Recipe and Mealie's native format."""
    name: str
    description: Optional[str] = None
    recipeIngredient: list[str] = Field(default_factory=list)
    recipeInstructions: list[RecipeInstruction] = Field(default_factory=list)
    recipeYield: Optional[str] = None
    prepTime: Optional[str] = None
    cookTime: Optional[str] = None
    totalTime: Optional[str] = None
    keywords: Optional[list[str]] = None


class ExtractResponse(BaseModel):
    recipe: RecipeSchema
    mealie_url: Optional[str] = None
    mealie_slug: Optional[str] = None
    mealie_warning: Optional[str] = None  # set if Mealie upload failed (non-fatal)
    transcript: Optional[str] = None  # included for debugging


class JobSubmitResponse(BaseModel):
    job_id: str


class JobStatusResponse(BaseModel):
    status: str
    step: Optional[str] = None
    result: Optional[dict] = None
    error: Optional[str] = None
