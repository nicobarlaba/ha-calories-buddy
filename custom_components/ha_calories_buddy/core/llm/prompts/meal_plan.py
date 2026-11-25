"""Structured recipe & meal plan schemas and prompt builders for Phase 2 Chef."""

from __future__ import annotations

from pydantic import BaseModel, Field

from custom_components.ha_calories_buddy.core.models import (
    IngredientRole,
    Macros,
    MealSlotConfig,
    PersonProfile,
    PlanGenerationOptions,
    PreparationState,
)
from custom_components.ha_calories_buddy.core.planner.preferences import (
    PreferenceContext,
)


class PlannedIngredientSpec(BaseModel):
    """Single ingredient returned by LLM prior to deterministic macro scaling."""

    name: str = Field(description="Clean ingredient name in user's language or English")
    brand: str | None = Field(default=None, description="Preferred brand if applicable")
    grams: float = Field(gt=0.0, le=2000.0, description="Initial culinary quantity in grams")
    preparation_state: PreparationState = Field(default=PreparationState.RAW)
    role: IngredientRole = Field(default=IngredientRole.PRODUCE_FIXED)


class PlannedMealSkeleton(BaseModel):
    """Structured recipe/meal skeleton returned by LLM."""

    meal_slot: str = Field(description="Exact slot name from allowed meal slots")
    title: str = Field(min_length=2, description="Short appetizing dish title")
    prep_minutes: int = Field(default=20, ge=0, le=180)
    steps: list[str] = Field(default_factory=list, description="Short concise preparation steps")
    tips: str = Field(default="", description="Optional short cooking/substitution tip")
    ingredients: list[PlannedIngredientSpec] = Field(min_length=1)


class MealSuggestionListSchema(BaseModel):
    """Structured output for on-demand suggest_meal."""

    suggestions: list[PlannedMealSkeleton] = Field(min_length=1, max_length=4)


class DayPlanProposalSchema(BaseModel):
    """Structured output for a full day's meals."""

    meals: list[PlannedMealSkeleton] = Field(min_length=1)


class SingleMealSwapSchema(BaseModel):
    """Structured output for single-slot swap or intra-day rebalance replacement."""

    meal: PlannedMealSkeleton


def _format_preference_block(ctx: PreferenceContext) -> str:
    parts: list[str] = [f"Dietary regime: {ctx.dietary_regime or 'omnivore'}."]
    if ctx.hard_constraints:
        parts.append(
            "STRICTLY FORBIDDEN (allergies/intolerances/blacklist - NEVER include): "
            + ", ".join(ctx.hard_constraints)
            + "."
        )
    if ctx.never_suggest_titles:
        parts.append(
            "Never suggest these dishes: " + ", ".join(ctx.never_suggest_titles[:10]) + "."
        )
    if ctx.disliked_items or ctx.disliked_titles:
        disliked = [*ctx.disliked_items[:12], *ctx.disliked_titles[:8]]
        parts.append("Avoid dislikes: " + ", ".join(disliked) + ".")
    if ctx.liked_items or ctx.liked_titles:
        liked = [*ctx.liked_items[:12], *ctx.liked_titles[:8]]
        parts.append("Prioritize favorites: " + ", ".join(liked) + ".")
    if ctx.preferred_brands:
        parts.append("Preferred brands: " + ", ".join(ctx.preferred_brands[:8]) + ".")
    if ctx.top_frequent_foods:
        parts.append(
            "User's staple foods in pantry: "
            + ", ".join(ctx.top_frequent_foods[:12])
            + "."
        )
    if ctx.recent_meals_last_7d:
        parts.append(
            "Recent meals (vary from these when possible): "
            + ", ".join(ctx.recent_meals_last_7d[:8])
            + "."
        )
    return "\n".join(parts)


def build_suggest_meal_prompts(
    *,
    profile: PersonProfile,
    ctx: PreferenceContext,
    meal_slot: str,
    target_macros: Macros,
    max_cook_minutes: int,
    user_hint: str = "",
    count: int = 3,
) -> tuple[str, str]:
    """Build prompts asking the Chef LLM for 1-3 tailored meal options."""
    system_prompt = (
        "You are an expert culinary nutrition chef designing practical home-cooked meals.\n"
        f"Output language for titles and steps: {profile.language}.\n"
        "Use real, simple whole/pantry ingredients with realistic starting weights in grams "
        "and accurate preparation_state ('raw', 'cooked', or 'ready_to_eat').\n"
        "Classify each ingredient's `role`: 'protein_anchor', 'carb_anchor', 'fat_anchor', "
        "'produce_fixed', or 'condiment_fixed'.\n"
        "Python will algebraically scale the anchor ingredients to hit exact macros, "
        "so focus on balanced food pairings rather than obsessing over decimal weights.\n"
        "NEVER violate dietary regime, allergies, intolerances, or blacklist.\n"
        f"{_format_preference_block(ctx)}"
    )
    hint_line = f"\nUser request/hint: {user_hint.strip()}" if user_hint.strip() else ""
    user_prompt = (
        f"Propose {count} distinct meal options for meal_slot='{meal_slot}'.\n"
        f"Target approx: {round(target_macros.kcal)} kcal | "
        f"P {round(target_macros.protein_g)}g | C {round(target_macros.carbs_g)}g | "
        f"F {round(target_macros.fat_g)}g.\n"
        f"Maximum cooking time: {max_cook_minutes} minutes.{hint_line}"
    )
    return system_prompt, user_prompt


def build_day_plan_prompts(
    *,
    profile: PersonProfile,
    ctx: PreferenceContext,
    plan_date_str: str,
    slots: list[MealSlotConfig],
    slot_targets: dict[str, Macros],
    options: PlanGenerationOptions,
    previous_titles: list[str] | None = None,
) -> tuple[str, str]:
    """Build prompts to generate one complete day's meals respecting per-slot budgets."""
    slot_lines: list[str] = []
    for s in slots:
        max_min = options.max_cook_overrides.get(s.name, s.max_cook_minutes)
        t = slot_targets.get(s.name)
        if t is not None:
            slot_lines.append(
                f"- {s.name} (max {max_min} min): ~{round(t.kcal)} kcal, "
                f"P {round(t.protein_g)}g, C {round(t.carbs_g)}g, F {round(t.fat_g)}g"
            )
        else:
            slot_lines.append(f"- {s.name} (max {max_min} min)")

    system_prompt = (
        "You are a precision personal dietitian & chef planning one full day of practical meals.\n"
        f"Output language for titles and steps: {profile.language}.\n"
        "Generate exactly ONE meal for EVERY listed meal_slot matching its exact name.\n"
        "Each meal uses concrete whole ingredients in grams, explicit preparation_state, "
        "and accurate role ('protein_anchor', 'carb_anchor', 'fat_anchor', 'produce_fixed', 'condiment_fixed').\n"
        "NEVER include any forbidden allergen, intolerance, or non-compliant food.\n"
        f"{_format_preference_block(ctx)}"
    )
    avoid_line = (
        f"\nAvoid repeating dishes already chosen this week: {', '.join(previous_titles[-12:])}."
        if previous_titles
        else ""
    )
    notes_line = f"\nAdditional notes: {options.notes.strip()}" if options.notes.strip() else ""
    prep_line = (
        "\nMeal-prep friendly mode is ON: favor batch-cookable recipes whose proteins/grains keep well."
        if options.meal_prep_mode
        else ""
    )
    user_prompt = (
        f"Plan date: {plan_date_str}\nMeal slots & macro targets:\n"
        + "\n".join(slot_lines)
        + prep_line
        + avoid_line
        + notes_line
    )
    return system_prompt, user_prompt


def build_swap_meal_prompts(
    *,
    profile: PersonProfile,
    ctx: PreferenceContext,
    meal_slot: str,
    target_macros: Macros,
    max_cook_minutes: int,
    current_title: str,
    user_instruction: str = "",
) -> tuple[str, str]:
    """Build prompts to swap or rebalance a single meal slot."""
    system_prompt = (
        "You are a personal chef creating a compliant replacement dish for one meal slot.\n"
        f"Output language for title and steps: {profile.language}.\n"
        "Set meal_slot to the exact requested slot name.\n"
        "Include concrete ingredients in grams, preparation_state, and ingredient roles.\n"
        "NEVER violate allergies, intolerances, blacklist, or dietary regime.\n"
        f"{_format_preference_block(ctx)}"
    )
    instr = (
        f"User instruction: {user_instruction.strip()}"
        if user_instruction.strip()
        else "Provide a fresh, distinct alternative."
    )
    user_prompt = (
        f"Replace '{current_title}' in meal_slot='{meal_slot}' (max {max_cook_minutes} min).\n"
        f"New target budget: ~{round(target_macros.kcal)} kcal | "
        f"P {round(target_macros.protein_g)}g | C {round(target_macros.carbs_g)}g | "
        f"F {round(target_macros.fat_g)}g.\n"
        f"{instr}"
    )
    return system_prompt, user_prompt
