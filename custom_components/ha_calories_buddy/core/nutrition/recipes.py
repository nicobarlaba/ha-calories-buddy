"""Homemade composite recipes service with deterministic nutrition and Vault sync."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from custom_components.ha_calories_buddy.core.llm.base import LLMClient
from custom_components.ha_calories_buddy.core.llm.prompts.recipe_parse import (
    ParsedRecipeSchema,
    build_recipe_parse_prompts,
)
from custom_components.ha_calories_buddy.core.logging import get_logger, log_event
from custom_components.ha_calories_buddy.core.models import (
    FoodItem,
    FoodPortion,
    FoodSource,
    HomemadeRecipe,
    LabelStandard,
    PersonProfile,
    PreparationState,
    RecipeIngredient,
    RecipeIngredientInput,
)
from custom_components.ha_calories_buddy.core.nutrition.calculator import (
    compute_macros_for_grams,
    sum_macros,
)
from custom_components.ha_calories_buddy.core.nutrition.resolver import FoodResolver
from custom_components.ha_calories_buddy.core.nutrition.units import (
    convert_to_grams,
    normalize_unit,
)
from custom_components.ha_calories_buddy.core.nutrition.yield_factors import (
    convert_to_reference_grams,
    resolve_preparation_state,
)
from custom_components.ha_calories_buddy.core.planner.meal_parser import (
    LLMFoodEstimatorAdapter,
)
from custom_components.ha_calories_buddy.core.storage.food_repo import FoodRepository
from custom_components.ha_calories_buddy.core.storage.recipe_repo import (
    RecipeRepository,
    compute_recipe_breakdown,
)
from custom_components.ha_calories_buddy.core.storage.seeder import normalize_text

_LOGGER = get_logger(__name__)
_ALLOWED_RECIPE_UNITS = frozenset({"serving", "slice", "piece"})


def normalize_recipe_serving_unit(raw_unit: str | None) -> str:
    """Normalize portion unit to one of 'serving', 'slice', or 'piece'."""
    norm = normalize_unit(raw_unit or "serving")
    if norm in _ALLOWED_RECIPE_UNITS:
        return norm
    return "serving"


def parse_recipe_ingredient_inputs(
    raw_items: list[dict[str, Any]],
) -> list[RecipeIngredientInput]:
    """Convert raw service/WebSocket ingredient dicts into validated inputs."""
    result: list[RecipeIngredientInput] = []
    for raw in raw_items:
        state_val = str(
            raw.get("state") or PreparationState.UNSPECIFIED.value
        ).lower()
        state = (
            PreparationState(state_val)
            if state_val in ("raw", "cooked", "ready_to_eat", "unspecified")
            else PreparationState.UNSPECIFIED
        )
        result.append(
            RecipeIngredientInput(
                food_id=str(raw["food_id"]) if raw.get("food_id") else None,
                name=str(raw["name"]).strip(),
                quantity=float(raw["quantity"]),
                unit=str(raw.get("unit") or "g").strip(),
                state=state,
            )
        )
    return result


class RecipeService:
    """Manages homemade recipes, ingredient nutrition aggregation, and Food Vault sync."""

    def __init__(
        self,
        *,
        recipe_repo: RecipeRepository,
        food_repo: FoodRepository,
        resolver: FoodResolver,
        llm_client_getter: Callable[[PersonProfile], LLMClient | None] | None = None,
    ) -> None:
        self._recipe_repo = recipe_repo
        self._food_repo = food_repo
        self._resolver = resolver
        self._get_llm = llm_client_getter

    async def save_recipe(
        self,
        profile: PersonProfile,
        *,
        recipe_id: str | None = None,
        name: str,
        ingredients: list[RecipeIngredientInput],
        servings: float = 1.0,
        serving_unit: str = "serving",
        final_weight_g: float | None = None,
        category: str = "other",
        allow_in_planner: bool = False,
        notes: str = "",
    ) -> HomemadeRecipe:
        """Resolve all ingredients, compute exact yield macros, and sync to Global Food Vault."""
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Recipe name cannot be empty")
        if not ingredients:
            raise ValueError("Recipe must include at least one ingredient")
        if float(servings) <= 0.0:
            raise ValueError("Recipe servings must be greater than zero")
        if final_weight_g is not None and float(final_weight_g) <= 0.0:
            raise ValueError("Final cooked weight must be greater than zero")

        existing = (
            await self._recipe_repo.get_recipe(recipe_id) if recipe_id else None
        )
        target_recipe_id = (
            existing.recipe_id if existing else f"rec_{uuid.uuid4().hex[:12]}"
        )
        target_food_id = (
            existing.food_id if existing else f"recipe_{uuid.uuid4().hex[:12]}"
        )
        norm_unit = normalize_recipe_serving_unit(serving_unit)

        llm_client = self._get_llm(profile) if self._get_llm else None
        estimator = (
            LLMFoodEstimatorAdapter(llm_client) if llm_client is not None else None
        )

        resolved_ings: list[RecipeIngredient] = []
        for idx, ing in enumerate(ingredients):
            food = await self._resolver.resolve_food_candidate(
                person_id=profile.person_id,
                name=ing.name,
                food_id=ing.food_id,
                state=ing.state,
                language=profile.language,
                llm_estimator=estimator,
            )
            if food is None:
                raise ValueError(f"Ingredient not found in Food Vault: {ing.name}")

            canon_unit = normalize_unit(ing.unit or "g")
            input_grams, _ = convert_to_grams(ing.quantity, canon_unit, food)
            if input_grams <= 0.0:
                raise ValueError(f"Invalid quantity for ingredient: {ing.name}")

            eff_state, _ = resolve_preparation_state(
                category=food.category,
                input_grams=input_grams,
                input_state=ing.state,
                ref_state=food.preparation_state,
            )
            ref_grams = convert_to_reference_grams(
                input_grams=input_grams,
                effective_state=eff_state,
                ref_state=food.preparation_state,
                yield_factor=food.yield_factor,
            )
            macros = compute_macros_for_grams(food.macros_per_100g, ref_grams)
            resolved_ings.append(
                RecipeIngredient(
                    ingredient_id=f"ring_{uuid.uuid4().hex[:12]}",
                    recipe_id=target_recipe_id,
                    sort_order=idx,
                    food_id=(
                        None
                        if food.source == FoodSource.LLM_ESTIMATED
                        else food.food_id
                    ),
                    display_name=food.localized_name(profile.language),
                    input_quantity=float(ing.quantity),
                    input_unit=canon_unit,
                    input_state=eff_state,
                    grams=round(input_grams, 1),
                    macros=macros,
                )
            )

        raw_total_grams = round(sum(i.grams for i in resolved_ings), 1)
        effective_weight_g = (
            round(float(final_weight_g), 1)
            if final_weight_g is not None
            else raw_total_grams
        )
        total_macros = sum_macros([i.macros for i in resolved_ings])
        grams_per_serving, macros_per_100g, macros_per_serving = (
            compute_recipe_breakdown(
                total_macros, effective_weight_g, float(servings)
            )
        )

        # Register both chosen unit (e.g. slice/piece) and 'serving' for deterministic Assist/manual resolution
        portions = [
            FoodPortion(
                food_id=target_food_id,
                unit_name=norm_unit,
                grams=grams_per_serving,
                description=f"1 {norm_unit} ({float(servings):g} total)",
            )
        ]
        if norm_unit != "serving":
            portions.append(
                FoodPortion(
                    food_id=target_food_id,
                    unit_name="serving",
                    grams=grams_per_serving,
                    description=f"1 serving (1 {norm_unit})",
                )
            )

        vault_food = FoodItem(
            food_id=target_food_id,
            name=clean_name,
            name_it=clean_name,
            normalized_name=normalize_text(clean_name),
            aliases=[normalize_text(clean_name)],
            category=category.strip().lower() or "other",
            preparation_state=PreparationState.READY_TO_EAT,
            macros_per_100g=macros_per_100g,
            label_standard=LabelStandard.EU,
            yield_factor=1.0,
            source=FoodSource.RECIPE,
            verified=True,
            atwater_warning=False,
            portions=portions,
        )
        await self._food_repo.upsert_global_food(vault_food, replace_portions=True)

        now = datetime.now(UTC)
        recipe = HomemadeRecipe(
            recipe_id=target_recipe_id,
            food_id=target_food_id,
            name=clean_name,
            normalized_name=normalize_text(clean_name),
            category=vault_food.category,
            servings=float(servings),
            serving_unit=norm_unit,
            raw_total_grams=raw_total_grams,
            final_weight_g=(
                round(float(final_weight_g), 1)
                if final_weight_g is not None
                else None
            ),
            effective_weight_g=effective_weight_g,
            grams_per_serving=grams_per_serving,
            total_macros=total_macros,
            macros_per_100g=macros_per_100g,
            macros_per_serving=macros_per_serving,
            allow_in_planner=bool(allow_in_planner),
            notes=notes.strip(),
            created_by_person_id=(
                existing.created_by_person_id if existing else profile.person_id
            ),
            ingredients=resolved_ings,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        saved = await self._recipe_repo.save_recipe(recipe)
        log_event(
            _LOGGER,
            logging.INFO,
            "homemade_recipe_saved",
            person_id=profile.person_id,
            servings=servings,
            serving_unit=norm_unit,
            ingredient_count=len(resolved_ings),
        )
        return saved

    async def delete_recipe(self, recipe_id: str) -> bool:
        """Delete a homemade recipe and its linked Vault food (preserving historical meal logs)."""
        food_id = await self._recipe_repo.delete_recipe(recipe_id)
        if food_id is None:
            return False
        await self._food_repo.delete_global_food(food_id)
        log_event(
            _LOGGER,
            logging.INFO,
            "homemade_recipe_deleted",
        )
        return True

    async def parse_recipe_from_text(
        self, profile: PersonProfile, raw_text: str
    ) -> dict[str, Any]:
        """Use the active LLM to turn pasted recipe text into a Vault-linked draft."""
        if not raw_text.strip():
            raise ValueError("Recipe text cannot be empty")
        llm_client = self._get_llm(profile) if self._get_llm else None
        if llm_client is None:
            raise RuntimeError("No active LLM provider configured")

        sys_prompt, user_prompt = build_recipe_parse_prompts(
            raw_text, language=profile.language
        )
        parsed = await llm_client.generate_structured(
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            response_model=ParsedRecipeSchema,
        )

        draft_ingredients: list[dict[str, Any]] = []
        for ing in parsed.ingredients:
            food = await self._resolver.resolve_food_candidate(
                person_id=profile.person_id,
                name=ing.name,
                state=ing.preparation_state,
                language=profile.language,
                llm_estimator=None,
            )
            canon_unit = normalize_unit(ing.unit or "g")
            grams, _ = convert_to_grams(ing.quantity, canon_unit, food)
            draft_ingredients.append(
                {
                    "food_id": food.food_id if food is not None else None,
                    "raw_name": ing.name.strip(),
                    "name": (
                        food.localized_name(profile.language)
                        if food is not None
                        else ing.name.strip()
                    ),
                    "matched_brand": (food.brand if food is not None else None),
                    "category": (food.category if food is not None else "other"),
                    "food_portions": (dict(food.food_portions) if food is not None else {}),
                    "person_portions": (
                        dict(food.person_portions) if food is not None else {}
                    ),
                    "quantity": float(ing.quantity),
                    "unit": canon_unit,
                    "state": (
                        ing.preparation_state.value
                        if ing.preparation_state != PreparationState.UNSPECIFIED
                        else (
                            food.preparation_state.value
                            if food is not None
                            else PreparationState.UNSPECIFIED.value
                        )
                    ),
                    "grams_estimate": round(grams, 1),
                    "macros_per_100g": (
                        food.macros_per_100g.model_dump(mode="json")
                        if food is not None
                        else None
                    ),
                    "yield_factor": food.yield_factor if food is not None else 1.0,
                    "ref_state": (
                        food.preparation_state.value
                        if food is not None
                        else PreparationState.READY_TO_EAT.value
                    ),
                    "resolved": food is not None,
                }
            )

        return {
            "name": parsed.name.strip(),
            "category": parsed.category.strip().lower() or "other",
            "servings": float(parsed.servings),
            "serving_unit": normalize_recipe_serving_unit(parsed.serving_unit),
            "final_weight_g": (
                round(float(parsed.final_weight_g), 1)
                if parsed.final_weight_g is not None
                else None
            ),
            "notes": parsed.notes.strip(),
            "ingredients": draft_ingredients,
        }
