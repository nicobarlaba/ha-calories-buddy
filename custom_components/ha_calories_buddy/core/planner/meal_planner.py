"""Phase 2 Chef & Dietitian service: recipe suggestions, 7-day plans, swaps, promotion."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from custom_components.ha_calories_buddy.core.llm.base import LLMClient
from custom_components.ha_calories_buddy.core.llm.prompts.meal_plan import (
    DayPlanProposalSchema,
    MealSuggestionListSchema,
    PlannedMealSkeleton,
    SingleMealSwapSchema,
    build_day_plan_prompts,
    build_suggest_meal_prompts,
    build_swap_meal_prompts,
)
from custom_components.ha_calories_buddy.core.logging import get_logger, log_event
from custom_components.ha_calories_buddy.core.models import (
    EffectiveFood,
    Macros,
    MealLog,
    MealLogItem,
    MealPlan,
    MealPlanDay,
    MealPlanItem,
    MealSlotConfig,
    MealStatus,
    MealSuggestion,
    PersonProfile,
    PlanGenerationOptions,
    PlanItemStatus,
    PlannedIngredient,
    PreparationState,
    RecipeDetail,
)
from custom_components.ha_calories_buddy.core.nutrition.calculator import sum_macros
from custom_components.ha_calories_buddy.core.nutrition.resolver import FoodResolver
from custom_components.ha_calories_buddy.core.physiology.targets import TargetResult
from custom_components.ha_calories_buddy.core.planner.macro_scaler import (
    ScalableIngredientInput,
    allocate_slot_targets,
    classify_ingredient_role,
    scale_day_meals,
    scale_meal_ingredients,
)
from custom_components.ha_calories_buddy.core.planner.preferences import (
    PreferenceContext,
    PreferenceEngine,
)
from custom_components.ha_calories_buddy.core.planner.slot_resolver import (
    default_slots_for_language,
    resolve_meal_slot,
)
from custom_components.ha_calories_buddy.core.storage.meal_repo import MealRepository
from custom_components.ha_calories_buddy.core.storage.plan_repo import (
    MealPlanRepository,
)

_LOGGER = get_logger(__name__)
MAX_SAFETY_RETRIES = 2


class HardConstraintViolationError(Exception):
    """Raised when an LLM fails to produce allergen/regime-compliant meals after retries."""

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        super().__init__(
            "Generated meal violated dietary hard constraints: "
            + "; ".join(violations[:5])
        )


class MealPlannerService:
    """Orchestrates AI recipe proposals, zero-trust allergen filtering, and macro scaling."""

    def __init__(
        self,
        *,
        plan_repo: MealPlanRepository,
        meal_repo: MealRepository,
        preference_engine: PreferenceEngine,
        resolver: FoodResolver,
        llm_client_getter: Callable[[PersonProfile], LLMClient | None],
    ) -> None:
        self._plan_repo = plan_repo
        self._meal_repo = meal_repo
        self._pref_engine = preference_engine
        self._resolver = resolver
        self._get_llm = llm_client_getter

    async def suggest_meal(
        self,
        profile: PersonProfile,
        *,
        meal_slot: str | None = None,
        target_macros: Macros,
        user_hint: str = "",
        max_cook_minutes: int | None = None,
        count: int = 3,
    ) -> list[MealSuggestion]:
        """Generate 1-3 constraint-verified, macro-scaled meal suggestions on demand."""
        llm_client = self._require_llm(profile)
        slots = profile.meal_slots or default_slots_for_language(profile.language)
        chosen_slot = resolve_meal_slot(
            meal_slot, slots, language=profile.language
        )
        slot_cfg = next((s for s in slots if s.name == chosen_slot), slots[0])
        cook_limit = (
            max_cook_minutes
            if max_cook_minutes is not None
            else slot_cfg.max_cook_minutes
        )

        ctx = await self._pref_engine.build_context(profile)
        sys_prompt, user_prompt = build_suggest_meal_prompts(
            profile=profile,
            ctx=ctx,
            meal_slot=chosen_slot,
            target_macros=target_macros,
            max_cook_minutes=cook_limit,
            user_hint=user_hint,
            count=max(1, min(3, count)),
        )

        safe_skeletons: list[tuple[PlannedMealSkeleton, list[ScalableIngredientInput]]] = []
        last_violations: list[str] = []
        current_prompt = user_prompt

        for _attempt in range(MAX_SAFETY_RETRIES + 1):
            resp = await llm_client.generate_structured(
                system_prompt=sys_prompt,
                user_prompt=current_prompt,
                response_model=MealSuggestionListSchema,
            )
            passed, violations = await self._validate_and_resolve_skeletons(
                profile=profile,
                skeletons=resp.suggestions,
                ctx=ctx,
            )
            safe_skeletons.extend(passed)
            if safe_skeletons and not violations:
                break
            if violations:
                last_violations = violations
                current_prompt = (
                    f"{user_prompt}\n\n"
                    "REJECTED DUE TO FORBIDDEN INGREDIENTS/ALLERGENS: "
                    f"{', '.join(violations)}. "
                    "Replace all forbidden items immediately."
                )

        if not safe_skeletons:
            raise HardConstraintViolationError(last_violations or ["no_safe_suggestion"])

        suggestions: list[MealSuggestion] = []
        for skel, resolved_ings in safe_skeletons[:count]:
            scaled_ings, macros, macro_warn = scale_meal_ingredients(
                resolved_ings, target_macros
            )
            suggestions.append(
                MealSuggestion(
                    title=skel.title,
                    meal_slot=chosen_slot,
                    prep_minutes=skel.prep_minutes,
                    steps=skel.steps,
                    tips=skel.tips,
                    ingredients=scaled_ings,
                    macros=macros,
                    macro_fit_warning=macro_warn,
                )
            )
        return suggestions

    async def generate_meal_plan(
        self,
        profile: PersonProfile,
        *,
        start_date: date,
        daily_targets: TargetResult,
        options: PlanGenerationOptions,
    ) -> MealPlan:
        """Generate a 1-7 day meal plan day-by-day with safety & <=5% macro scaling."""
        llm_client = self._require_llm(profile)
        slots = profile.meal_slots or default_slots_for_language(profile.language)
        slot_targets = allocate_slot_targets(daily_targets, slots)
        ctx = await self._pref_engine.build_context(profile)

        day_target_macros = Macros(
            kcal=daily_targets.target_kcal,
            protein_g=daily_targets.protein_g,
            carbs_g=daily_targets.carbs_g,
            fat_g=daily_targets.fat_g,
            fiber_g=daily_targets.fiber_g,
        )

        plan_id = f"plan_{uuid.uuid4().hex[:12]}"
        days: list[MealPlanDay] = []
        generated_titles: list[str] = []
        prev_day_skeletons: list[tuple[str, str, int, list[str], str, list[ScalableIngredientInput]]] | None = None

        for offset in range(options.num_days):
            cur_date = start_date + timedelta(days=offset)
            plan_day_id = f"day_{uuid.uuid4().hex[:12]}"

            # Meal-prep reuse on alternate days when requested
            if options.meal_prep_mode and prev_day_skeletons is not None and offset % 2 == 1:
                slot_inputs = [
                    (slot_name, ings, slot_targets.get(slot_name, day_target_macros))
                    for slot_name, _, _, _, _, ings in prev_day_skeletons
                ]
                scaled_meals, _, _ = scale_day_meals(slot_inputs, day_target_macros)
                day_items: list[MealPlanItem] = []
                for idx, (slot_name, title, prep_min, steps, tips, _) in enumerate(
                    prev_day_skeletons
                ):
                    _, scaled_ings, meal_macros, meal_warn = scaled_meals[idx]
                    day_items.append(
                        self._build_plan_item(
                            plan_day_id=plan_day_id,
                            meal_slot=slot_name,
                            title=title,
                            prep_minutes=prep_min,
                            steps=steps,
                            tips=tips,
                            ingredients=scaled_ings,
                            macros=meal_macros,
                            macro_fit_warning=meal_warn,
                        )
                    )
                days.append(
                    MealPlanDay(
                        plan_day_id=plan_day_id,
                        plan_id=plan_id,
                        plan_date=cur_date,
                        target_macros=day_target_macros,
                        items=day_items,
                    )
                )
                continue

            sys_prompt, user_prompt = build_day_plan_prompts(
                profile=profile,
                ctx=ctx,
                plan_date_str=cur_date.isoformat(),
                slots=slots,
                slot_targets=slot_targets,
                options=options,
                previous_titles=generated_titles,
            )

            day_resolved = await self._generate_safe_day(
                llm_client=llm_client,
                profile=profile,
                ctx=ctx,
                slots=slots,
                system_prompt=sys_prompt,
                user_prompt=user_prompt,
            )

            slot_inputs = [
                (
                    skel.meal_slot,
                    ings,
                    slot_targets.get(skel.meal_slot, day_target_macros),
                )
                for skel, ings in day_resolved
            ]
            scaled_meals, _, _ = scale_day_meals(slot_inputs, day_target_macros)

            day_items = []
            prev_day_skeletons = []
            for idx, (skel, ings) in enumerate(day_resolved):
                _, scaled_ings, meal_macros, meal_warn = scaled_meals[idx]
                generated_titles.append(skel.title)
                day_items.append(
                    self._build_plan_item(
                        plan_day_id=plan_day_id,
                        meal_slot=skel.meal_slot,
                        title=skel.title,
                        prep_minutes=skel.prep_minutes,
                        steps=skel.steps,
                        tips=skel.tips,
                        ingredients=scaled_ings,
                        macros=meal_macros,
                        macro_fit_warning=meal_warn,
                    )
                )
                prev_day_skeletons.append(
                    (skel.meal_slot, skel.title, skel.prep_minutes, skel.steps, skel.tips, ings)
                )

            days.append(
                MealPlanDay(
                    plan_day_id=plan_day_id,
                    plan_id=plan_id,
                    plan_date=cur_date,
                    target_macros=day_target_macros,
                    items=day_items,
                )
            )

        plan = MealPlan(
            plan_id=plan_id,
            person_id=profile.person_id,
            start_date=start_date,
            end_date=start_date + timedelta(days=options.num_days - 1),
            status="active",
            created_at=datetime.now(UTC),
            days=days,
        )
        await self._plan_repo.save_plan(plan)
        log_event(
            _LOGGER,
            logging.INFO,
            "meal_plan_generated",
            person_id=profile.person_id,
            days=len(days),
            meal_prep=options.meal_prep_mode,
        )
        return plan

    async def swap_plan_meal(
        self,
        profile: PersonProfile,
        plan_item_id: str,
        *,
        user_instruction: str = "",
        target_override: Macros | None = None,
    ) -> MealPlanItem:
        """Replace a single planned meal slot while preserving remaining day targets."""
        llm_client = self._require_llm(profile)
        found = await self._plan_repo.get_plan_item(profile.person_id, plan_item_id)
        if found is None:
            raise ValueError(f"Planned item not found: {plan_item_id}")

        plan_day, current_item = found
        slots = profile.meal_slots or default_slots_for_language(profile.language)
        slot_cfg = next(
            (s for s in slots if s.name == current_item.meal_slot),
            slots[0],
        )

        if target_override is not None:
            slot_budget = target_override
        else:
            others_macros = sum_macros(
                [
                    it.macros
                    for it in plan_day.items
                    if it.plan_item_id != plan_item_id
                    and it.status != PlanItemStatus.SKIPPED
                ]
            )
            slot_budget = Macros(
                kcal=max(120.0, plan_day.target_macros.kcal - others_macros.kcal),
                protein_g=max(5.0, plan_day.target_macros.protein_g - others_macros.protein_g),
                carbs_g=max(0.0, plan_day.target_macros.carbs_g - others_macros.carbs_g),
                fat_g=max(2.0, plan_day.target_macros.fat_g - others_macros.fat_g),
                fiber_g=(
                    max(0.0, (plan_day.target_macros.fiber_g or 25.0) - (others_macros.fiber_g or 0.0))
                ),
            )

        ctx = await self._pref_engine.build_context(profile)
        sys_prompt, user_prompt = build_swap_meal_prompts(
            profile=profile,
            ctx=ctx,
            meal_slot=current_item.meal_slot,
            target_macros=slot_budget,
            max_cook_minutes=slot_cfg.max_cook_minutes,
            current_title=current_item.title,
            user_instruction=user_instruction,
        )

        new_skel, resolved_ings = await self._generate_safe_single_swap(
            llm_client=llm_client,
            profile=profile,
            ctx=ctx,
            slot_name=current_item.meal_slot,
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
        )
        scaled_ings, meal_macros, meal_warn = scale_meal_ingredients(
            resolved_ings, slot_budget
        )
        updated_item = self._build_plan_item(
            plan_day_id=plan_day.plan_day_id,
            plan_item_id=current_item.plan_item_id,
            meal_slot=current_item.meal_slot,
            title=new_skel.title,
            prep_minutes=new_skel.prep_minutes,
            steps=new_skel.steps,
            tips=new_skel.tips,
            ingredients=scaled_ings,
            macros=meal_macros,
            macro_fit_warning=meal_warn,
            status=PlanItemStatus.PLANNED,
        )
        await self._plan_repo.replace_plan_item(updated_item)
        return updated_item

    async def promote_plan_meal(
        self,
        profile: PersonProfile,
        plan_item_id: str,
        *,
        actual_scale: float = 1.0,
    ) -> MealLog:
        """Convert a planned meal item into a confirmed MealLog in one tap."""
        found = await self._plan_repo.get_plan_item(profile.person_id, plan_item_id)
        if found is None:
            raise ValueError(f"Planned item not found: {plan_item_id}")

        plan_day, plan_item = found
        scale = max(0.1, min(5.0, float(actual_scale)))

        raw_ings = json.loads(plan_item.ingredients_json or "[]")
        log_id = f"log_{uuid.uuid4().hex[:12]}"
        log_items: list[MealLogItem] = []

        for raw in raw_ings:
            ing = PlannedIngredient.model_validate(raw)
            scaled_grams = round(ing.grams * scale, 1)
            scaled_macros = Macros(
                kcal=round(ing.macros.kcal * scale, 1),
                protein_g=round(ing.macros.protein_g * scale, 1),
                carbs_g=round(ing.macros.carbs_g * scale, 1),
                fat_g=round(ing.macros.fat_g * scale, 1),
                fiber_g=(
                    round(ing.macros.fiber_g * scale, 1)
                    if ing.macros.fiber_g is not None
                    else None
                ),
            )
            log_items.append(
                MealLogItem(
                    item_id=f"item_{uuid.uuid4().hex[:12]}",
                    log_id=log_id,
                    food_id=ing.food_id,
                    display_name=ing.name,
                    brand=ing.brand,
                    input_quantity=scaled_grams,
                    input_unit="g",
                    input_state=ing.preparation_state,
                    resolved_grams_ref=scaled_grams,
                    macros=scaled_macros,
                )
            )

        total_macros = sum_macros([i.macros for i in log_items])
        meal_slot = resolve_meal_slot(
            plan_item.meal_slot,
            profile.meal_slots,
            language=profile.language,
        )
        meal_log = MealLog(
            log_id=log_id,
            person_id=profile.person_id,
            log_date=plan_day.plan_date,
            meal_slot=meal_slot,
            logged_at=datetime.now(UTC),
            status=MealStatus.CONFIRMED,
            source_channel="plan_promote",
            raw_input_text=plan_item.title,
            total_macros=total_macros,
            plan_item_id=plan_item.plan_item_id,
            items=log_items,
        )
        await self._meal_repo.create_meal_log(meal_log)
        await self._plan_repo.update_plan_item_status(
            profile.person_id, plan_item.plan_item_id, PlanItemStatus.CONSUMED
        )
        log_event(
            _LOGGER,
            logging.INFO,
            "plan_meal_promoted",
            person_id=profile.person_id,
            scale=scale,
        )
        return meal_log

    async def _generate_safe_day(
        self,
        *,
        llm_client: LLMClient,
        profile: PersonProfile,
        ctx: PreferenceContext,
        slots: list[MealSlotConfig],
        system_prompt: str,
        user_prompt: str,
    ) -> list[tuple[PlannedMealSkeleton, list[ScalableIngredientInput]]]:
        prompt = user_prompt
        last_violations: list[str] = []
        for _attempt in range(MAX_SAFETY_RETRIES + 1):
            resp = await llm_client.generate_structured(
                system_prompt=system_prompt,
                user_prompt=prompt,
                response_model=DayPlanProposalSchema,
            )
            # Align each generated meal slot name cleanly
            for idx, m in enumerate(resp.meals):
                fallback = (
                    getattr(slots[idx], "name", None)
                    if idx < len(slots)
                    else None
                )
                m.meal_slot = resolve_meal_slot(
                    m.meal_slot or fallback,
                    profile.meal_slots,
                    language=profile.language,
                )
            passed, violations = await self._validate_and_resolve_skeletons(
                profile=profile,
                skeletons=resp.meals,
                ctx=ctx,
            )
            if not violations and len(passed) == len(resp.meals):
                return passed
            last_violations = violations
            prompt = (
                f"{user_prompt}\n\n"
                "CRITICAL SAFETY REJECTION — Forbidden allergen/blacklist/regime detected: "
                f"{', '.join(violations)}. Regenerate the full day strictly without these items."
            )
        raise HardConstraintViolationError(last_violations or ["day_safety_failed"])

    async def _generate_safe_single_swap(
        self,
        *,
        llm_client: LLMClient,
        profile: PersonProfile,
        ctx: PreferenceContext,
        slot_name: str,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[PlannedMealSkeleton, list[ScalableIngredientInput]]:
        prompt = user_prompt
        last_violations: list[str] = []
        for _attempt in range(MAX_SAFETY_RETRIES + 1):
            resp = await llm_client.generate_structured(
                system_prompt=system_prompt,
                user_prompt=prompt,
                response_model=SingleMealSwapSchema,
            )
            resp.meal.meal_slot = slot_name
            passed, violations = await self._validate_and_resolve_skeletons(
                profile=profile,
                skeletons=[resp.meal],
                ctx=ctx,
            )
            if not violations and passed:
                return passed[0]
            last_violations = violations
            prompt = (
                f"{user_prompt}\n\n"
                "SAFETY REJECTION — Forbidden item detected: "
                f"{', '.join(violations)}. Replace cleanly without forbidden ingredients."
            )
        raise HardConstraintViolationError(last_violations or ["swap_safety_failed"])

    async def _validate_and_resolve_skeletons(
        self,
        *,
        profile: PersonProfile,
        skeletons: list[PlannedMealSkeleton],
        ctx: PreferenceContext,
    ) -> tuple[
        list[tuple[PlannedMealSkeleton, list[ScalableIngredientInput]]],
        list[str],
    ]:
        all_violations: list[str] = []
        accepted: list[tuple[PlannedMealSkeleton, list[ScalableIngredientInput]]] = []

        for skel in skeletons:
            raw_names = [i.name for i in skel.ingredients]
            raw_violations = self._pref_engine.check_hard_constraints(
                title=skel.title,
                ingredient_names=raw_names,
                resolved_foods=None,
                context=ctx,
            )
            if raw_violations:
                all_violations.extend(raw_violations)
                continue

            resolved_foods: list[EffectiveFood | None] = []
            scalable: list[ScalableIngredientInput] = []
            for spec in skel.ingredients:
                food = await self._resolver.resolve_food_candidate(
                    person_id=profile.person_id,
                    name=spec.name,
                    brand=spec.brand,
                    state=spec.preparation_state,
                    language=profile.language,
                )
                resolved_foods.append(food)
                role = classify_ingredient_role(food, spec.role)
                eff_state = spec.preparation_state
                if eff_state == PreparationState.UNSPECIFIED and food is not None:
                    eff_state = food.preparation_state
                disp_name = (
                    food.localized_name(profile.language)
                    if food is not None
                    else spec.name
                )
                scalable.append(
                    ScalableIngredientInput(
                        name=disp_name,
                        food=food,
                        proposed_grams=spec.grams,
                        preparation_state=eff_state,
                        role=role,
                        brand=spec.brand,
                    )
                )

            post_violations = self._pref_engine.check_hard_constraints(
                title=skel.title,
                ingredient_names=raw_names,
                resolved_foods=resolved_foods,
                context=ctx,
            )
            if post_violations:
                all_violations.extend(post_violations)
                continue

            accepted.append((skel, scalable))

        return accepted, list(dict.fromkeys(all_violations))

    @staticmethod
    def _build_plan_item(
        *,
        plan_day_id: str,
        meal_slot: str,
        title: str,
        prep_minutes: int,
        steps: list[str],
        tips: str,
        ingredients: list[PlannedIngredient],
        macros: Macros,
        macro_fit_warning: bool,
        plan_item_id: str | None = None,
        status: PlanItemStatus = PlanItemStatus.PLANNED,
    ) -> MealPlanItem:
        recipe = RecipeDetail(
            prep_minutes=prep_minutes,
            steps=steps,
            tips=tips,
            macro_fit_warning=macro_fit_warning,
        )
        return MealPlanItem(
            plan_item_id=plan_item_id or f"pitem_{uuid.uuid4().hex[:12]}",
            plan_day_id=plan_day_id,
            meal_slot=meal_slot,
            title=title,
            recipe_json=recipe.model_dump_json(),
            ingredients_json=json.dumps(
                [ing.model_dump(mode="json") for ing in ingredients]
            ),
            macros=macros,
            prep_minutes=prep_minutes,
            macro_fit_warning=macro_fit_warning,
            status=status,
        )

    def _require_llm(self, profile: PersonProfile) -> LLMClient:
        client = self._get_llm(profile)
        if client is None:
            raise RuntimeError(
                "No active LLM provider configured for meal planning or recipe proposals"
            )
        return client
