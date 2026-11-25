"""Home Assistant service registrations for ha-calories-buddy."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import probatio as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from custom_components.ha_calories_buddy.const import (
    ATTR_BARCODE,
    ATTR_DATE,
    ATTR_FOOD_ID,
    ATTR_ITEM_ID,
    ATTR_LOG_ID,
    ATTR_MEAL_SLOT,
    ATTR_PERSON,
    ATTR_QUANTITY,
    ATTR_STATE,
    ATTR_TEXT,
    ATTR_UNIT,
    ATTR_WEIGHT_KG,
    DOMAIN,
)
from custom_components.ha_calories_buddy.core.logging import get_logger, log_event
from custom_components.ha_calories_buddy.core.models import (
    MealStatus,
    PersonProfile,
    PlanGenerationOptions,
    PreferenceScope,
    PreferenceStance,
    PreparationState,
)
from custom_components.ha_calories_buddy.core.physiology.targets import TargetResult
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


async def _maybe_notify(
    hass: HomeAssistant,
    profile: PersonProfile,
    title: str,
    message: str,
) -> None:
    svc = profile.entity_mappings.notify_service
    if not svc or "." not in svc:
        return
    domain, service_name = svc.split(".", maxsplit=1)
    if not hass.services.has_service(domain, service_name):
        return
    try:
        await hass.services.async_call(
            domain,
            service_name,
            {"title": title, "message": message},
            blocking=False,
        )
    except Exception as exc:  # noqa: BLE001
        log_event(
            _LOGGER,
            logging.WARNING,
            "notify_dispatch_failed",
            error_type=type(exc).__name__,
        )


def _resolve_target_person(
    hass: HomeAssistant, call: ServiceCall
) -> PersonProfile:
    try:
        return resolve_person(
            hass,
            explicit_person=call.data.get(ATTR_PERSON),
            context_user_id=call.context.user_id,
        )
    except (PersonNotFoundError, PersonAmbiguousError) as exc:
        raise HomeAssistantError(str(exc)) from exc


async def _refresh_person(
    hass: HomeAssistant, person_id: str, reason: str = "service"
) -> None:
    runtime: DomainRuntime = hass.data[DOMAIN]
    await runtime.async_notify_person_updated(person_id, reason)


async def _require_targets(
    hass: HomeAssistant, profile: PersonProfile
) -> tuple[TargetResult, float, float]:
    runtime: DomainRuntime = hass.data[DOMAIN]
    person_rt = runtime.persons.get(profile.person_id)
    if person_rt is None:
        raise HomeAssistantError("Person profile is not active")
    if person_rt.coordinator.data is None:
        await person_rt.coordinator.async_refresh()
    data = person_rt.coordinator.data
    if data is None:
        raise HomeAssistantError("Person dashboard state not ready")
    return data.targets, data.tdee.bmr_kcal, data.weight_kg


def async_register_services(hass: HomeAssistant) -> None:
    """Register domain services on Home Assistant."""

    async def handle_log_meal_text(call: ServiceCall) -> dict[str, Any]:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_target_person(hass, call)
        local_now = dt_util.now()
        log_d = (
            date.fromisoformat(call.data[ATTR_DATE])
            if ATTR_DATE in call.data
            else local_now.date()
        )
        try:
            result = await runtime.logging_service.log_from_text(
                profile,
                str(call.data[ATTR_TEXT]),
                log_date=log_d,
                meal_slot=call.data.get(ATTR_MEAL_SLOT),
                source_channel="service",
                current_local_time=local_now.strftime("%H:%M"),
            )
        except Exception as exc:
            raise HomeAssistantError(str(exc)) from exc

        await _refresh_person(hass, profile.person_id, "service_log_meal_text")
        msg = (
            f"{result.summary_message}\n" + " ".join(result.warnings)
            if result.warnings
            else result.summary_message
        )
        await _maybe_notify(hass, profile, f"Calories Buddy — {profile.display_name}", msg)
        return {
            "summary": result.summary_message,
            "needs_confirmation": result.needs_confirmation,
            "warnings": result.warnings,
            "meal_log": result.meal_log.model_dump(mode="json"),
        }

    async def handle_log_meal_item(call: ServiceCall) -> dict[str, Any]:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_target_person(hass, call)
        log_d = (
            date.fromisoformat(call.data[ATTR_DATE])
            if ATTR_DATE in call.data
            else dt_util.now().date()
        )
        state = PreparationState(
            call.data.get(ATTR_STATE, PreparationState.UNSPECIFIED.value)
        )
        result = await runtime.logging_service.log_manual_item(
            profile,
            food_id=call.data.get(ATTR_FOOD_ID),
            barcode=call.data.get(ATTR_BARCODE),
            name=str(call.data.get("name") or ""),
            quantity=float(call.data[ATTR_QUANTITY]),
            unit=str(call.data.get(ATTR_UNIT, "g")),
            state=state,
            meal_slot=call.data.get(ATTR_MEAL_SLOT),
            log_date=log_d,
            source_channel="manual",
        )
        await _refresh_person(hass, profile.person_id, "service_log_meal_item")
        return {
            "summary": result.summary_message,
            "needs_confirmation": result.needs_confirmation,
            "meal_log": result.meal_log.model_dump(mode="json"),
        }

    async def handle_confirm_meal(call: ServiceCall) -> dict[str, Any]:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_target_person(hass, call)
        confirmed = await runtime.logging_service.confirm_pending_meal(
            profile,
            str(call.data[ATTR_LOG_ID]),
            promote_to_vault=bool(call.data.get("promote_to_vault", False)),
        )
        if confirmed is None:
            raise HomeAssistantError("Meal log not found for this person")
        await _refresh_person(hass, profile.person_id)
        return {"meal_log": confirmed.model_dump(mode="json")}

    async def handle_add_meal_item(call: ServiceCall) -> dict[str, Any]:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_target_person(hass, call)
        raw_state = str(call.data.get(ATTR_STATE, PreparationState.UNSPECIFIED.value))
        state = (
            PreparationState(raw_state)
            if raw_state in ("raw", "cooked", "ready_to_eat", "unspecified")
            else PreparationState.UNSPECIFIED
        )
        updated = await runtime.logging_service.add_item_to_meal(
            profile,
            str(call.data[ATTR_LOG_ID]),
            food_id=call.data.get(ATTR_FOOD_ID) or None,
            barcode=call.data.get(ATTR_BARCODE) or None,
            name=str(call.data.get("name") or ""),
            brand=call.data.get("brand") or None,
            quantity=float(call.data[ATTR_QUANTITY]),
            unit=str(call.data.get(ATTR_UNIT, "g")),
            state=state,
        )
        if updated is None:
            raise HomeAssistantError("Meal log not found")
        await _refresh_person(hass, profile.person_id, "service_add_meal_item")
        return {"meal_log": updated.model_dump(mode="json")}

    async def handle_update_meal_item(call: ServiceCall) -> dict[str, Any]:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_target_person(hass, call)
        state = (
            PreparationState(call.data[ATTR_STATE])
            if ATTR_STATE in call.data
            else None
        )
        updated = await runtime.logging_service.update_item_measurement(
            profile,
            str(call.data[ATTR_LOG_ID]),
            str(call.data[ATTR_ITEM_ID]),
            quantity=float(call.data[ATTR_QUANTITY]),
            unit=call.data.get(ATTR_UNIT),
            state=state,
            food_id=call.data.get(ATTR_FOOD_ID) or None,
        )
        if updated is None:
            raise HomeAssistantError("Meal item not found")
        await _refresh_person(hass, profile.person_id, "service_update_meal_item")
        return {"meal_log": updated.model_dump(mode="json")}

    async def handle_delete_meal_item(call: ServiceCall) -> dict[str, Any]:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_target_person(hass, call)
        updated = await runtime.logging_service.remove_meal_item(
            profile,
            str(call.data[ATTR_LOG_ID]),
            str(call.data[ATTR_ITEM_ID]),
        )
        if updated is None:
            raise HomeAssistantError("Meal item not found")
        await _refresh_person(hass, profile.person_id, "service_delete_meal_item")
        return {
            "meal_log": updated.model_dump(mode="json"),
            "meal_deleted": updated.status == MealStatus.DELETED,
        }

    async def handle_update_meal_slot(call: ServiceCall) -> dict[str, Any]:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_target_person(hass, call)
        updated = await runtime.logging_service.change_meal_slot(
            profile,
            str(call.data[ATTR_LOG_ID]),
            str(call.data[ATTR_MEAL_SLOT]),
        )
        if updated is None:
            raise HomeAssistantError("Meal log not found")
        await _refresh_person(hass, profile.person_id)
        return {"meal_log": updated.model_dump(mode="json")}

    async def handle_delete_meal(call: ServiceCall) -> dict[str, Any]:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_target_person(hass, call)
        deleted = await runtime.meal_repo.soft_delete_meal(
            profile.person_id, str(call.data[ATTR_LOG_ID])
        )
        if not deleted:
            raise HomeAssistantError("Meal log not found")
        await _refresh_person(hass, profile.person_id)
        return {"deleted": True, "log_id": str(call.data[ATTR_LOG_ID])}

    async def handle_lookup_barcode(call: ServiceCall) -> dict[str, Any]:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_target_person(hass, call)
        food = await runtime.resolver.lookup_barcode(
            str(call.data[ATTR_BARCODE]), person_id=profile.person_id
        )
        return {"food": food.model_dump(mode="json") if food else None}

    async def handle_log_weight(call: ServiceCall) -> dict[str, Any]:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_target_person(hass, call)
        person_rt = runtime.persons.get(profile.person_id)
        if person_rt is None:
            raise HomeAssistantError("Person coordinator not active")
        snap = await person_rt.coordinator.async_persist_today_snapshot(
            override_weight_kg=float(call.data[ATTR_WEIGHT_KG])
        )
        await _refresh_person(hass, profile.person_id, "service_log_weight")
        return {"snapshot": snap.model_dump(mode="json")}

    async def handle_suggest_meal(call: ServiceCall) -> dict[str, Any]:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_target_person(hass, call)
        targets, _, _ = await _require_targets(hass, profile)
        slots = profile.meal_slots or default_slots_for_language(profile.language)
        slot_name = resolve_meal_slot(
            call.data.get(ATTR_MEAL_SLOT), slots, language=profile.language
        )
        today = dt_util.now().date()
        totals = await runtime.meal_repo.get_daily_totals(profile.person_id, today)
        budget_mode_arg = str(call.data.get("budget_mode") or "auto")
        slot_macro, mode_used = compute_smart_slot_budget(
            targets,
            slots,
            slot_name,
            totals.consumed,
            set(totals.by_slot.keys()),
            budget_mode=budget_mode_arg,
        )
        try:
            suggestions = await runtime.planner_service.suggest_meal(
                profile,
                meal_slot=slot_name,
                target_macros=slot_macro,
                user_hint=str(call.data.get("hint") or ""),
                max_cook_minutes=call.data.get("max_cook_minutes"),
                count=int(call.data.get("count", 3)),
                reference_date=today,
                budget_mode=mode_used,
            )
        except Exception as exc:
            raise HomeAssistantError(str(exc)) from exc
        return {
            "suggestions": [s.model_dump(mode="json") for s in suggestions],
            "target_macros": slot_macro.model_dump(mode="json"),
            "budget_mode_used": mode_used,
            "meal_slot": slot_name,
        }

    async def handle_generate_meal_plan(call: ServiceCall) -> dict[str, Any]:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_target_person(hass, call)
        targets, _, _ = await _require_targets(hass, profile)
        start_d = (
            date.fromisoformat(call.data["start_date"])
            if call.data.get("start_date")
            else dt_util.now().date()
        )
        options = PlanGenerationOptions(
            num_days=int(call.data.get("num_days", 7)),
            meal_prep_mode=bool(call.data.get("meal_prep_mode", False)),
            notes=str(call.data.get("notes") or ""),
        )
        try:
            plan = await runtime.planner_service.generate_meal_plan(
                profile,
                start_date=start_d,
                daily_targets=targets,
                options=options,
            )
        except Exception as exc:
            raise HomeAssistantError(str(exc)) from exc
        await _refresh_person(hass, profile.person_id)
        return {"plan": plan.model_dump(mode="json")}

    async def handle_swap_plan_meal(call: ServiceCall) -> dict[str, Any]:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_target_person(hass, call)
        try:
            updated = await runtime.planner_service.swap_plan_meal(
                profile,
                str(call.data["plan_item_id"]),
                user_instruction=str(call.data.get("instruction") or ""),
            )
        except Exception as exc:
            raise HomeAssistantError(str(exc)) from exc
        await _refresh_person(hass, profile.person_id)
        return {"plan_item": updated.model_dump(mode="json")}

    async def handle_promote_plan_meal(call: ServiceCall) -> dict[str, Any]:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_target_person(hass, call)
        try:
            logged = await runtime.planner_service.promote_plan_meal(
                profile,
                str(call.data["plan_item_id"]),
                actual_scale=float(call.data.get("scale", 1.0)),
            )
        except Exception as exc:
            raise HomeAssistantError(str(exc)) from exc
        await _refresh_person(hass, profile.person_id)
        return {"meal_log": logged.model_dump(mode="json")}

    async def handle_rebalance_today(call: ServiceCall) -> dict[str, Any]:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_target_person(hass, call)
        targets, bmr_kcal, weight_kg = await _require_targets(hass, profile)
        target_d = (
            date.fromisoformat(call.data[ATTR_DATE])
            if call.data.get(ATTR_DATE)
            else dt_util.now().date()
        )
        spread_days = call.data.get("spread_days")
        if spread_days is not None:
            totals = await runtime.meal_repo.get_daily_totals(
                profile.person_id, target_d
            )
            excess = totals.consumed.kcal - targets.target_kcal
            res = await runtime.rebalancer_service.rebalance_multiday(
                profile,
                start_date=target_d,
                excess_kcal=excess,
                bmr_kcal=bmr_kcal,
                weight_kg=weight_kg,
                spread_days=int(spread_days),
            )
        else:
            res = await runtime.rebalancer_service.rebalance_intraday(
                profile,
                target_date=target_d,
                daily_targets=targets,
                allow_llm_swap=bool(call.data.get("allow_llm_swap", True)),
            )
        await _refresh_person(hass, profile.person_id)
        return {"rebalance": res.model_dump(mode="json")}

    async def handle_set_preference(call: ServiceCall) -> dict[str, Any]:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_target_person(hass, call)
        scope = PreferenceScope(str(call.data["scope"]))
        pref = await runtime.preference_repo.upsert_preference(
            scope=scope,
            person_id=profile.person_id if scope == PreferenceScope.PERSON else None,
            target_type=str(call.data["target_type"]),
            target_value=str(call.data["target_value"]),
            stance=PreferenceStance(str(call.data["stance"])),
            notes=str(call.data.get("notes") or ""),
        )
        return {"preference": pref.model_dump(mode="json")}

    hass.services.async_register(
        DOMAIN,
        "log_meal_text",
        handle_log_meal_text,
        schema=vol.Schema(
            {
                vol.Required(ATTR_TEXT): cv.string,
                vol.Optional(ATTR_PERSON): cv.string,
                vol.Optional(ATTR_DATE): cv.string,
                vol.Optional(ATTR_MEAL_SLOT): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "log_meal_item",
        handle_log_meal_item,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_FOOD_ID): cv.string,
                vol.Optional(ATTR_BARCODE): cv.string,
                vol.Optional("name"): cv.string,
                vol.Required(ATTR_QUANTITY): vol.Coerce(float),
                vol.Optional(ATTR_UNIT, default="g"): cv.string,
                vol.Optional(ATTR_STATE, default="unspecified"): cv.string,
                vol.Optional(ATTR_MEAL_SLOT): cv.string,
                vol.Optional(ATTR_PERSON): cv.string,
                vol.Optional(ATTR_DATE): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "confirm_meal",
        handle_confirm_meal,
        schema=vol.Schema(
            {
                vol.Required(ATTR_LOG_ID): cv.string,
                vol.Optional("promote_to_vault", default=False): cv.boolean,
                vol.Optional(ATTR_PERSON): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "add_meal_item",
        handle_add_meal_item,
        schema=vol.Schema(
            {
                vol.Required(ATTR_LOG_ID): cv.string,
                vol.Optional(ATTR_FOOD_ID): cv.string,
                vol.Optional(ATTR_BARCODE): cv.string,
                vol.Optional("name"): cv.string,
                vol.Optional("brand"): cv.string,
                vol.Required(ATTR_QUANTITY): vol.Coerce(float),
                vol.Optional(ATTR_UNIT, default="g"): cv.string,
                vol.Optional(ATTR_STATE, default="unspecified"): cv.string,
                vol.Optional(ATTR_PERSON): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "update_meal_item",
        handle_update_meal_item,
        schema=vol.Schema(
            {
                vol.Required(ATTR_LOG_ID): cv.string,
                vol.Required(ATTR_ITEM_ID): cv.string,
                vol.Required(ATTR_QUANTITY): vol.Coerce(float),
                vol.Optional(ATTR_UNIT): cv.string,
                vol.Optional(ATTR_STATE): cv.string,
                vol.Optional(ATTR_FOOD_ID): cv.string,
                vol.Optional(ATTR_PERSON): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "delete_meal_item",
        handle_delete_meal_item,
        schema=vol.Schema(
            {
                vol.Required(ATTR_LOG_ID): cv.string,
                vol.Required(ATTR_ITEM_ID): cv.string,
                vol.Optional(ATTR_PERSON): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "update_meal_slot",
        handle_update_meal_slot,
        schema=vol.Schema(
            {
                vol.Required(ATTR_LOG_ID): cv.string,
                vol.Required(ATTR_MEAL_SLOT): cv.string,
                vol.Optional(ATTR_PERSON): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "delete_meal",
        handle_delete_meal,
        schema=vol.Schema(
            {
                vol.Required(ATTR_LOG_ID): cv.string,
                vol.Optional(ATTR_PERSON): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "lookup_barcode",
        handle_lookup_barcode,
        schema=vol.Schema(
            {
                vol.Required(ATTR_BARCODE): cv.string,
                vol.Optional(ATTR_PERSON): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "log_weight",
        handle_log_weight,
        schema=vol.Schema(
            {
                vol.Required(ATTR_WEIGHT_KG): vol.Coerce(float),
                vol.Optional(ATTR_PERSON): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "suggest_meal",
        handle_suggest_meal,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_MEAL_SLOT): cv.string,
                vol.Optional("hint"): cv.string,
                vol.Optional("max_cook_minutes"): vol.Coerce(int),
                vol.Optional("count", default=3): vol.Coerce(int),
                vol.Optional("budget_mode", default="auto"): cv.string,
                vol.Optional(ATTR_PERSON): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "generate_meal_plan",
        handle_generate_meal_plan,
        schema=vol.Schema(
            {
                vol.Optional("start_date"): cv.string,
                vol.Optional("num_days", default=7): vol.Coerce(int),
                vol.Optional("meal_prep_mode", default=False): cv.boolean,
                vol.Optional("notes"): cv.string,
                vol.Optional(ATTR_PERSON): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "swap_plan_meal",
        handle_swap_plan_meal,
        schema=vol.Schema(
            {
                vol.Required("plan_item_id"): cv.string,
                vol.Optional("instruction"): cv.string,
                vol.Optional(ATTR_PERSON): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "promote_plan_meal",
        handle_promote_plan_meal,
        schema=vol.Schema(
            {
                vol.Required("plan_item_id"): cv.string,
                vol.Optional("scale", default=1.0): vol.Coerce(float),
                vol.Optional(ATTR_PERSON): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "rebalance_today",
        handle_rebalance_today,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_DATE): cv.string,
                vol.Optional("spread_days"): vol.Coerce(int),
                vol.Optional("allow_llm_swap", default=True): cv.boolean,
                vol.Optional(ATTR_PERSON): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "set_preference",
        handle_set_preference,
        schema=vol.Schema(
            {
                vol.Required("scope"): cv.string,
                vol.Required("target_type"): cv.string,
                vol.Required("target_value"): cv.string,
                vol.Required("stance"): cv.string,
                vol.Optional("notes"): cv.string,
                vol.Optional(ATTR_PERSON): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    # Keep timedelta import clean if needed later; explicitly avoid unused import
    _ = timedelta
