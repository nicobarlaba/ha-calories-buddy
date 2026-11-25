"""Home Assistant Assist llm.API tool registration."""

from __future__ import annotations

import logging
from collections.abc import Callable

import probatio as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from homeassistant.util.json import JsonValueType

from custom_components.ha_calories_buddy.const import DOMAIN
from custom_components.ha_calories_buddy.core.logging import get_logger, log_event
from custom_components.ha_calories_buddy.core.models import PreparationState
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
        "Preserve all brand names, quantities, units, and raw/cooked wording verbatim in `text`."
    )
    integration = DOMAIN
    parameters = vol.Schema(
        {
            vol.Required("text"): str,
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
                "status": result.meal_log.status.value,
                "needs_confirmation": result.needs_confirmation,
                "summary": result.summary_message,
                "warnings": warnings_json,
                "kcal": result.meal_log.total_macros.kcal,
                "protein_g": result.meal_log.total_macros.protein_g,
                "carbs_g": result.meal_log.total_macros.carbs_g,
                "fat_g": result.meal_log.total_macros.fat_g,
            }
        )


class CorrectLastMealTool(llm.Tool):
    """Assist tool to adjust quantity/unit/state of an item in today's meals."""

    name = "correct_last_meal"
    title = "Correct logged meal item"
    description = (
        "Adjust the quantity, unit, or raw/cooked state of a food item logged today."
    )
    integration = DOMAIN
    parameters = vol.Schema(
        {
            vol.Required("item_name"): str,
            vol.Required("new_quantity"): vol.Coerce(float),
            vol.Optional("new_unit"): str,
            vol.Optional("new_state"): str,
            vol.Optional("person"): str,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        """Find the latest matching logged item today and update its measurement."""
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
        needle = str(args["item_name"]).strip().lower()

        target_log_id: str | None = None
        target_item_id: str | None = None
        for meal in reversed(meals):
            for item in reversed(meal.items):
                if needle in item.display_name.lower():
                    target_log_id = meal.log_id
                    target_item_id = item.item_id
                    break
            if target_item_id is not None:
                break

        if target_log_id is None or target_item_id is None:
            return llm.ToolResult(
                data={"error": f"No item matching '{args['item_name']}' logged today"},
                error=True,
            )

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
        person_rt = runtime.persons.get(profile.person_id)
        if person_rt is not None:
            await person_rt.coordinator.async_request_refresh()

        if updated is None:
            return llm.ToolResult(data={"error": "Failed to update item"}, error=True)

        return llm.ToolResult(
            data={
                "person": profile.display_name,
                "meal_slot": updated.meal_slot,
                "new_meal_kcal": updated.total_macros.kcal,
                "protein_g": updated.total_macros.protein_g,
                "carbs_g": updated.total_macros.carbs_g,
                "fat_g": updated.total_macros.fat_g,
            }
        )


class GetTodayNutritionSummaryTool(llm.Tool):
    """Assist tool to query today's calories, macros, remaining budget, and TDEE."""

    name = "get_today_nutrition_summary"
    title = "Get today's nutrition summary"
    description = (
        "Check consumed and remaining calories, protein, carbs, fat, TDEE, "
        "and pending meal confirmations for today."
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
                "tdee_kcal": round(data.tdee.tdee_kcal, 1),
                "pending_confirmations": data.totals.pending_count,
            }
        )


class CaloriesBuddyLLMAPI(llm.API):
    """Home Assistant Assist API exposing nutrition logging and summary tools."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass=hass, id=DOMAIN, name="HA Calories Buddy")

    async def async_get_api_instance(
        self, llm_context: llm.LLMContext
    ) -> llm.APIInstance:
        """Return API instance with nutrition tools."""
        return llm.APIInstance(
            api=self,
            api_prompt=(
                "Use HA Calories Buddy tools whenever the user logs meals, corrects "
                "a logged food quantity, or asks about today's calories, macros, or TDEE."
            ),
            llm_context=llm_context,
            tools=[
                LogMealTool(),
                CorrectLastMealTool(),
                GetTodayNutritionSummaryTool(),
            ],
        )


def async_register_llm_api(hass: HomeAssistant) -> Callable[[], None]:
    """Register HA Calories Buddy Assist API and return unregister callback."""
    return llm.async_register_api(hass, CaloriesBuddyLLMAPI(hass))
