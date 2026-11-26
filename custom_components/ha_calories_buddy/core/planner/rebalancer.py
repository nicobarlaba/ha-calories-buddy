"""Intra-day and physiologically safe multi-day dynamic meal plan rebalancer."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from custom_components.ha_calories_buddy.core.i18n import format_message
from custom_components.ha_calories_buddy.core.llm.base import LLMError
from custom_components.ha_calories_buddy.core.logging import get_logger, log_event
from custom_components.ha_calories_buddy.core.models import (
    Macros,
    MealPlanItem,
    PersonProfile,
    PlanItemStatus,
    PlannedIngredient,
    RebalanceResult,
    RecipeDetail,
    Sex,
)
from custom_components.ha_calories_buddy.core.nutrition.resolver import FoodResolver
from custom_components.ha_calories_buddy.core.physiology.targets import (
    TargetResult,
    default_fiber_target_g,
)
from custom_components.ha_calories_buddy.core.planner.macro_scaler import (
    ScalableIngredientInput,
    scale_day_meals,
)
from custom_components.ha_calories_buddy.core.planner.meal_planner import (
    MealPlannerService,
)
from custom_components.ha_calories_buddy.core.planner.slot_resolver import (
    extract_logged_slot_keys,
)
from custom_components.ha_calories_buddy.core.storage.meal_repo import MealRepository
from custom_components.ha_calories_buddy.core.storage.plan_repo import (
    MealPlanRepository,
)
from custom_components.ha_calories_buddy.core.storage.seeder import normalize_text

_LOGGER = get_logger(__name__)


class MealRebalancerService:
    """Dynamically adjusts planned meals when actual intake diverges from targets."""

    def __init__(
        self,
        *,
        plan_repo: MealPlanRepository,
        meal_repo: MealRepository,
        resolver: FoodResolver,
        planner_service: MealPlannerService,
    ) -> None:
        self._plan_repo = plan_repo
        self._meal_repo = meal_repo
        self._resolver = resolver
        self._planner = planner_service

    async def rebalance_intraday(
        self,
        profile: PersonProfile,
        *,
        target_date: date,
        daily_targets: TargetResult,
        allow_llm_swap: bool = True,
    ) -> RebalanceResult:
        """Recalibrate remaining planned meals on target_date to close the daily macro gap."""
        totals = await self._meal_repo.get_daily_totals(profile.person_id, target_date)
        consumed = totals.consumed
        remaining_budget = Macros(
            kcal=max(0.0, daily_targets.target_kcal - consumed.kcal),
            protein_g=max(0.0, daily_targets.protein_g - consumed.protein_g),
            carbs_g=max(0.0, daily_targets.carbs_g - consumed.carbs_g),
            fat_g=max(0.0, daily_targets.fat_g - consumed.fat_g),
            fiber_g=max(0.0, daily_targets.fiber_g - (consumed.fiber_g or 0.0)),
        )

        logged_meals = await self._meal_repo.list_meals_for_date(
            profile.person_id, target_date
        )
        logged_slots = extract_logged_slot_keys(logged_meals)

        plan_day = await self._plan_repo.get_plan_day(profile.person_id, target_date)
        remaining_items = (
            [
                it
                for it in plan_day.items
                if it.status == PlanItemStatus.PLANNED
                and normalize_text(it.meal_slot) not in logged_slots
            ]
            if plan_day is not None
            else []
        )

        if not remaining_items:
            return RebalanceResult(
                mode="no_items",
                remaining_before=remaining_budget,
                remaining_after=remaining_budget,
                safety_warnings=[],
                summary_message=format_message(
                    profile.language, "rebalance_no_items"
                ),
            )

        warnings: list[str] = []
        raw_remaining_kcal = daily_targets.target_kcal - consumed.kcal
        if raw_remaining_kcal < 250.0:
            warnings.append("over_budget_suggest_multiday")

        # Stage 1: fast deterministic re-scaling (0 LLM calls)
        rescaled_items, fit_warning = await self._deterministic_rescale_items(
            profile=profile,
            items=remaining_items,
            remaining_budget=remaining_budget,
        )

        # Stage 2: swap remaining slots via Chef LLM if scaling hit hard culinary limits
        used_swap = False
        if (
            fit_warning
            and allow_llm_swap
            and remaining_budget.kcal >= 250.0
        ):
            try:
                swapped: list[MealPlanItem] = []
                total_weight = sum(max(it.macros.kcal, 100.0) for it in remaining_items)
                for it in remaining_items:
                    share = max(it.macros.kcal, 100.0) / total_weight
                    slot_target = Macros(
                        kcal=max(120.0, remaining_budget.kcal * share),
                        protein_g=max(5.0, remaining_budget.protein_g * share),
                        carbs_g=max(0.0, remaining_budget.carbs_g * share),
                        fat_g=max(2.0, remaining_budget.fat_g * share),
                        fiber_g=(remaining_budget.fiber_g or 0.0) * share,
                    )
                    updated_it = await self._planner.swap_plan_meal(
                        profile,
                        it.plan_item_id,
                        user_instruction=(
                            "Rebalance for remaining daily macro budget after earlier intake; "
                            "favor lean high-protein, high-satiety ingredients fitting the exact macro gap."
                        ),
                        target_override=slot_target,
                    )
                    swapped.append(updated_it)
                rescaled_items = swapped
                used_swap = True
            except (RuntimeError, LLMError):
                # LLM unconfigured or swap failed: keep best-effort deterministic rescaled items
                warnings.append("llm_unavailable_kept_rescaled")

        if not used_swap:
            for item in rescaled_items:
                await self._plan_repo.replace_plan_item(item)

        log_event(
            _LOGGER,
            logging.INFO,
            "meal_plan_rebalanced_intraday",
            person_id=profile.person_id,
            mode="intraday_swap" if used_swap else "intraday_rescale",
            items=len(rescaled_items),
        )
        msg_key = "rebalance_swap_ok" if used_swap else "rebalance_intraday_ok"
        return RebalanceResult(
            mode="intraday_swap" if used_swap else "intraday_rescale",
            updated_items=rescaled_items,
            updated_days=[target_date],
            remaining_before=remaining_budget,
            remaining_after=remaining_budget,
            safety_warnings=warnings,
            summary_message=format_message(
                profile.language, msg_key, kcal=round(remaining_budget.kcal)
            ),
        )

    async def rebalance_multiday(
        self,
        profile: PersonProfile,
        *,
        start_date: date,
        excess_kcal: float,
        bmr_kcal: float,
        weight_kg: float,
        spread_days: int = 3,
    ) -> RebalanceResult:
        """Spread a caloric surplus/deficit over the next 2-4 days respecting physiological floors."""
        clamped_days = max(2, min(4, int(spread_days)))
        kcal_floor = max(
            1200.0 if profile.sex == Sex.FEMALE else 1500.0,
            0.85 * bmr_kcal,
        )
        fat_floor_g = 0.65 * weight_kg
        delta_per_day = -excess_kcal / clamped_days

        updated_items: list[MealPlanItem] = []
        updated_days: list[date] = []
        warnings: list[str] = []

        for offset in range(1, clamped_days + 1):
            day_date = start_date + timedelta(days=offset)
            plan_day = await self._plan_repo.get_plan_day(profile.person_id, day_date)
            if plan_day is None:
                continue

            cur = plan_day.target_macros
            raw_new_kcal = cur.kcal + delta_per_day
            if raw_new_kcal < kcal_floor:
                new_kcal = kcal_floor
                warnings.append("daily_calorie_floor_enforced")
            else:
                new_kcal = raw_new_kcal

            actual_delta_kcal = new_kcal - cur.kcal
            # Preserve protein target; distribute caloric adjustment across carbs (70%) and fats (30%)
            new_fat = max(fat_floor_g, cur.fat_g + (0.30 * actual_delta_kcal) / 9.0)
            fat_kcal_delta = (new_fat - cur.fat_g) * 9.0
            carbs_kcal_delta = actual_delta_kcal - fat_kcal_delta
            new_carbs = max(30.0, cur.carbs_g + (carbs_kcal_delta / 4.0))

            new_target = Macros(
                kcal=round(new_kcal, 1),
                protein_g=round(cur.protein_g, 1),
                carbs_g=round(new_carbs, 1),
                fat_g=round(new_fat, 1),
                fiber_g=cur.fiber_g or default_fiber_target_g(new_kcal),
            )
            await self._plan_repo.update_plan_day_targets(
                plan_day.plan_day_id, new_target
            )

            planned_items = [
                it for it in plan_day.items if it.status == PlanItemStatus.PLANNED
            ]
            if planned_items:
                day_rescaled, _ = await self._deterministic_rescale_items(
                    profile=profile,
                    items=planned_items,
                    remaining_budget=new_target,
                )
                for it in day_rescaled:
                    await self._plan_repo.replace_plan_item(it)
                updated_items.extend(day_rescaled)
            updated_days.append(day_date)

        zero_macros = Macros(kcal=0.0, protein_g=0.0, carbs_g=0.0, fat_g=0.0, fiber_g=0.0)
        log_event(
            _LOGGER,
            logging.INFO,
            "meal_plan_rebalanced_multiday",
            person_id=profile.person_id,
            days=len(updated_days),
        )
        return RebalanceResult(
            mode="multiday_smooth",
            updated_items=updated_items,
            updated_days=updated_days,
            remaining_before=zero_macros,
            remaining_after=zero_macros,
            safety_warnings=list(dict.fromkeys(warnings)),
            summary_message=format_message(
                profile.language, "rebalance_multiday_ok", days=len(updated_days)
            ),
        )

    async def _deterministic_rescale_items(
        self,
        *,
        profile: PersonProfile,
        items: list[MealPlanItem],
        remaining_budget: Macros,
    ) -> tuple[list[MealPlanItem], bool]:
        total_weight = sum(max(it.macros.kcal, 100.0) for it in items)
        skeletons: list[tuple[str, list[ScalableIngredientInput], Macros]] = []

        for it in items:
            share = max(it.macros.kcal, 100.0) / total_weight
            slot_budget = Macros(
                kcal=max(80.0, remaining_budget.kcal * share),
                protein_g=max(2.0, remaining_budget.protein_g * share),
                carbs_g=max(0.0, remaining_budget.carbs_g * share),
                fat_g=max(1.0, remaining_budget.fat_g * share),
                fiber_g=(remaining_budget.fiber_g or 0.0) * share,
            )
            raw_ings = json.loads(it.ingredients_json or "[]")
            scalable: list[ScalableIngredientInput] = []
            for raw in raw_ings:
                ing = PlannedIngredient.model_validate(raw)
                food = await self._resolver.resolve_food_candidate(
                    person_id=profile.person_id,
                    name=ing.name,
                    brand=ing.brand,
                    food_id=ing.food_id,
                    state=ing.preparation_state,
                    language=profile.language,
                )
                scalable.append(
                    ScalableIngredientInput(
                        name=ing.name,
                        food=food,
                        proposed_grams=ing.grams,
                        preparation_state=ing.preparation_state,
                        role=ing.role,
                        brand=ing.brand,
                    )
                )
            skeletons.append((it.meal_slot, scalable, slot_budget))

        scaled_meals, _, day_warn = scale_day_meals(skeletons, remaining_budget)
        result: list[MealPlanItem] = []
        for idx, it in enumerate(items):
            _, scaled_ings, new_macros, meal_warn = scaled_meals[idx]
            try:
                recipe = RecipeDetail.model_validate_json(it.recipe_json or "{}")
            except Exception:  # noqa: BLE001
                recipe = RecipeDetail(prep_minutes=it.prep_minutes)
            recipe.macro_fit_warning = meal_warn
            result.append(
                MealPlanItem(
                    plan_item_id=it.plan_item_id,
                    plan_day_id=it.plan_day_id,
                    meal_slot=it.meal_slot,
                    title=it.title,
                    recipe_json=recipe.model_dump_json(),
                    ingredients_json=json.dumps(
                        [ing.model_dump(mode="json") for ing in scaled_ings]
                    ),
                    macros=new_macros,
                    prep_minutes=it.prep_minutes,
                    macro_fit_warning=meal_warn,
                    status=it.status,
                )
            )
        return result, day_warn
