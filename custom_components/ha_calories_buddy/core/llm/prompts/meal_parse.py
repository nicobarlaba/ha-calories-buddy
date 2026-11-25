"""Structured meal parsing schema and prompt builder."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from custom_components.ha_calories_buddy.core.models import (
    PersonProfile,
    PreparationState,
)


class ParsedMealItemSchema(BaseModel):
    """Single parsed food item extracted from natural language."""

    name: str = Field(description="Clean food name without quantity or cooking state words")
    brand: str | None = Field(default=None, description="Brand name if explicitly mentioned")
    quantity: float = Field(gt=0.0, description="Numeric quantity consumed")
    unit: str = Field(default="g", description="Canonical unit: g, kg, ml, tbsp, tsp, cup, slice, piece, can, pack, serving")
    preparation_state: PreparationState = Field(
        default=PreparationState.UNSPECIFIED,
        description="raw, cooked, ready_to_eat, or unspecified",
    )


class ParsedMealResponseSchema(BaseModel):
    """Structured meal parse output."""

    meal_slot: str = Field(description="Meal slot matching user's configured meal slots")
    items: list[ParsedMealItemSchema] = Field(min_length=1)


def build_meal_parse_prompts(
    profile: PersonProfile,
    utterance: str,
    frequent_foods: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Build lean, language-aware system and user prompts for meal extraction."""
    slot_names = [s.name for s in profile.meal_slots] or (
        ["Colazione", "Pranzo", "Spuntino", "Cena"]
        if profile.language == "it"
        else ["Breakfast", "Lunch", "Snack", "Dinner"]
    )
    hints = ""
    if frequent_foods:
        formatted = []
        for entry in frequent_foods[:20]:
            name = entry.get("display_name", "")
            brand = entry.get("brand")
            formatted.append(f"{name} ({brand})" if brand else str(name))
        hints = f"\nUser's frequent/preferred foods: {', '.join(formatted)}."

    system_prompt = (
        "You are a precise nutrition utterance parser. "
        "Extract every food item consumed, preserving any mentioned brand separately in `brand`, "
        "normalizing units to canonical English codes (g, kg, ml, l, tbsp, tsp, cup, slice, piece, can, pack, serving), "
        "and accurately setting `preparation_state`:\n"
        "- 'cooked' if the user says cooked/boiled/grilled/cotto/cotta/cotte/lessate/al vapore\n"
        "- 'raw' if the user says raw/dry/crudo/cruda/secco/secche\n"
        "- 'ready_to_eat' for oils, yogurt, milk, bread, fruit, nuts, canned drained items if specified\n"
        "- 'unspecified' when preparation state is not explicitly stated.\n"
        f"Available meal_slot values: {', '.join(slot_names)}."
        f"{hints}\n"
        "Never invent extra foods and never compute total calories."
    )
    user_prompt = f"User language: {profile.language}\nUtterance: {utterance}"
    return system_prompt, user_prompt
