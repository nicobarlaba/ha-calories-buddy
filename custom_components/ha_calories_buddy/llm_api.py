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
    MealStatus,
    PlanGenerationOptions,
    PlanItemStatus,
    PreparationState,
)
from custom_components.ha_calories_buddy.core.planner.macro_scaler import (
    compute_smart_slot_budget,
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
            local_now = dt_util.now()
            result = await runtime.logging_service.log_from_text(
                profile,
                str(args["text"]),
                log_date=local_now.date(),
                meal_slot=args.get("meal_slot"),
                source_channel="voice",
                current_local_time=local_now.strftime("%H:%M"),
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

        await runtime.async_notify_person_updated(profile.person_id, "assist_log_meal")

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
    """Assist tool to add, adjust, replace, remove items, or move meal_slot of a meal logged today."""

    name = "correct_last_meal"
    title = "Correct logged meal item or slot"
    description = (
        "Add a forgotten ingredient to an existing meal logged today (`add_food_name`, `add_quantity`, "
        "optional `target_meal_slot`), adjust quantity/unit/raw-cooked state of a logged item, "
        "swap a wrongly recognized ingredient with another food in the Vault (`replacement_food_name`), "
        "remove an unwanted extra ingredient (`remove_item: true`), and/or change the `meal_slot`."
    )
    integration = DOMAIN
    parameters = vol.Schema(
        {
            vol.Optional("item_name"): str,
            vol.Optional("new_quantity"): vol.Coerce(float),
            vol.Optional("new_unit"): str,
            vol.Optional("new_state"): str,
            vol.Optional("replacement_food_name"): str,
            vol.Optional("replacement_brand"): str,
            vol.Optional("remove_item", default=False): bool,
            vol.Optional("new_meal_slot"): str,
            vol.Optional("add_food_name"): str,
            vol.Optional("add_brand"): str,
            vol.Optional("add_quantity"): vol.Coerce(float),
            vol.Optional("add_unit"): str,
            vol.Optional("add_state"): str,
            vol.Optional("target_meal_slot"): str,
            vol.Optional("person"): str,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        """Add, update, swap, or remove an item and/or update meal_slot on today's meal."""
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
        add_name = str(args.get("add_food_name") or "").strip()
        if add_name:
            add_qty = float(args.get("add_quantity") or 100.0)
            if add_qty <= 0:
                return llm.ToolResult(
                    data={"error": "add_quantity must be greater than zero"},
                    error=True,
                )
            add_unit = str(args.get("add_unit") or "g")
            raw_add_state = str(args.get("add_state") or "unspecified")
            add_state = (
                PreparationState(raw_add_state)
                if raw_add_state in ("raw", "cooked", "ready_to_eat")
                else PreparationState.UNSPECIFIED
            )
            target_meal: str | None = None
            resolved_slot: str | None = None
            if args.get("target_meal_slot"):
                resolved_slot = resolve_meal_slot(
                    str(args["target_meal_slot"]),
                    profile.meal_slots,
                    language=profile.language,
                )
                for m in reversed(meals):
                    if m.meal_slot == resolved_slot:
                        target_meal = m.log_id
                        break
            elif meals:
                target_meal = meals[-1].log_id

            if target_meal is not None:
                added_meal = await runtime.logging_service.add_item_to_meal(
                    profile,
                    target_meal,
                    name=add_name,
                    brand=args.get("add_brand"),
                    quantity=add_qty,
                    unit=add_unit,
                    state=add_state,
                )
                if added_meal is None:
                    return llm.ToolResult(
                        data={"error": "Failed to add ingredient to meal"},
                        error=True,
                    )
            elif resolved_slot is not None:
                res = await runtime.logging_service.log_manual_item(
                    profile,
                    name=add_name,
                    quantity=add_qty,
                    unit=add_unit,
                    state=add_state,
                    meal_slot=resolved_slot,
                    log_date=today,
                    source_channel="assist_correct_meal",
                )
                added_meal = res.meal_log
            else:
                return llm.ToolResult(
                    data={"error": "No meals logged today and no target_meal_slot provided"},
                    error=True,
                )

            await runtime.async_notify_person_updated(
                profile.person_id, "assist_correct_meal"
            )
            return llm.ToolResult(
                data={
                    "person": profile.display_name,
                    "meal_slot": added_meal.meal_slot,
                    "added": True,
                    "new_meal_kcal": added_meal.total_macros.kcal,
                    "protein_g": added_meal.total_macros.protein_g,
                    "carbs_g": added_meal.total_macros.carbs_g,
                    "fat_g": added_meal.total_macros.fat_g,
                    "fiber_g": round(added_meal.total_macros.fiber_g or 0.0, 1),
                }
            )

        if not meals:
            return llm.ToolResult(data={"error": "No meals logged today"}, error=True)

        needle = str(args.get("item_name") or "").strip().lower()
        target_log_id: str | None = None
        target_item_id: str | None = None
        current_qty = 0.0

        if needle:
            for meal in reversed(meals):
                for item in reversed(meal.items):
                    if needle in item.display_name.lower():
                        target_log_id = meal.log_id
                        target_item_id = item.item_id
                        current_qty = item.input_quantity
                        break
                if target_item_id is not None:
                    break
            if target_log_id is None:
                return llm.ToolResult(
                    data={"error": f"No item matching '{args['item_name']}' logged today"},
                    error=True,
                )
        else:
            latest_meal = meals[-1]
            target_log_id = latest_meal.log_id
            if len(latest_meal.items) == 1:
                target_item_id = latest_meal.items[0].item_id
                current_qty = latest_meal.items[0].input_quantity

        remove_requested = bool(args.get("remove_item")) or (
            args.get("new_quantity") is not None and float(args["new_quantity"]) <= 0
        )
        if remove_requested:
            if target_item_id is None:
                return llm.ToolResult(
                    data={"error": "Specify item_name to remove a specific ingredient"},
                    error=True,
                )
            removed_meal = await runtime.logging_service.remove_meal_item(
                profile, target_log_id, target_item_id
            )
            if removed_meal is None:
                return llm.ToolResult(
                    data={"error": "Failed to remove item from meal"},
                    error=True,
                )
            await runtime.async_notify_person_updated(
                profile.person_id, "assist_correct_meal"
            )
            return llm.ToolResult(
                data={
                    "person": profile.display_name,
                    "meal_slot": removed_meal.meal_slot,
                    "removed": True,
                    "meal_deleted": removed_meal.status == MealStatus.DELETED,
                    "new_meal_kcal": removed_meal.total_macros.kcal,
                    "protein_g": removed_meal.total_macros.protein_g,
                    "carbs_g": removed_meal.total_macros.carbs_g,
                    "fat_g": removed_meal.total_macros.fat_g,
                    "fiber_g": round(removed_meal.total_macros.fiber_g or 0.0, 1),
                }
            )

        updated = None
        replacement_name = str(args.get("replacement_food_name") or "").strip()
        if replacement_name:
            if target_item_id is None:
                return llm.ToolResult(
                    data={"error": "Specify item_name to replace a specific ingredient"},
                    error=True,
                )
            replacement = await runtime.resolver.resolve_food_candidate(
                person_id=profile.person_id,
                name=replacement_name,
                brand=args.get("replacement_brand"),
                language=profile.language,
                llm_estimator=None,
            )
            if replacement is None:
                return llm.ToolResult(
                    data={"error": f"Food '{replacement_name}' not found in Food Vault"},
                    error=True,
                )
            state = (
                PreparationState(args["new_state"])
                if args.get("new_state") in ("raw", "cooked", "ready_to_eat")
                else None
            )
            qty_to_use = (
                float(args["new_quantity"])
                if args.get("new_quantity") is not None
                else max(1.0, current_qty)
            )
            updated = await runtime.logging_service.update_item_measurement(
                profile,
                target_log_id,
                target_item_id,
                quantity=qty_to_use,
                unit=args.get("new_unit"),
                state=state,
                food_id=replacement.food_id,
            )
        elif target_item_id is not None and args.get("new_quantity") is not None:
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
                data={
                    "error": (
                        "Specify new_quantity, replacement_food_name, remove_item, "
                        "or new_meal_slot to update"
                    )
                },
                error=True,
            )

        await runtime.async_notify_person_updated(
            profile.person_id, "assist_correct_meal"
        )

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
            vol.Optional("budget_mode", default="auto"): str,
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
            today = dt_util.now().date()
            totals = await runtime.meal_repo.get_daily_totals(profile.person_id, today)
            budget, mode_used = compute_smart_slot_budget(
                data.targets,
                slots,
                slot_name,
                totals.consumed,
                set(totals.by_slot.keys()),
                budget_mode=str(args.get("budget_mode") or "auto"),
            )
            suggestions = await runtime.planner_service.suggest_meal(
                profile,
                meal_slot=slot_name,
                target_macros=budget,
                user_hint=str(args.get("hint") or ""),
                max_cook_minutes=args.get("max_cook_minutes"),
                count=3,
                reference_date=today,
                budget_mode=mode_used,
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
                    f"[{i.course}] {i.name} {round(i.grams)}g"
                    if i.course
                    else f"{i.name} {round(i.grams)}g"
                    for i in s.ingredients
                ],
            }
            for s in suggestions
        ]
        return llm.ToolResult(
            data={
                "person": profile.display_name,
                "meal_slot": slot_name,
                "target_kcal": round(budget.kcal),
                "budget_mode_used": mode_used,
                "options": opts_json,
            }
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
            await runtime.async_notify_person_updated(
                profile.person_id, "assist_generate_plan"
            )
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
            await runtime.async_notify_person_updated(
                profile.person_id, "assist_swap_plan_meal"
            )
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
            await runtime.async_notify_person_updated(
                profile.person_id, "assist_rebalance_today"
            )
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
