"""Last-resort LLM food macro estimation schema and prompt builder."""

from __future__ import annotations

from pydantic import BaseModel, Field

from custom_components.ha_calories_buddy.core.models import (
    FoodItem,
    FoodSource,
    LabelStandard,
    Macros,
    PreparationState,
)
from custom_components.ha_calories_buddy.core.nutrition.atwater import check_atwater
from custom_components.ha_calories_buddy.core.nutrition.yield_factors import (
    default_yield_for_category,
)
from custom_components.ha_calories_buddy.core.storage.seeder import normalize_text


class EstimatedFoodSchema(BaseModel):
    """Estimated nutritional values per 100g when databases lack a food."""

    name: str
    category: str = Field(
        default="other",
        description="rice, pasta, grains, legumes, oats, poultry, red_meat, fish, dairy, egg, bread, oil, vegetables, fruit, nuts, snack, beverage, other",
    )
    preparation_state: PreparationState = PreparationState.READY_TO_EAT
    kcal_per_100g: float = Field(ge=0.0, le=950.0)
    protein_g_per_100g: float = Field(ge=0.0, le=100.0)
    carbs_g_per_100g: float = Field(ge=0.0, le=100.0)
    fat_g_per_100g: float = Field(ge=0.0, le=100.0)
    fiber_g_per_100g: float | None = Field(default=None, ge=0.0, le=100.0)


def build_food_estimate_prompts(
    name: str,
    brand: str | None,
    preparation_state: PreparationState,
    language: str,
) -> tuple[str, str]:
    """Build prompt requesting per-100g macronutrient estimate."""
    system_prompt = (
        "You are an evidence-based nutritional database assistant. "
        "Estimate accurate macronutrients PER 100g (using EU available-carbohydrate labeling) "
        "for the requested food item in the requested preparation state. "
        "Ensure calories satisfy ~4*P + 4*C + 9*F + 2*Fiber."
    )
    brand_part = f" (Brand: {brand})" if brand else ""
    state_part = (
        f" in state '{preparation_state.value}'"
        if preparation_state != PreparationState.UNSPECIFIED
        else ""
    )
    user_prompt = f"Language: {language}\nFood: {name}{brand_part}{state_part}"
    return system_prompt, user_prompt


def schema_to_estimated_food(
    estimated: EstimatedFoodSchema,
    brand: str | None = None,
) -> FoodItem:
    """Convert an LLM estimation schema into an unverified FoodItem."""
    macros = Macros(
        kcal=round(estimated.kcal_per_100g, 1),
        protein_g=round(estimated.protein_g_per_100g, 1),
        carbs_g=round(estimated.carbs_g_per_100g, 1),
        fat_g=round(estimated.fat_g_per_100g, 1),
        fiber_g=(
            round(estimated.fiber_g_per_100g, 1)
            if estimated.fiber_g_per_100g is not None
            else None
        ),
    )
    atwater = check_atwater(macros, LabelStandard.EU)
    return FoodItem(
        food_id="llm_estimated_temp",
        name=estimated.name,
        normalized_name=normalize_text(estimated.name),
        brand=brand,
        category=estimated.category,
        preparation_state=(
            PreparationState.READY_TO_EAT
            if estimated.preparation_state == PreparationState.UNSPECIFIED
            else estimated.preparation_state
        ),
        macros_per_100g=macros,
        label_standard=LabelStandard.EU,
        yield_factor=default_yield_for_category(estimated.category),
        source=FoodSource.LLM_ESTIMATED,
        verified=False,
        atwater_warning=atwater.warning,
    )
