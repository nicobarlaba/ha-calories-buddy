"""Home Assistant service registrations for ha-calories-buddy."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import probatio as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

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
    PersonProfile,
    PreparationState,
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


async def _refresh_person(hass: HomeAssistant, person_id: str) -> None:
    runtime: DomainRuntime = hass.data[DOMAIN]
    person_rt = runtime.persons.get(person_id)
    if person_rt is not None:
        await person_rt.coordinator.async_request_refresh()


def async_register_services(hass: HomeAssistant) -> None:
    """Register domain services on Home Assistant."""

    async def handle_log_meal_text(call: ServiceCall) -> dict[str, Any]:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_target_person(hass, call)
        log_d = (
            date.fromisoformat(call.data[ATTR_DATE])
            if ATTR_DATE in call.data
            else None
        )
        try:
            result = await runtime.logging_service.log_from_text(
                profile,
                str(call.data[ATTR_TEXT]),
                log_date=log_d,
                meal_slot=call.data.get(ATTR_MEAL_SLOT),
                source_channel="service",
            )
        except Exception as exc:
            raise HomeAssistantError(str(exc)) from exc

        await _refresh_person(hass, profile.person_id)
        if result.warnings:
            msg = f"{result.summary_message}\n" + " ".join(result.warnings)
        else:
            msg = result.summary_message
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
            else None
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
            meal_slot=str(call.data.get(ATTR_MEAL_SLOT, "Snack")),
            log_date=log_d,
            source_channel="manual",
        )
        await _refresh_person(hass, profile.person_id)
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
        )
        if updated is None:
            raise HomeAssistantError("Meal item not found")
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
        await person_rt.coordinator.async_request_refresh()
        return {"snapshot": snap.model_dump(mode="json")}

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
                vol.Optional(ATTR_MEAL_SLOT, default="Snack"): cv.string,
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
        "update_meal_item",
        handle_update_meal_item,
        schema=vol.Schema(
            {
                vol.Required(ATTR_LOG_ID): cv.string,
                vol.Required(ATTR_ITEM_ID): cv.string,
                vol.Required(ATTR_QUANTITY): vol.Coerce(float),
                vol.Optional(ATTR_UNIT): cv.string,
                vol.Optional(ATTR_STATE): cv.string,
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
