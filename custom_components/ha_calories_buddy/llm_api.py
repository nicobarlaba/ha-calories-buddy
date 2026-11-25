"""Home Assistant Assist llm.API tool registration (7 nutrition & planning tools)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date

import probatio as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from homeassistant.util.json import JsonValueType

from custom_components.ha_calories_buddy.const import DOMAIN
from custom_components.ha_calories_buddy.core.logging import get_logger, log_event
from custom_components.ha_calories_buddy.core.models import (
    Macros,
    PlanGenerationOptions,
    PlanItemStatus,
    PreparationState,
)
from custom_components.ha_calories_buddy.core.planner.macro_scaler import (
    allocate_slot_targets,
)
from custom_components.ha_calories_buddy.core.planner.slot_resolver import (
    default_slots_for_language,
    resolve_meal_slot,
)
from custom_components.ha_calories_buddy.identity import (
    PersonAmbiguousError,
    PersonNotFoundError,
    resolve_person,
)
from custom_components.ha_calories_buddy.runtime import DomainRuntime

_LOGGER = get_logger(__name__)


def _user_id_from_llm_context(llm_context: llm.LLMContext) -> str | None:
    if llm_context.context is None:
        return None
    return llm_context.context.user_id


class LogMealTool(llm.Tool):
    """Assist tool to log meals from natural speech or text."""

    name = "log_meal"
    title = "Log meal calories and macros"
    description = (
        "Log a meal consumed by the user from a natural language description. "
        "Preserve all brand names, quantities, units, and raw/cooked wording verbatim in `text`, "
        "and ALWAYS pass `meal_slot` (e.g. Colazione/Breakfast, Pranzo/Lunch, Spuntino/Snack, Cena/Dinner) "
        "when the user mentions which meal it was."
    )
    integration = DOMAIN
    parameters = vol.Schema(
        {
            vol.Required("text"): str,
            vol.Optional("meal_slot"): str,
            vol.Optional("person"): str,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        """Execute meal logging for the resolved person."""
        runtime: DomainRuntime = hass.data[DOMAIN]
        args = tool_input.tool_args
        try:
            profile = resolve_person(
                hass,
                explicit_person=args.get("person"),
                context_user_id=_user_id_from_llm_context(llm_context),
            )
            result = await runtime.logging_service.log_from_text(
                profile,
                str(args["text"]),
                meal_slot=args.get("meal_slot"),
                source_channel="voice",
            )
        except (PersonNotFoundError, PersonAmbiguousError) as exc:
            return llm.ToolResult(data={"error": str(exc)}, error=True)
        except Exception as exc:  # noqa: BLE001
            log_event(
                _LOGGER,
                logging.WARNING,
                "assist_log_meal_failed",
                error_type=type(exc).__name__,
            )
            return llm.ToolResult(data={"error": str(exc)}, error=True)

        person_rt = runtime.persons.get(profile.person_id)
        if person_rt is not None:
            await person_rt.coordinator.async_request_refresh()

        warnings_json: list[JsonValueType] = list(result.warnings)
        return llm.ToolResult(
            data={
                "person": profile.display_name,
                "meal_slot": result.meal_log.meal_slot,
                "status": result.meal_log.status.value,
                "needs_confirmation": result.needs_confirmation,
                "summary": result.summary_message,
                "warnings": warnings_json,
                "kcal": result.meal_log.total_macros.kcal,
                "protein_g": result.meal_log.total_macros.protein_g,
                "carbs_g": result.meal_log.total_macros.carbs_g,
                "fat_g": result.meal_log.total_macros.fat_g,
                "fiber_g": round(result.meal_log.total_macros.fiber_g or 0.0, 1),
            }
        )


class CorrectLastMealTool(llm.Tool):
    """Assist tool to adjust quantity/unit/state or meal_slot of a meal logged today."""

    name = "correct_last_meal"
    title = "Correct logged meal item or slot"
    description = (
        "Adjust the quantity, unit, raw/cooked state of a food item logged today, "
        "and/or change the meal_slot (e.g. move to Lunch/Pranzo)."
    )
    integration = DOMAIN
    parameters = vol.Schema(
        {
            vol.Optional("item_name"): str,
            vol.Optional("new_quantity"): vol.Coerce(float),
            vol.Optional("new_unit"): str,
            vol.Optional("new_state"): str,
            vol.Optional("new_meal_slot"): str,
            vol.Optional("person"): str,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        """Update item measurement and/or meal_slot on today's latest matching meal."""
        runtime: DomainRuntime = hass.data[DOMAIN]
        args = tool_input.tool_args
        try:
            profile = resolve_person(
                hass,
                explicit_person=args.get("person"),
                context_user_id=_user_id_from_llm_context(llm_context),
            )
        except (PersonNotFoundError, PersonAmbiguousError) as exc:
            return llm.ToolResult(data={"error": str(exc)}, error=True)

        today = dt_util.now().date()
        meals = await runtime.meal_repo.list_meals_for_date(profile.person_id, today)
        if not meals:
            return llm.ToolResult(data={"error": "No meals logged today"}, error=True)

        needle = str(args.get("item_name") or "").strip().lower()
        target_log_id: str | None = None
        target_item_id: str | None = None

        if needle:
            for meal in reversed(meals):
                for item in reversed(meal.items):
                    if needle in item.display_name.lower():
                        target_log_id = meal.log_id
                        target_item_id = item.item_id
                        break
                if target_item_id is not None:
                    break
            if target_log_id is None:
                return llm.ToolResult(
                    data={"error": f"No item matching '{args['item_name']}' logged today"},
                    error=True,
                )
        else:
            target_log_id = meals[-1].log_id

        updated = None
        if target_item_id is not None and args.get("new_quantity") is not None:
            state = (
                PreparationState(args["new_state"])
                if args.get("new_state") in ("raw", "cooked", "ready_to_eat")
                else None
            )
            updated = await runtime.logging_service.update_item_measurement(
                profile,
                target_log_id,
                target_item_id,
                quantity=float(args["new_quantity"]),
                unit=args.get("new_unit"),
                state=state,
            )

        if args.get("new_meal_slot"):
            updated = await runtime.logging_service.change_meal_slot(
                profile,
                target_log_id,
                str(args["new_meal_slot"]),
            )

        if updated is None:
            return llm.ToolResult(
                data={"error": "Specify new_quantity or new_meal_slot to update"},
                error=True,
            )

        person_rt = runtime.persons.get(profile.person_id)
        if person_rt is not None:
            await person_rt.coordinator.async_request_refresh()

        return llm.ToolResult(
            data={
                "person": profile.display_name,
                "meal_slot": updated.meal_slot,
                "new_meal_kcal": updated.total_macros.kcal,
                "protein_g": updated.total_macros.protein_g,
                "carbs_g": updated.total_macros.carbs_g,
                "fat_g": updated.total_macros.fat_g,
                "fiber_g": round(updated.total_macros.fiber_g or 0.0, 1),
            }
        )


class GetTodayNutritionSummaryTool(llm.Tool):
    """Assist tool to query today's calories, macros, fiber, remaining budget, and plan."""

    name = "get_today_nutrition_summary"
    title = "Get today's nutrition summary"
    description = (
        "Check consumed and remaining calories, protein, carbs, fat, fiber, TDEE, "
        "pending meal confirmations, and any remaining planned meals for today."
    )
    integration = DOMAIN
    parameters = vol.Schema({vol.Optional("person"): str})

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        """Return today's computed dashboard summary for the resolved person."""
        runtime: DomainRuntime = hass.data[DOMAIN]
        args = tool_input.tool_args
        try:
            profile = resolve_person(
                hass,
                explicit_person=args.get("person"),
                context_user_id=_user_id_from_llm_context(llm_context),
            )
        except (PersonNotFoundError, PersonAmbiguousError) as exc:
            return llm.ToolResult(data={"error": str(exc)}, error=True)

        person_rt = runtime.persons.get(profile.person_id)
        if person_rt is None or person_rt.coordinator.data is None:
            return llm.ToolResult(
                data={"error": "Person dashboard state not ready"}, error=True
            )

        data = person_rt.coordinator.data
        fiber_consumed = round(data.totals.consumed.fiber_g or 0.0, 1)
        planned_list: list[JsonValueType] = [
            {
                "meal_slot": p.meal_slot,
                "title": p.title,
                "kcal": round(p.macros.kcal, 1),
                "prep_minutes": p.prep_minutes,
            }
            for p in data.planned_today
            if p.status == PlanItemStatus.PLANNED
        ]
        return llm.ToolResult(
            data={
                "person": profile.display_name,
                "date": data.today.isoformat(),
                "consumed_kcal": round(data.totals.consumed.kcal, 1),
                "target_kcal": round(data.targets.target_kcal, 1),
                "remaining_kcal": round(
                    data.targets.target_kcal - data.totals.consumed.kcal, 1
                ),
                "protein_consumed_g": round(data.totals.consumed.protein_g, 1),
                "protein_target_g": round(data.targets.protein_g, 1),
                "protein_remaining_g": round(
                    data.targets.protein_g - data.totals.consumed.protein_g, 1
                ),
                "carbs_consumed_g": round(data.totals.consumed.carbs_g, 1),
                "carbs_target_g": round(data.targets.carbs_g, 1),
                "fat_consumed_g": round(data.totals.consumed.fat_g, 1),
                "fat_target_g": round(data.targets.fat_g, 1),
                "fiber_consumed_g": fiber_consumed,
                "fiber_target_g": round(data.targets.fiber_g, 1),
                "fiber_remaining_g": round(data.targets.fiber_g - fiber_consumed, 1),
                "tdee_kcal": round(data.tdee.tdee_kcal, 1),
                "pending_confirmations": data.totals.pending_count,
                "planned_meals_remaining": planned_list,
            }
        )


class SuggestMealTool(llm.Tool):
    """Assist tool to propose 1-3 personalized meals matching remaining macros & constraints."""

    name = "suggest_meal"
    title = "Suggest balanced meal or recipe"
    description = (
        "Propose concrete meal/recipe ideas tailored to the user's remaining or slot "
        "calories, macros, dietary constraints, allergies, and max cooking time."
    )
    integration = DOMAIN
    parameters = vol.Schema(
        {
            vol.Optional("meal_slot"): str,
            vol.Optional("hint"): str,
            vol.Optional("max_cook_minutes"): vol.Coerce(int),
            vol.Optional("person"): str,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        """Generate constraint-verified, macro-scaled recipe suggestions."""
        runtime: DomainRuntime = hass.data[DOMAIN]
        args = tool_input.tool_args
        try:
            profile = resolve_person(
                hass,
                explicit_person=args.get("person"),
                context_user_id=_user_id_from_llm_context(llm_context),
            )
            person_rt = runtime.persons.get(profile.person_id)
            if person_rt is None or person_rt.coordinator.data is None:
                return llm.ToolResult(data={"error": "Person state not ready"}, error=True)
            data = person_rt.coordinator.data
            slots = profile.meal_slots or default_slots_for_language(profile.language)
            slot_name = resolve_meal_slot(
                args.get("meal_slot"), slots, language=profile.language
            )
            rem_kcal = data.targets.target_kcal - data.totals.consumed.kcal
            if rem_kcal > 150.0 and data.totals.consumed.kcal > 0:
                budget = Macros(
                    kcal=max(180.0, rem_kcal),
                    protein_g=max(10.0, data.targets.protein_g - data.totals.consumed.protein_g),
                    carbs_g=max(0.0, data.targets.carbs_g - data.totals.consumed.carbs_g),
                    fat_g=max(3.0, data.targets.fat_g - data.totals.consumed.fat_g),
                    fiber_g=max(0.0, data.targets.fiber_g - (data.totals.consumed.fiber_g or 0.0)),
                )
            else:
                slot_targets = allocate_slot_targets(data.targets, slots)
                budget = slot_targets[slot_name]
            suggestions = await runtime.planner_service.suggest_meal(
                profile,
                meal_slot=slot_name,
                target_macros=budget,
                user_hint=str(args.get("hint") or ""),
                max_cook_minutes=args.get("max_cook_minutes"),
                count=2,
            )
        except Exception as exc:  # noqa: BLE001
            return llm.ToolResult(data={"error": str(exc)}, error=True)

        opts_json: list[JsonValueType] = [
            {
                "title": s.title,
                "prep_minutes": s.prep_minutes,
                "kcal": round(s.macros.kcal),
                "protein_g": s.macros.protein_g,
                "carbs_g": s.macros.carbs_g,
                "fat_g": s.macros.fat_g,
                "fiber_g": s.macros.fiber_g or 0.0,
                "ingredients": [
                    f"{i.name} {round(i.grams)}g" for i in s.ingredients
                ],
            }
            for s in suggestions
        ]
        return llm.ToolResult(
            data={"person": profile.display_name, "meal_slot": slot_name, "options": opts_json}
        )


class GenerateMealPlanTool(llm.Tool):
    """Assist tool to generate a 1-7 day meal plan and project onto HA Calendar."""

    name = "generate_meal_plan"
    title = "Generate weekly or multi-day meal plan"
    description = (
        "Generate a personalized 1 to 7 day meal plan matching daily targets, "
        "allergies, preferences, and optional meal-prep batch reuse."
    )
    integration = DOMAIN
    parameters = vol.Schema(
        {
            vol.Optional("num_days", default=7): vol.Coerce(int),
            vol.Optional("meal_prep_mode", default=False): bool,
            vol.Optional("notes"): str,
            vol.Optional("person"): str,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        """Generate and save a new active meal plan starting today."""
        runtime: DomainRuntime = hass.data[DOMAIN]
        args = tool_input.tool_args
        try:
            profile = resolve_person(
                hass,
                explicit_person=args.get("person"),
                context_user_id=_user_id_from_llm_context(llm_context),
            )
            person_rt = runtime.persons.get(profile.person_id)
            if person_rt is None or person_rt.coordinator.data is None:
                return llm.ToolResult(data={"error": "Person state not ready"}, error=True)
            data = person_rt.coordinator.data
            num_days = max(1, min(7, int(args.get("num_days", 7))))
            plan = await runtime.planner_service.generate_meal_plan(
                profile,
                start_date=data.today,
                daily_targets=data.targets,
                options=PlanGenerationOptions(
                    num_days=num_days,
                    meal_prep_mode=bool(args.get("meal_prep_mode", False)),
                    notes=str(args.get("notes") or ""),
                ),
            )
            await person_rt.coordinator.async_request_refresh()
        except Exception as exc:  # noqa: BLE001
            return llm.ToolResult(data={"error": str(exc)}, error=True)

        return llm.ToolResult(
            data={
                "person": profile.display_name,
                "plan_id": plan.plan_id,
                "start_date": plan.start_date.isoformat(),
                "end_date": plan.end_date.isoformat(),
                "days_generated": len(plan.days),
            }
        )


class SwapPlanMealTool(llm.Tool):
    """Assist tool to replace one planned meal slot on a given date."""

    name = "swap_plan_meal"
    title = "Swap planned meal"
    description = (
        "Replace a single meal in the active meal plan (defaults to today) "
        "according to the user's instruction while preserving daily macro targets."
    )
    integration = DOMAIN
    parameters = vol.Schema(
        {
            vol.Required("meal_slot"): str,
            vol.Optional("instruction"): str,
            vol.Optional("date"): str,
            vol.Optional("person"): str,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        """Find the matching planned item and regenerate it."""
        runtime: DomainRuntime = hass.data[DOMAIN]
        args = tool_input.tool_args
        try:
            profile = resolve_person(
                hass,
                explicit_person=args.get("person"),
                context_user_id=_user_id_from_llm_context(llm_context),
            )
            target_d = (
                date.fromisoformat(args["date"])
                if args.get("date")
                else dt_util.now().date()
            )
            slot_name = resolve_meal_slot(
                str(args["meal_slot"]),
                profile.meal_slots,
                language=profile.language,
            )
            day_plan = await runtime.plan_repo.get_plan_day(profile.person_id, target_d)
            if day_plan is None:
                return llm.ToolResult(
                    data={"error": f"No active plan for {target_d.isoformat()}"},
                    error=True,
                )
            item = next(
                (
                    it
                    for it in day_plan.items
                    if it.meal_slot == slot_name and it.status == PlanItemStatus.PLANNED
                ),
                None,
            )
            if item is None:
                return llm.ToolResult(
                    data={"error": f"No planned meal in slot '{slot_name}' on {target_d}"},
                    error=True,
                )
            updated = await runtime.planner_service.swap_plan_meal(
                profile,
                item.plan_item_id,
                user_instruction=str(args.get("instruction") or ""),
            )
            person_rt = runtime.persons.get(profile.person_id)
            if person_rt is not None:
                await person_rt.coordinator.async_request_refresh()
        except Exception as exc:  # noqa: BLE001
            return llm.ToolResult(data={"error": str(exc)}, error=True)

        return llm.ToolResult(
            data={
                "person": profile.display_name,
                "date": target_d.isoformat(),
                "meal_slot": updated.meal_slot,
                "new_title": updated.title,
                "kcal": round(updated.macros.kcal),
                "protein_g": updated.macros.protein_g,
            }
        )


class RebalanceTodayTool(llm.Tool):
    """Assist tool to dynamically rebalance remaining planned meals or smooth over 2-4 days."""

    name = "rebalance_today"
    title = "Rebalance remaining meal plan"
    description = (
        "Adjust today's remaining planned meals to match actual remaining calories/macros "
        "(or spread an excess/deficit safely across 2-4 upcoming days if `spread_days` is given)."
    )
    integration = DOMAIN
    parameters = vol.Schema(
        {
            vol.Optional("spread_days"): vol.Coerce(int),
            vol.Optional("person"): str,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        """Execute intra-day or multi-day dynamic rebalancing."""
        runtime: DomainRuntime = hass.data[DOMAIN]
        args = tool_input.tool_args
        try:
            profile = resolve_person(
                hass,
                explicit_person=args.get("person"),
                context_user_id=_user_id_from_llm_context(llm_context),
            )
            person_rt = runtime.persons.get(profile.person_id)
            if person_rt is None or person_rt.coordinator.data is None:
                return llm.ToolResult(data={"error": "Person state not ready"}, error=True)
            data = person_rt.coordinator.data
            if args.get("spread_days") is not None:
                excess = data.totals.consumed.kcal - data.targets.target_kcal
                res = await runtime.rebalancer_service.rebalance_multiday(
                    profile,
                    start_date=data.today,
                    excess_kcal=excess,
                    bmr_kcal=data.tdee.bmr_kcal,
                    weight_kg=data.weight_kg,
                    spread_days=int(args["spread_days"]),
                )
            else:
                res = await runtime.rebalancer_service.rebalance_intraday(
                    profile,
                    target_date=data.today,
                    daily_targets=data.targets,
                )
            await person_rt.coordinator.async_request_refresh()
        except Exception as exc:  # noqa: BLE001
            return llm.ToolResult(data={"error": str(exc)}, error=True)

        warnings_json: list[JsonValueType] = list(res.safety_warnings)
        return llm.ToolResult(
            data={
                "person": profile.display_name,
                "mode": res.mode,
                "updated_items": len(res.updated_items),
                "summary": res.summary_message,
                "safety_warnings": warnings_json,
            }
        )


class CaloriesBuddyLLMAPI(llm.API):
    """Home Assistant Assist API exposing nutrition logging, summary, and planning tools."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass=hass, id=DOMAIN, name="HA Calories Buddy")

    async def async_get_api_instance(
        self, llm_context: llm.LLMContext
    ) -> llm.APIInstance:
        """Return API instance with all 7 nutrition & planning tools."""
        return llm.APIInstance(
            api=self,
            api_prompt=(
                "Use HA Calories Buddy tools whenever the user logs meals, corrects "
                "a logged food or meal slot, asks about today's calories/macros/fiber/TDEE, "
                "asks for recipe suggestions, creates/modifies a meal plan, or rebalances today."
            ),
            llm_context=llm_context,
            tools=[
                LogMealTool(),
                CorrectLastMealTool(),
                GetTodayNutritionSummaryTool(),
                SuggestMealTool(),
                GenerateMealPlanTool(),
                SwapPlanMealTool(),
                RebalanceTodayTool(),
            ],
        )


def async_register_llm_api(hass: HomeAssistant) -> Callable[[], None]:
    """Register HA Calories Buddy Assist API and return unregister callback."""
    return llm.async_register_api(hass, CaloriesBuddyLLMAPI(hass))
