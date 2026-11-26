"""Home Assistant Companion App actionable notifications & mobile_app action listener."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from homeassistant.core import Event, HomeAssistant

from custom_components.ha_calories_buddy.const import DOMAIN
from custom_components.ha_calories_buddy.core.logging import get_logger, log_event
from custom_components.ha_calories_buddy.core.models import MealLog, PersonProfile
from custom_components.ha_calories_buddy.runtime import DomainRuntime

_LOGGER = get_logger(__name__)

ACTION_CONFIRM_PREFIX = "CB_CONFIRM_"
ACTION_DELETE_PREFIX = "CB_DELETE_"
ACTION_UNDO_PREFIX = "CB_UNDO_"


def _parse_notify_service(raw: str | None) -> tuple[str, str] | None:
    svc = (raw or "").strip()
    if not svc:
        return None
    if "." in svc:
        domain, name = svc.split(".", maxsplit=1)
        return domain.strip(), name.strip()
    return "notify", svc


async def async_send_meal_notification(
    hass: HomeAssistant,
    profile: PersonProfile,
    meal_log: MealLog,
    *,
    needs_confirmation: bool,
    warnings: list[str] | None = None,
) -> bool:
    """Dispatch a Companion App actionable notification for a newly logged meal."""
    parsed = _parse_notify_service(profile.entity_mappings.notify_service)
    if parsed is None:
        return False
    domain, service_name = parsed
    if not hass.services.has_service(domain, service_name):
        return False

    lang = profile.language or "en"
    kcal = round(meal_log.total_macros.kcal)
    items_txt = ", ".join(it.display_name for it in meal_log.items[:4])
    title = (
        f"Calories Buddy ({profile.display_name}) — Conferma Pasto"
        if needs_confirmation and lang == "it"
        else (
            f"Calories Buddy ({profile.display_name}) — Confirm Meal"
            if needs_confirmation
            else f"Calories Buddy — {profile.display_name}"
        )
    )
    if needs_confirmation:
        warn_str = f"\n⚠️ {' '.join(warnings[:2])}" if warnings else ""
        message = (
            f"{meal_log.meal_slot}: {items_txt or meal_log.raw_input_text or ''} ({kcal} kcal){warn_str}".strip()
        )
        actions = [
            {
                "action": f"{ACTION_CONFIRM_PREFIX}{meal_log.log_id}",
                "title": "Conferma" if lang == "it" else "Confirm",
            },
            {
                "action": f"{ACTION_DELETE_PREFIX}{meal_log.log_id}",
                "title": "Elimina" if lang == "it" else "Delete",
                "destructive": True,
            },
        ]
    else:
        message = f"{meal_log.meal_slot}: {items_txt or ''} — {kcal} kcal (P {round(meal_log.total_macros.protein_g)}g)".strip()
        actions = [
            {
                "action": f"{ACTION_UNDO_PREFIX}{meal_log.log_id}",
                "title": "Annulla" if lang == "it" else "Undo",
                "destructive": True,
            }
        ]

    payload: dict[str, Any] = {
        "title": title,
        "message": message,
        "data": {
            "tag": f"ha_cb_{meal_log.log_id}",
            "actions": actions,
        },
    }
    try:
        await hass.services.async_call(domain, service_name, payload, blocking=False)
        return True
    except Exception as exc:  # noqa: BLE001
        log_event(
            _LOGGER,
            logging.WARNING,
            "actionable_notify_failed",
            error_type=type(exc).__name__,
        )
        return False


def async_register_notification_listener(hass: HomeAssistant) -> Callable[[], None]:
    """Listen for mobile_app_notification_action events targeting Calories Buddy meals."""

    async def _handle_action(event: Event) -> None:
        action = str(event.data.get("action") or "").strip()
        if not action:
            return
        runtime: DomainRuntime | None = hass.data.get(DOMAIN)
        if runtime is None:
            return

        if action.startswith(ACTION_CONFIRM_PREFIX):
            log_id = action[len(ACTION_CONFIRM_PREFIX) :]
            meal = await runtime.meal_repo.get_meal_log(log_id)
            if meal is None:
                return
            person_rt = runtime.persons.get(meal.person_id)
            if person_rt is not None:
                await runtime.logging_service.confirm_pending_meal(
                    person_rt.profile, log_id, promote_to_vault=False
                )
            else:
                await runtime.meal_repo.confirm_meal(meal.person_id, log_id)
            await runtime.async_notify_person_updated(
                meal.person_id, "notify_confirm_meal"
            )
            log_event(
                _LOGGER,
                logging.INFO,
                "notify_meal_confirmed",
                person_id=meal.person_id,
                log_id=log_id,
            )
        elif action.startswith(ACTION_DELETE_PREFIX) or action.startswith(
            ACTION_UNDO_PREFIX
        ):
            prefix = (
                ACTION_DELETE_PREFIX
                if action.startswith(ACTION_DELETE_PREFIX)
                else ACTION_UNDO_PREFIX
            )
            log_id = action[len(prefix) :]
            meal = await runtime.meal_repo.get_meal_log(log_id)
            if meal is None:
                return
            deleted = await runtime.meal_repo.soft_delete_meal(meal.person_id, log_id)
            if deleted:
                await runtime.async_notify_person_updated(
                    meal.person_id, "notify_delete_meal"
                )
                log_event(
                    _LOGGER,
                    logging.INFO,
                    "notify_meal_deleted",
                    person_id=meal.person_id,
                    log_id=log_id,
                )

    return hass.bus.async_listen("mobile_app_notification_action", _handle_action)
