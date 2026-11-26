"""Structured recipe-text extraction schema and prompt builder."""

from __future__ import annotations

from pydantic import BaseModel, Field

from custom_components.ha_calories_buddy.core.models import PreparationState


class ParsedRecipeIngredientSchema(BaseModel):
    """Single ingredient parsed from pasted recipe text."""

    name: str = Field(
        description="Clean single ingredient name without quantity or unit words"
    )
    quantity: float = Field(gt=0.0, description="Numeric quantity used in the recipe")
    unit: str = Field(
        default="g",
        description="Canonical English unit: g, kg, ml, l, tbsp, tsp, cup, slice, piece, can, pack, serving",
    )
    preparation_state: PreparationState = Field(
        default=PreparationState.UNSPECIFIED,
        description="raw, cooked, ready_to_eat, or unspecified",
    )


class ParsedRecipeSchema(BaseModel):
    """Structured recipe draft extracted from user-provided recipe text."""

    name: str = Field(description="Concise recipe title")
    category: str = Field(
        default="other",
        description="Category slug: other, snack, protein, carbs, dairy, legumes, produce",
    )
    servings: float = Field(
        default=1.0,
        gt=0.0,
        description="Total yield in servings, slices, or pieces (default 1 if unspecified)",
    )
    serving_unit: str = Field(
        default="serving",
        description="Portion unit: serving, slice, or piece",
    )
    final_weight_g: float | None = Field(
        default=None,
        gt=0.0,
        description="Optional final cooked/baked weight in grams only if explicitly mentioned",
    )
    notes: str = Field(default="", description="Optional short preparation notes")
    ingredients: list[ParsedRecipeIngredientSchema] = Field(min_length=1)


def build_recipe_parse_prompts(
    raw_text: str, *, language: str = "en"
) -> tuple[str, str]:
    """Build prompts asking the LLM to extract structured ingredients & yield only."""
    system_prompt = (
        "You are a precise culinary recipe parser. Extract the recipe name, "
        "total yield (`servings` and `serving_unit` strictly from 'serving', 'slice', 'piece'), "
        "optional `final_weight_g` ONLY if a final cooked/baked total weight in grams is explicitly stated, "
        "and every ingredient with clean canonical names, numeric quantities, and canonical English units "
        "(g, kg, ml, l, tbsp, tsp, cup, slice, piece, can, pack, serving). "
        "Use `serving_unit = 'slice'` for cakes/loaves/tarts divided in slices/fette, "
        "`'piece'` for cookies/muffins/meatballs/pezzi, and `'serving'` otherwise. "
        "Never invent calories or macros and never omit an ingredient listed by the user."
    )
    user_prompt = f"User language: {language}\nRecipe text:\n{raw_text.strip()}"
    return system_prompt, user_prompt
