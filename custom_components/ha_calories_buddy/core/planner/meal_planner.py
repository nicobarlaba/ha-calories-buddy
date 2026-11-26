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
    extract_hint_tokens,
    is_savory_hint,
)
from custom_components.ha_calories_buddy.core.logging import get_logger, log_event
from custom_components.ha_calories_buddy.core.models import (
    EffectiveFood,
    FoodSource,
    IngredientRole,
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
from custom_components.ha_calories_buddy.core.nutrition.yield_factors import (
    convert_to_reference_grams,
)
from custom_components.ha_calories_buddy.core.physiology.targets import TargetResult
from custom_components.ha_calories_buddy.core.planner.macro_scaler import (
    ScalableIngredientInput,
    allocate_slot_targets,
    classify_ingredient_role,
    scale_day_meals,
    scale_meal_ingredients,
)
from custom_components.ha_calories_buddy.core.planner.meal_parser import (
    LLMFoodEstimatorAdapter,
)
from custom_components.ha_calories_buddy.core.planner.preferences import (
    PreferenceContext,
    PreferenceEngine,
)
from custom_components.ha_calories_buddy.core.planner.slot_resolver import (
    canonical_slot_group,
    default_slots_for_language,
    effective_slot_group,
    extract_logged_slot_keys,
    is_sweet_default_slot_group,
    resolve_meal_slot,
)
from custom_components.ha_calories_buddy.core.storage.meal_repo import MealRepository
from custom_components.ha_calories_buddy.core.storage.plan_repo import (
    MealPlanRepository,
)
from custom_components.ha_calories_buddy.core.storage.recipe_repo import (
    RecipeRepository,
)
from custom_components.ha_calories_buddy.core.storage.seeder import normalize_text

_LOGGER = get_logger(__name__)
MAX_SAFETY_RETRIES = 2

_SAVORY_ENTREE_CATS = frozenset(
    {"poultry", "red_meat", "fish", "pasta", "rice", "legumes", "vegetables"}
)
_SAVORY_RAW_KEYWORDS = (
    "bresaola",
    "prosciutto",
    "salame",
    "tacchino",
    "pollo",
    "manzo",
    "tonno",
    "salmone",
    "merluzzo",
    "ceci",
    "lenticchie",
    "cetriol",
    "finocch",
    "carot",
    "rucola",
    "broccol",
    "peperon",
    "funghi",
    "chicken",
    "turkey",
    "beef",
    "tuna",
    "salmon",
    "cod",
    "lentil",
    "chickpea",
    "cucumber",
    "fennel",
    "carrot",
    "broccoli",
    "pepper",
    "mushroom",
)


def _find_sweet_slot_savory_item(ings: list[ScalableIngredientInput]) -> str | None:
    """Return the first savory entree/deli/vegetable ingredient name, or None."""
    for ing in ings:
        if ing.food is not None and ing.food.category.lower() in _SAVORY_ENTREE_CATS:
            return ing.name
        norm_name = normalize_text(ing.name)
        if any(kw in norm_name for kw in _SAVORY_RAW_KEYWORDS):
            return ing.name
    return None


def _primary_anchor_pair(ings: list[ScalableIngredientInput]) -> tuple[str, str]:
    """Extract (protein_anchor_token, carb_anchor_token) for diversity comparison."""
    prot = next(
        (
            normalize_text(i.name).split()[0]
            for i in ings
            if i.role == IngredientRole.PROTEIN_ANCHOR and normalize_text(i.name)
        ),
        "",
    )
    carb_ing = next((i for i in ings if i.role == IngredientRole.CARB_ANCHOR), None)
    carb = (
        normalize_text(carb_ing.name).split()[0]
        if carb_ing and normalize_text(carb_ing.name)
        else ""
    )
    return prot, carb


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
        recipe_repo: RecipeRepository | None = None,
    ) -> None:
        self._plan_repo = plan_repo
        self._meal_repo = meal_repo
        self._pref_engine = preference_engine
        self._resolver = resolver
        self._get_llm = llm_client_getter
        self._recipe_repo = recipe_repo

    async def suggest_meal(
        self,
        profile: PersonProfile,
        *,
        meal_slot: str | None = None,
        target_macros: Macros,
        user_hint: str = "",
        max_cook_minutes: int | None = None,
        count: int = 3,
        reference_date: date | None = None,
        budget_mode: str = "auto",
        workout_context: str = "",
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

        ctx = await self._pref_engine.build_context(profile, reference_date=reference_date)
        vault_hint = await self._build_vault_catalog_hint(
            profile, meal_slot=chosen_slot, user_hint=user_hint
        )
        requested_count = max(1, min(3, count))
        sys_prompt, user_prompt = build_suggest_meal_prompts(
            profile=profile,
            ctx=ctx,
            meal_slot=chosen_slot,
            target_macros=target_macros,
            max_cook_minutes=cook_limit,
            user_hint=user_hint,
            count=requested_count,
            vault_catalog_hint=vault_hint,
            workout_context=workout_context,
        )

        resolve_cache: dict[tuple[str, str, PreparationState], EffectiveFood | None] = {}
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
                llm_client=llm_client,
                resolve_cache=resolve_cache,
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

        # Non-blocking single retry for culinary diversity, slot coherence, anti-repetition, or missing nutrition
        quality_issues = self._detect_suggestion_quality_issues(
            safe_skeletons[:requested_count],
            ctx=ctx,
            requested_count=requested_count,
            user_hint=user_hint,
            meal_slot=chosen_slot,
        )
        if quality_issues:
            try:
                retry_prompt = (
                    f"{user_prompt}\n\n"
                    "QUALITY IMPROVEMENT NEEDED — Please regenerate distinct balanced options fixing:\n"
                    + "\n".join(f"- {issue}" for issue in quality_issues)
                )
                retry_resp = await llm_client.generate_structured(
                    system_prompt=sys_prompt,
                    user_prompt=retry_prompt,
                    response_model=MealSuggestionListSchema,
                )
                retry_passed, retry_violations = await self._validate_and_resolve_skeletons(
                    profile=profile,
                    skeletons=retry_resp.suggestions,
                    ctx=ctx,
                    llm_client=llm_client,
                    resolve_cache=resolve_cache,
                )
                if retry_passed and not retry_violations:
                    retry_issues = self._detect_suggestion_quality_issues(
                        retry_passed[:requested_count],
                        ctx=ctx,
                        requested_count=requested_count,
                        user_hint=user_hint,
                        meal_slot=chosen_slot,
                    )
                    if len(retry_issues) <= len(quality_issues):
                        safe_skeletons = retry_passed
            except Exception as exc:  # noqa: BLE001
                log_event(
                    _LOGGER,
                    logging.INFO,
                    "suggest_meal_quality_retry_skipped",
                    error_type=type(exc).__name__,
                )

        suggestions: list[MealSuggestion] = []
        for skel, resolved_ings in safe_skeletons[:requested_count]:
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
                    target_macros=target_macros,
                    budget_mode=budget_mode,
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
        reference_today: date | None = None,
        workout_context: str = "",
    ) -> MealPlan:
        """Generate a 1-7 day meal plan day-by-day with safety & <=5% macro scaling."""
        llm_client = self._require_llm(profile)
        slots = profile.meal_slots or default_slots_for_language(profile.language)
        slot_targets = allocate_slot_targets(daily_targets, slots)
        ctx = await self._pref_engine.build_context(profile, reference_date=start_date)
        vault_hint = await self._build_vault_catalog_hint(profile)
        resolve_cache: dict[tuple[str, str, PreparationState], EffectiveFood | None] = {}
        today_date = reference_today or date.today()

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

            day_slots = slots
            day_slot_targets = slot_targets
            day_scale_target = day_target_macros
            is_partial_today = False

            if cur_date == today_date:
                today_meals = await self._meal_repo.list_meals_for_date(
                    profile.person_id, cur_date
                )
                logged_slots = extract_logged_slot_keys(today_meals)
                if logged_slots:
                    uncovered_slots = [
                        s for s in slots if normalize_text(s.name) not in logged_slots
                    ]
                    if not uncovered_slots:
                        days.append(
                            MealPlanDay(
                                plan_day_id=plan_day_id,
                                plan_id=plan_id,
                                plan_date=cur_date,
                                target_macros=day_target_macros,
                                items=[],
                            )
                        )
                        prev_day_skeletons = None
                        continue
                    if len(uncovered_slots) < len(slots):
                        is_partial_today = True
                        totals = await self._meal_repo.get_daily_totals(
                            profile.person_id, cur_date
                        )
                        rem_targets = daily_targets.model_copy(
                            update={
                                "target_kcal": round(
                                    max(150.0, daily_targets.target_kcal - totals.consumed.kcal),
                                    1,
                                ),
                                "protein_g": round(
                                    max(10.0, daily_targets.protein_g - totals.consumed.protein_g),
                                    1,
                                ),
                                "carbs_g": round(
                                    max(0.0, daily_targets.carbs_g - totals.consumed.carbs_g),
                                    1,
                                ),
                                "fat_g": round(
                                    max(5.0, daily_targets.fat_g - totals.consumed.fat_g),
                                    1,
                                ),
                                "fiber_g": round(
                                    max(0.0, daily_targets.fiber_g - (totals.consumed.fiber_g or 0.0)),
                                    1,
                                ),
                            }
                        )
                        day_slots = uncovered_slots
                        day_slot_targets = allocate_slot_targets(
                            rem_targets, uncovered_slots
                        )
                        day_scale_target = Macros(
                            kcal=rem_targets.target_kcal,
                            protein_g=rem_targets.protein_g,
                            carbs_g=rem_targets.carbs_g,
                            fat_g=rem_targets.fat_g,
                            fiber_g=rem_targets.fiber_g,
                        )

            # Meal-prep reuse on alternate days when requested (only from complete full days)
            if (
                options.meal_prep_mode
                and prev_day_skeletons is not None
                and offset % 2 == 1
                and not is_partial_today
            ):
                slot_inputs = [
                    (slot_name, ings, day_slot_targets.get(slot_name, day_scale_target))
                    for slot_name, _, _, _, _, ings in prev_day_skeletons
                ]
                scaled_meals, _, _ = scale_day_meals(slot_inputs, day_scale_target)
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
                slots=day_slots,
                slot_targets=day_slot_targets,
                options=options,
                previous_titles=generated_titles,
                vault_catalog_hint=vault_hint,
                workout_context=workout_context,
            )

            day_resolved = await self._generate_safe_day(
                llm_client=llm_client,
                profile=profile,
                ctx=ctx,
                slots=day_slots,
                system_prompt=sys_prompt,
                user_prompt=user_prompt,
                resolve_cache=resolve_cache,
                notes=options.notes,
            )

            slot_inputs = [
                (
                    skel.meal_slot,
                    ings,
                    day_slot_targets.get(skel.meal_slot, day_scale_target),
                )
                for skel, ings in day_resolved
            ]
            scaled_meals, _, _ = scale_day_meals(slot_inputs, day_scale_target)

            day_items = []
            prev_day_skeletons = [] if not is_partial_today else None
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
                if prev_day_skeletons is not None:
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

        ctx = await self._pref_engine.build_context(
            profile, reference_date=plan_day.plan_date
        )
        vault_hint = await self._build_vault_catalog_hint(
            profile,
            meal_slot=current_item.meal_slot,
            user_hint=user_instruction,
        )
        resolve_cache: dict[tuple[str, str, PreparationState], EffectiveFood | None] = {}
        sys_prompt, user_prompt = build_swap_meal_prompts(
            profile=profile,
            ctx=ctx,
            meal_slot=current_item.meal_slot,
            target_macros=slot_budget,
            max_cook_minutes=slot_cfg.max_cook_minutes,
            current_title=current_item.title,
            user_instruction=user_instruction,
            vault_catalog_hint=vault_hint,
        )

        new_skel, resolved_ings = await self._generate_safe_single_swap(
            llm_client=llm_client,
            profile=profile,
            ctx=ctx,
            slot_name=current_item.meal_slot,
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            resolve_cache=resolve_cache,
            user_instruction=user_instruction,
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

    async def log_suggestion_as_meal(
        self,
        profile: PersonProfile,
        suggestion: MealSuggestion,
        *,
        log_date: date | None = None,
        actual_scale: float = 1.0,
    ) -> MealLog:
        """Persist an accepted MealSuggestion as one single grouped confirmed MealLog."""
        scale = max(0.1, min(5.0, float(actual_scale)))
        log_id = f"log_{uuid.uuid4().hex[:12]}"
        log_items: list[MealLogItem] = []

        for ing in suggestion.ingredients:
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
            suggestion.meal_slot,
            profile.meal_slots,
            language=profile.language,
        )
        meal_log = MealLog(
            log_id=log_id,
            person_id=profile.person_id,
            log_date=log_date or date.today(),
            meal_slot=meal_slot,
            logged_at=datetime.now(UTC),
            status=MealStatus.CONFIRMED,
            source_channel="suggestion_accept",
            raw_input_text=suggestion.title,
            total_macros=total_macros,
            plan_item_id=None,
            items=log_items,
        )
        await self._meal_repo.create_meal_log(meal_log)
        log_event(
            _LOGGER,
            logging.INFO,
            "suggestion_logged_as_meal",
            person_id=profile.person_id,
            scale=scale,
            item_count=len(log_items),
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
        resolve_cache: dict[tuple[str, str, PreparationState], EffectiveFood | None] | None = None,
        notes: str = "",
    ) -> list[tuple[PlannedMealSkeleton, list[ScalableIngredientInput]]]:
        prompt = user_prompt
        last_violations: list[str] = []
        cache = resolve_cache if resolve_cache is not None else {}
        safe_day: list[tuple[PlannedMealSkeleton, list[ScalableIngredientInput]]] | None = None

        for _attempt in range(MAX_SAFETY_RETRIES + 1):
            resp = await llm_client.generate_structured(
                system_prompt=system_prompt,
                user_prompt=prompt,
                response_model=DayPlanProposalSchema,
            )
            for idx, m in enumerate(resp.meals):
                fallback = (
                    getattr(slots[idx], "name", None)
                    if idx < len(slots)
                    else None
                )
                m.meal_slot = resolve_meal_slot(
                    m.meal_slot or fallback,
                    slots,
                    language=profile.language,
                )
            passed, violations = await self._validate_and_resolve_skeletons(
                profile=profile,
                skeletons=resp.meals,
                ctx=ctx,
                llm_client=llm_client,
                resolve_cache=cache,
            )
            if not violations and len(passed) == len(resp.meals):
                safe_day = passed
                break
            last_violations = violations
            prompt = (
                f"{user_prompt}\n\n"
                "CRITICAL SAFETY REJECTION — Forbidden allergen/blacklist/regime detected: "
                f"{', '.join(violations)}. Regenerate the full day strictly without these items."
            )

        if safe_day is None:
            raise HardConstraintViolationError(last_violations or ["day_safety_failed"])

        quality_issues = self._detect_day_plan_quality_issues(
            safe_day, slots=slots, notes=notes
        )
        if quality_issues:
            try:
                retry_prompt = (
                    f"{user_prompt}\n\n"
                    "QUALITY IMPROVEMENT NEEDED — Regenerate the day plan fixing these culinary issues:\n"
                    + "\n".join(f"- {issue}" for issue in quality_issues)
                )
                retry_resp = await llm_client.generate_structured(
                    system_prompt=system_prompt,
                    user_prompt=retry_prompt,
                    response_model=DayPlanProposalSchema,
                )
                for idx, m in enumerate(retry_resp.meals):
                    fallback = (
                        getattr(slots[idx], "name", None)
                        if idx < len(slots)
                        else None
                    )
                    m.meal_slot = resolve_meal_slot(
                        m.meal_slot or fallback,
                        slots,
                        language=profile.language,
                    )
                retry_passed, retry_violations = await self._validate_and_resolve_skeletons(
                    profile=profile,
                    skeletons=retry_resp.meals,
                    ctx=ctx,
                    llm_client=llm_client,
                    resolve_cache=cache,
                )
                if (
                    retry_passed
                    and not retry_violations
                    and len(retry_passed) == len(retry_resp.meals)
                ):
                    retry_issues = self._detect_day_plan_quality_issues(
                        retry_passed, slots=slots, notes=notes
                    )
                    if len(retry_issues) <= len(quality_issues):
                        return retry_passed
            except Exception as exc:  # noqa: BLE001
                log_event(
                    _LOGGER,
                    logging.INFO,
                    "day_plan_quality_retry_skipped",
                    error_type=type(exc).__name__,
                )

        return safe_day

    async def _generate_safe_single_swap(
        self,
        *,
        llm_client: LLMClient,
        profile: PersonProfile,
        ctx: PreferenceContext,
        slot_name: str,
        system_prompt: str,
        user_prompt: str,
        resolve_cache: dict[tuple[str, str, PreparationState], EffectiveFood | None] | None = None,
        user_instruction: str = "",
    ) -> tuple[PlannedMealSkeleton, list[ScalableIngredientInput]]:
        prompt = user_prompt
        last_violations: list[str] = []
        cache = resolve_cache if resolve_cache is not None else {}
        safe_swap: tuple[PlannedMealSkeleton, list[ScalableIngredientInput]] | None = None

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
                llm_client=llm_client,
                resolve_cache=cache,
            )
            if not violations and passed:
                safe_swap = passed[0]
                break
            last_violations = violations
            prompt = (
                f"{user_prompt}\n\n"
                "SAFETY REJECTION — Forbidden item detected: "
                f"{', '.join(violations)}. Replace cleanly without forbidden ingredients."
            )

        if safe_swap is None:
            raise HardConstraintViolationError(last_violations or ["swap_safety_failed"])

        quality_issues = self._detect_swap_quality_issues(
            safe_swap, meal_slot=slot_name, user_instruction=user_instruction
        )
        if quality_issues:
            try:
                retry_prompt = (
                    f"{user_prompt}\n\n"
                    "QUALITY IMPROVEMENT NEEDED — Fix these issues:\n"
                    + "\n".join(f"- {issue}" for issue in quality_issues)
                )
                retry_resp = await llm_client.generate_structured(
                    system_prompt=system_prompt,
                    user_prompt=retry_prompt,
                    response_model=SingleMealSwapSchema,
                )
                retry_resp.meal.meal_slot = slot_name
                retry_passed, retry_violations = await self._validate_and_resolve_skeletons(
                    profile=profile,
                    skeletons=[retry_resp.meal],
                    ctx=ctx,
                    llm_client=llm_client,
                    resolve_cache=cache,
                )
                if retry_passed and not retry_violations:
                    retry_issues = self._detect_swap_quality_issues(
                        retry_passed[0],
                        meal_slot=slot_name,
                        user_instruction=user_instruction,
                    )
                    if len(retry_issues) <= len(quality_issues):
                        return retry_passed[0]
            except Exception as exc:  # noqa: BLE001
                log_event(
                    _LOGGER,
                    logging.INFO,
                    "swap_quality_retry_skipped",
                    error_type=type(exc).__name__,
                )

        return safe_swap

    async def _validate_and_resolve_skeletons(
        self,
        *,
        profile: PersonProfile,
        skeletons: list[PlannedMealSkeleton],
        ctx: PreferenceContext,
        llm_client: LLMClient | None = None,
        resolve_cache: dict[tuple[str, str, PreparationState], EffectiveFood | None] | None = None,
    ) -> tuple[
        list[tuple[PlannedMealSkeleton, list[ScalableIngredientInput]]],
        list[str],
    ]:
        all_violations: list[str] = []
        accepted: list[tuple[PlannedMealSkeleton, list[ScalableIngredientInput]]] = []
        estimator = LLMFoodEstimatorAdapter(llm_client) if llm_client is not None else None
        cache = resolve_cache if resolve_cache is not None else {}

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
                cache_key = (
                    normalize_text(spec.name),
                    normalize_text(spec.brand or ""),
                    spec.preparation_state,
                )
                if cache_key in cache:
                    food = cache[cache_key]
                else:
                    food = await self._resolver.resolve_food_candidate(
                        person_id=profile.person_id,
                        name=spec.name,
                        brand=spec.brand,
                        state=spec.preparation_state,
                        language=profile.language,
                        llm_estimator=estimator,
                    )
                    cache[cache_key] = food

                resolved_foods.append(food)
                role = classify_ingredient_role(food, spec.role)
                eff_state = spec.preparation_state
                proposed_grams = spec.grams
                if eff_state == PreparationState.UNSPECIFIED and food is not None:
                    eff_state = food.preparation_state
                elif (
                    food is not None
                    and food.preparation_state in (PreparationState.RAW, PreparationState.COOKED)
                    and eff_state in (PreparationState.RAW, PreparationState.COOKED)
                    and eff_state != food.preparation_state
                ):
                    proposed_grams = round(
                        max(
                            5.0,
                            convert_to_reference_grams(
                                input_grams=spec.grams,
                                effective_state=eff_state,
                                ref_state=food.preparation_state,
                                yield_factor=food.yield_factor,
                            ),
                        ),
                        1,
                    )
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
                        proposed_grams=proposed_grams,
                        preparation_state=eff_state,
                        role=role,
                        brand=spec.brand,
                        course=spec.course,
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

    async def _build_vault_catalog_hint(
        self,
        profile: PersonProfile,
        *,
        meal_slot: str | None = None,
        user_hint: str = "",
    ) -> str:
        """Build a compact categorized list of verified Food Vault names for the Chef."""
        foods = await self._resolver.list_vault_foods(
            person_id=profile.person_id, limit=120
        )
        if not foods:
            return ""
        slot_group = canonical_slot_group(meal_slot or "")
        sweet_snack_focus = (
            is_sweet_default_slot_group(slot_group)
            and not is_savory_hint(user_hint)
        )
        if sweet_snack_focus:
            buckets: dict[str, list[str]] = {
                "Dairy": [],
                "Carbs": [],
                "Fruit": [],
                "Fats": [],
                "Snack": [],
            }
            cat_to_bucket = {
                "dairy": "Dairy",
                "oats": "Carbs",
                "bread": "Carbs",
                "fruit": "Fruit",
                "nuts": "Fats",
                "snack": "Snack",
            }
        else:
            buckets = {
                "Protein": [],
                "Carbs": [],
                "Legumes": [],
                "Dairy": [],
                "Vegetables": [],
                "Fruit": [],
                "Fats": [],
            }
            cat_to_bucket = {
                "poultry": "Protein",
                "red_meat": "Protein",
                "fish": "Protein",
                "egg": "Protein",
                "rice": "Carbs",
                "pasta": "Carbs",
                "grains": "Carbs",
                "oats": "Carbs",
                "bread": "Carbs",
                "potatoes": "Carbs",
                "legumes": "Legumes",
                "dairy": "Dairy",
                "vegetables": "Vegetables",
                "fruit": "Fruit",
                "oil": "Fats",
                "nuts": "Fats",
            }
        allowed_recipe_fids = (
            await self._recipe_repo.list_planner_allowed_food_ids()
            if self._recipe_repo is not None
            else set()
        )
        homemade_names: list[str] = []
        for food in foods:
            if food.source == FoodSource.RECIPE:
                if food.food_id not in allowed_recipe_fids:
                    continue
                label = food.localized_name(profile.language)
                if label not in homemade_names and len(homemade_names) < 4:
                    homemade_names.append(label)
                continue
            bucket = cat_to_bucket.get(food.category.lower())
            if bucket is None or len(buckets[bucket]) >= 8:
                continue
            label = food.localized_name(profile.language)
            if label not in buckets[bucket]:
                buckets[bucket].append(label)
        lines = [
            f"- {bucket}: {', '.join(names)}"
            for bucket, names in buckets.items()
            if names
        ]
        if homemade_names:
            lines.insert(0, f"- Homemade: {', '.join(homemade_names)}")
        return "\n".join(lines)[:1200]

    @staticmethod
    def _detect_suggestion_quality_issues(
        candidates: list[tuple[PlannedMealSkeleton, list[ScalableIngredientInput]]],
        *,
        ctx: PreferenceContext,
        requested_count: int,
        user_hint: str = "",
        meal_slot: str = "",
    ) -> list[str]:
        """Flag unresolved ingredients, slot mismatch, same-day repetition, or duplicate bases."""
        issues: list[str] = []
        hint_norm = normalize_text(user_hint)
        hint_tokens = extract_hint_tokens(user_hint)
        slot_group = canonical_slot_group(meal_slot)
        is_sweet_slot = (
            is_sweet_default_slot_group(slot_group)
            and not is_savory_hint(user_hint)
        )

        # 1. Check excessive unresolved ingredients (> 25% missing nutrition)
        for skel, ings in candidates:
            if not ings:
                continue
            missing = sum(1 for i in ings if i.food is None)
            if missing / len(ings) > 0.25:
                issues.append(
                    f"Option '{skel.title}' has unresolved ingredients; use clear single whole-food names."
                )
                break

        # 2. Check snack/breakfast culinary coherence (no deli meats, chicken/fish/pasta, or savory veggies unless requested)
        if is_sweet_slot:
            for skel, ings in candidates:
                savory_bad = _find_sweet_slot_savory_item(ings)
                if savory_bad is not None:
                    issues.append(
                        f"Option '{skel.title}' includes a savory lunch/dinner ingredient "
                        f"('{savory_bad}'); propose light sweet/neutral snack ideas suitable for {meal_slot}."
                    )
                    break

        # 3. Check same-day repetition of primary savory/protein anchors unless requested in user_hint
        eaten_norms = [
            (raw, normalize_text(raw))
            for raw in ctx.eaten_today_foods
            if normalize_text(raw)
        ]
        savory_cats = {"pasta", "rice", "legumes", "poultry", "fish", "red_meat"}
        for _skel, ings in candidates:
            for ing in ings:
                ing_cat = ing.food.category.lower() if ing.food is not None else ""
                is_primary = ing.role == IngredientRole.PROTEIN_ANCHOR or ing_cat in savory_cats
                if not is_primary:
                    continue
                ing_norm = normalize_text(ing.name)
                if not ing_norm:
                    continue
                for raw_eaten, eaten_norm in eaten_norms:
                    eaten_tokens = [t for t in eaten_norm.split() if len(t) >= 4]
                    if eaten_norm in hint_norm or (
                        hint_tokens and any(tok in hint_tokens for tok in eaten_tokens)
                    ):
                        continue
                    ing_tokens = {t for t in ing_norm.split() if len(t) >= 4}
                    if (
                        eaten_norm == ing_norm
                        or (eaten_tokens and any(tok in ing_tokens for tok in eaten_tokens))
                    ):
                        issues.append(
                            f"Avoid repeating '{raw_eaten}' already eaten earlier today."
                        )
                        break

        # 4. Check cross-option anchor diversity when >= 2 options exist
        if requested_count >= 2 and len(candidates) >= 2:
            if hint_tokens:
                # When user gave a specific craving (e.g. yogurt & peanut butter), ensure distinct variants
                norm_titles = [normalize_text(skel.title) for skel, _ in candidates]
                if len(set(norm_titles)) < len(norm_titles):
                    issues.append(
                        "Multiple options have identical titles; create distinct variations around the user's craving."
                    )
            else:
                signatures: list[tuple[str, str]] = []
                heavy_carb_cats: list[str] = []
                for _skel, ings in candidates:
                    prot = next(
                        (
                            normalize_text(i.name).split()[0]
                            for i in ings
                            if i.role == IngredientRole.PROTEIN_ANCHOR and normalize_text(i.name)
                        ),
                        "",
                    )
                    carb_ing = next(
                        (i for i in ings if i.role == IngredientRole.CARB_ANCHOR),
                        None,
                    )
                    carb = (
                        normalize_text(carb_ing.name).split()[0]
                        if carb_ing and normalize_text(carb_ing.name)
                        else ""
                    )
                    signatures.append((prot, carb))
                    if carb_ing and carb_ing.food is not None:
                        c_cat = carb_ing.food.category.lower()
                        if c_cat in {"pasta", "rice", "legumes"}:
                            heavy_carb_cats.append(c_cat)

                non_empty = [sig for sig in signatures if sig != ("", "")]
                if len(non_empty) >= 2 and len(set(non_empty)) < len(non_empty):
                    issues.append(
                        "Multiple options share the same protein/carb base; vary protein AND carb across options."
                    )
                for repeated_cat in ("pasta", "legumes"):
                    if heavy_carb_cats.count(repeated_cat) >= 2:
                        issues.append(
                            f"Do not propose multiple {repeated_cat} options; diversify bases cleanly."
                        )

        return list(dict.fromkeys(issues))

    @staticmethod
    def _detect_day_plan_quality_issues(
        day_meals: list[tuple[PlannedMealSkeleton, list[ScalableIngredientInput]]],
        *,
        slots: list[MealSlotConfig],
        notes: str = "",
    ) -> list[str]:
        """Flag sweet-snack savory intrusions, unresolved foods, or same-day main-meal duplicates."""
        issues: list[str] = []
        wants_savory = is_savory_hint(notes)
        slot_by_name = {s.name: s for s in slots}
        lunch_sig: tuple[str, str] | None = None
        dinner_sig: tuple[str, str] | None = None

        for skel, ings in day_meals:
            if ings:
                missing = sum(1 for i in ings if i.food is None)
                if missing / len(ings) > 0.25:
                    issues.append(
                        f"Meal '{skel.title}' ({skel.meal_slot}) has unresolved ingredients."
                    )
            slot_cfg = slot_by_name.get(skel.meal_slot)
            grp = (
                effective_slot_group(slot_cfg, slots)
                if slot_cfg is not None
                else canonical_slot_group(skel.meal_slot)
            )
            if is_sweet_default_slot_group(grp) and not wants_savory:
                savory_bad = _find_sweet_slot_savory_item(ings)
                if savory_bad is not None:
                    issues.append(
                        f"Slot '{skel.meal_slot}' ('{skel.title}') contains '{savory_bad}': "
                        "snacks and breakfasts must be sweet/light (yogurt, fruit, oats, rusks, ricotta, nut butter), "
                        "NEVER bresaola, meat, fish, legumes, or savory vegetables."
                    )
            elif grp == "lunch":
                lunch_sig = _primary_anchor_pair(ings)
            elif grp == "dinner":
                dinner_sig = _primary_anchor_pair(ings)

        if (
            lunch_sig is not None
            and dinner_sig is not None
            and lunch_sig[0]
            and lunch_sig[0] == dinner_sig[0]
        ):
            issues.append(
                f"Lunch and Dinner share the same protein ('{lunch_sig[0]}'); use distinct proteins across Lunch and Dinner."
            )
        if (
            lunch_sig is not None
            and dinner_sig is not None
            and lunch_sig[1]
            and lunch_sig[1] == dinner_sig[1]
        ):
            issues.append(
                f"Lunch and Dinner share the same carb base ('{lunch_sig[1]}'); vary carb bases across Lunch and Dinner."
            )
        return list(dict.fromkeys(issues))

    @staticmethod
    def _detect_swap_quality_issues(
        candidate: tuple[PlannedMealSkeleton, list[ScalableIngredientInput]],
        *,
        meal_slot: str,
        user_instruction: str = "",
    ) -> list[str]:
        """Flag sweet-snack savory intrusions or unresolved ingredients on single-slot swap."""
        skel, ings = candidate
        issues: list[str] = []
        if ings:
            missing = sum(1 for i in ings if i.food is None)
            if missing / len(ings) > 0.25:
                issues.append(f"Replacement '{skel.title}' has unresolved ingredients.")
        grp = canonical_slot_group(meal_slot)
        if is_sweet_default_slot_group(grp) and not is_savory_hint(user_instruction):
            savory_bad = _find_sweet_slot_savory_item(ings)
            if savory_bad is not None:
                issues.append(
                    f"Slot '{meal_slot}' replacement '{skel.title}' includes savory '{savory_bad}'; "
                    "keep snacks sweet/light unless savory was explicitly requested."
                )
        return issues

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
