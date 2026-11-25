"""Authenticated WebSocket API endpoints powering the ha-calories-buddy Custom Card."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import probatio as vol
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.components.websocket_api.decorators import (
    async_response,
    websocket_command,
)
from homeassistant.core import HomeAssistant

from custom_components.ha_calories_buddy.const import DOMAIN
from custom_components.ha_calories_buddy.core.models import (
    FoodItem,
    FoodSource,
    LabelStandard,
    Macros,
    PersonFoodOverride,
    PersonProfile,
    PreparationState,
)
from custom_components.ha_calories_buddy.core.nutrition.atwater import check_atwater
from custom_components.ha_calories_buddy.core.nutrition.yield_factors import (
    default_yield_for_category,
)
from custom_components.ha_calories_buddy.core.storage.seeder import normalize_text
from custom_components.ha_calories_buddy.identity import (
    PersonAmbiguousError,
    PersonNotFoundError,
    resolve_person,
)
from custom_components.ha_calories_buddy.runtime import DomainRuntime


def _resolve_ws_person(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> PersonProfile | None:
    try:
        return resolve_person(
            hass,
            explicit_person=msg.get("person_id"),
            context_user_id=connection.user.id if connection.user else None,
        )
    except (PersonNotFoundError, PersonAmbiguousError) as exc:
        connection.send_error(msg["id"], "person_resolve_failed", str(exc))
        return None


def _safe_person_payload(
    runtime: DomainRuntime, profile: PersonProfile
) -> dict[str, Any]:
    """Serialize PersonProfile without ever exposing secret API keys."""
    return {
        "person_id": profile.person_id,
        "display_name": profile.display_name,
        "ha_user_id": profile.ha_user_id,
        "ha_person_entity": profile.ha_person_entity,
        "language": profile.language,
        "goal_type": profile.goal_type.value,
        "target_mode": profile.target_mode.value,
        "tdee_engine": profile.tdee_engine.value,
        "llm_enabled": runtime.is_llm_enabled(profile),
        "meal_slots": [s.model_dump(mode="json") for s in profile.meal_slots],
    }


def async_register_websocket_api(hass: HomeAssistant) -> None:
    """Register all ha_calories_buddy/* WebSocket commands."""

    @websocket_command({"type": f"{DOMAIN}/list_persons"})
    @async_response
    async def ws_list_persons(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime | None = hass.data.get(DOMAIN)
        persons = (
            [
                _safe_person_payload(runtime, prt.profile)
                for prt in runtime.persons.values()
            ]
            if runtime
            else []
        )
        connection.send_result(msg["id"], {"persons": persons})

    @websocket_command(
        {
            "type": f"{DOMAIN}/dashboard",
            vol.Optional("person_id"): str,
            vol.Optional("date"): str,
        }
    )
    @async_response
    async def ws_dashboard(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        person_rt = runtime.persons.get(profile.person_id)
        if person_rt is None:
            connection.send_error(msg["id"], "not_loaded", "Person not active")
            return

        if person_rt.coordinator.data is None:
            await person_rt.coordinator.async_refresh()
        state = person_rt.coordinator.data
        if state is None:
            connection.send_error(msg["id"], "no_data", "Dashboard state unavailable")
            return

        req_date = date.fromisoformat(msg["date"]) if msg.get("date") else state.today
        if req_date == state.today:
            totals = state.totals
            meals = state.meals
        else:
            totals = await runtime.meal_repo.get_daily_totals(
                profile.person_id, req_date
            )
            meals = await runtime.meal_repo.list_meals_for_date(
                profile.person_id, req_date
            )

        connection.send_result(
            msg["id"],
            {
                "person": _safe_person_payload(runtime, profile),
                "llm_enabled": runtime.is_llm_enabled(profile),
                "date": req_date.isoformat(),
                "tdee": state.tdee.model_dump(mode="json"),
                "targets": state.targets.model_dump(mode="json"),
                "totals": totals.model_dump(mode="json"),
                "meals": [m.model_dump(mode="json") for m in meals],
                "latest_weight_kg": state.latest_weight_kg,
            },
        )

    @websocket_command(
        {
            "type": f"{DOMAIN}/log_text",
            vol.Required("text"): str,
            vol.Optional("person_id"): str,
            vol.Optional("date"): str,
            vol.Optional("meal_slot"): str,
        }
    )
    @async_response
    async def ws_log_text(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        try:
            res = await runtime.logging_service.log_from_text(
                profile,
                msg["text"],
                log_date=date.fromisoformat(msg["date"]) if msg.get("date") else None,
                meal_slot=msg.get("meal_slot"),
                source_channel="card_ai",
            )
        except Exception as exc:  # noqa: BLE001
            connection.send_error(msg["id"], "log_text_failed", str(exc))
            return
        person_rt = runtime.persons.get(profile.person_id)
        if person_rt is not None:
            await person_rt.coordinator.async_request_refresh()
        connection.send_result(msg["id"], res.model_dump(mode="json"))

    @websocket_command(
        {
            "type": f"{DOMAIN}/log_item",
            vol.Optional("person_id"): str,
            vol.Optional("food_id"): str,
            vol.Optional("barcode"): str,
            vol.Optional("name"): str,
            vol.Required("quantity"): vol.Coerce(float),
            vol.Optional("unit", default="g"): str,
            vol.Optional("state", default="unspecified"): str,
            vol.Optional("meal_slot", default="Snack"): str,
            vol.Optional("date"): str,
        }
    )
    @async_response
    async def ws_log_item(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        res = await runtime.logging_service.log_manual_item(
            profile,
            food_id=msg.get("food_id"),
            barcode=msg.get("barcode"),
            name=str(msg.get("name") or ""),
            quantity=float(msg["quantity"]),
            unit=str(msg.get("unit", "g")),
            state=PreparationState(msg.get("state", "unspecified")),
            meal_slot=str(msg.get("meal_slot", "Snack")),
            log_date=date.fromisoformat(msg["date"]) if msg.get("date") else None,
            source_channel="card_manual",
        )
        person_rt = runtime.persons.get(profile.person_id)
        if person_rt is not None:
            await person_rt.coordinator.async_request_refresh()
        connection.send_result(msg["id"], res.model_dump(mode="json"))

    @websocket_command(
        {
            "type": f"{DOMAIN}/confirm_meal",
            vol.Required("log_id"): str,
            vol.Optional("person_id"): str,
            vol.Optional("promote_to_vault", default=False): bool,
        }
    )
    @async_response
    async def ws_confirm_meal(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        meal = await runtime.logging_service.confirm_pending_meal(
            profile,
            msg["log_id"],
            promote_to_vault=bool(msg.get("promote_to_vault", False)),
        )
        if meal is None:
            connection.send_error(msg["id"], "not_found", "Meal log not found")
            return
        person_rt = runtime.persons.get(profile.person_id)
        if person_rt is not None:
            await person_rt.coordinator.async_request_refresh()
        connection.send_result(msg["id"], {"meal_log": meal.model_dump(mode="json")})

    @websocket_command(
        {
            "type": f"{DOMAIN}/update_meal_item",
            vol.Required("log_id"): str,
            vol.Required("item_id"): str,
            vol.Required("quantity"): vol.Coerce(float),
            vol.Optional("unit"): str,
            vol.Optional("state"): str,
            vol.Optional("person_id"): str,
        }
    )
    @async_response
    async def ws_update_meal_item(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        state = PreparationState(msg["state"]) if msg.get("state") else None
        meal = await runtime.logging_service.update_item_measurement(
            profile,
            msg["log_id"],
            msg["item_id"],
            quantity=float(msg["quantity"]),
            unit=msg.get("unit"),
            state=state,
        )
        if meal is None:
            connection.send_error(msg["id"], "not_found", "Meal item not found")
            return
        person_rt = runtime.persons.get(profile.person_id)
        if person_rt is not None:
            await person_rt.coordinator.async_request_refresh()
        connection.send_result(msg["id"], {"meal_log": meal.model_dump(mode="json")})

    @websocket_command(
        {
            "type": f"{DOMAIN}/delete_meal",
            vol.Required("log_id"): str,
            vol.Optional("person_id"): str,
        }
    )
    @async_response
    async def ws_delete_meal(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        deleted = await runtime.meal_repo.soft_delete_meal(
            profile.person_id, msg["log_id"]
        )
        person_rt = runtime.persons.get(profile.person_id)
        if person_rt is not None:
            await person_rt.coordinator.async_request_refresh()
        connection.send_result(msg["id"], {"deleted": deleted})

    @websocket_command(
        {
            "type": f"{DOMAIN}/restore_meal",
            vol.Required("log_id"): str,
            vol.Optional("person_id"): str,
        }
    )
    @async_response
    async def ws_restore_meal(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        restored = await runtime.meal_repo.restore_meal(
            profile.person_id, msg["log_id"]
        )
        person_rt = runtime.persons.get(profile.person_id)
        if person_rt is not None:
            await person_rt.coordinator.async_request_refresh()
        connection.send_result(msg["id"], {"restored": restored})

    @websocket_command(
        {
            "type": f"{DOMAIN}/search_foods",
            vol.Optional("person_id"): str,
            vol.Optional("query", default=""): str,
            vol.Optional("include_external", default=True): bool,
        }
    )
    @async_response
    async def ws_search_foods(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        foods = await runtime.resolver.search_foods(
            query=str(msg.get("query", "")),
            person_id=profile.person_id,
            language=profile.language,
            include_external=bool(msg.get("include_external", True)),
            limit=20,
        )
        connection.send_result(
            msg["id"], {"foods": [f.model_dump(mode="json") for f in foods]}
        )

    @websocket_command(
        {
            "type": f"{DOMAIN}/lookup_barcode",
            vol.Required("barcode"): str,
            vol.Optional("person_id"): str,
        }
    )
    @async_response
    async def ws_lookup_barcode(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        food = await runtime.resolver.lookup_barcode(
            msg["barcode"], person_id=profile.person_id
        )
        connection.send_result(
            msg["id"], {"food": food.model_dump(mode="json") if food else None}
        )

    @websocket_command(
        {
            "type": f"{DOMAIN}/save_food",
            vol.Optional("person_id"): str,
            vol.Optional("food_id"): vol.Any(str, None),
            vol.Required("name"): str,
            vol.Optional("brand"): vol.Any(str, None),
            vol.Optional("barcode_ean"): vol.Any(str, None),
            vol.Optional("category", default="other"): str,
            vol.Optional("preparation_state", default="ready_to_eat"): str,
            vol.Required("kcal_per_100g"): vol.Coerce(float),
            vol.Required("protein_g_per_100g"): vol.Coerce(float),
            vol.Required("carbs_g_per_100g"): vol.Coerce(float),
            vol.Required("fat_g_per_100g"): vol.Coerce(float),
            vol.Optional("fiber_g_per_100g"): vol.Any(vol.Coerce(float), None),
            vol.Optional("yield_factor", default=1.0): vol.Coerce(float),
            vol.Optional("verified", default=True): bool,
            vol.Optional("scope", default="global"): str,
            vol.Optional("preferred_alias"): vol.Any(str, None),
            vol.Optional("custom_portions"): dict,
        }
    )
    @async_response
    async def ws_save_food(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return

        scope = str(msg.get("scope", "global"))
        food_id = msg.get("food_id")

        if scope == "person" and food_id:
            override = PersonFoodOverride(
                override_id=f"ovr_{uuid.uuid4().hex[:10]}",
                person_id=profile.person_id,
                food_id=str(food_id),
                preferred_alias=msg.get("preferred_alias"),
                kcal_per_100g=float(msg["kcal_per_100g"]),
                protein_g_per_100g=float(msg["protein_g_per_100g"]),
                carbs_g_per_100g=float(msg["carbs_g_per_100g"]),
                fat_g_per_100g=float(msg["fat_g_per_100g"]),
                fiber_g_per_100g=(
                    float(msg["fiber_g_per_100g"])
                    if msg.get("fiber_g_per_100g") is not None
                    else None
                ),
                yield_factor=float(msg.get("yield_factor", 1.0)),
                custom_portions={
                    str(k).lower(): float(v)
                    for k, v in (msg.get("custom_portions") or {}).items()
                },
            )
            await runtime.food_repo.upsert_person_override(override)
            eff = await runtime.food_repo.get_effective_food(
                str(food_id), person_id=profile.person_id
            )
            connection.send_result(
                msg["id"], {"food": eff.model_dump(mode="json") if eff else None}
            )
            return

        macros = Macros(
            kcal=float(msg["kcal_per_100g"]),
            protein_g=float(msg["protein_g_per_100g"]),
            carbs_g=float(msg["carbs_g_per_100g"]),
            fat_g=float(msg["fat_g_per_100g"]),
            fiber_g=(
                float(msg["fiber_g_per_100g"])
                if msg.get("fiber_g_per_100g") is not None
                else None
            ),
        )
        atwater = check_atwater(macros, LabelStandard.EU)
        category = str(msg.get("category", "other"))
        yield_f = float(msg.get("yield_factor") or default_yield_for_category(category))
        target_fid = str(food_id) if food_id else f"custom_{uuid.uuid4().hex[:12]}"
        food = FoodItem(
            food_id=target_fid,
            name=str(msg["name"]).strip(),
            normalized_name=normalize_text(str(msg["name"])),
            aliases=[normalize_text(str(msg["name"]))],
            brand=msg.get("brand"),
            barcode_ean=msg.get("barcode_ean"),
            category=category,
            preparation_state=PreparationState(
                msg.get("preparation_state", "ready_to_eat")
            ),
            macros_per_100g=macros,
            label_standard=LabelStandard.EU,
            yield_factor=yield_f,
            source=FoodSource.MANUAL,
            verified=bool(msg.get("verified", True)),
            atwater_warning=atwater.warning and not bool(msg.get("verified", True)),
        )
        saved = await runtime.food_repo.upsert_global_food(food)
        eff = await runtime.food_repo.get_effective_food(
            saved.food_id, person_id=profile.person_id
        )
        connection.send_result(
            msg["id"], {"food": eff.model_dump(mode="json") if eff else None}
        )

    @websocket_command(
        {
            "type": f"{DOMAIN}/log_weight",
            vol.Required("weight_kg"): vol.Coerce(float),
            vol.Optional("person_id"): str,
        }
    )
    @async_response
    async def ws_log_weight(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        person_rt = runtime.persons.get(profile.person_id)
        if person_rt is None:
            connection.send_error(msg["id"], "not_loaded", "Person not active")
            return
        snap = await person_rt.coordinator.async_persist_today_snapshot(
            override_weight_kg=float(msg["weight_kg"])
        )
        await person_rt.coordinator.async_request_refresh()
        connection.send_result(msg["id"], {"snapshot": snap.model_dump(mode="json")})

    for cmd in (
        ws_list_persons,
        ws_dashboard,
        ws_log_text,
        ws_log_item,
        ws_confirm_meal,
        ws_update_meal_item,
        ws_delete_meal,
        ws_restore_meal,
        ws_search_foods,
        ws_lookup_barcode,
        ws_save_food,
        ws_log_weight,
    ):
        websocket_api.async_register_command(hass, cmd)
