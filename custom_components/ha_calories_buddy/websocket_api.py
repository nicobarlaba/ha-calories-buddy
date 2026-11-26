"""Authenticated WebSocket API endpoints powering the ha-calories-buddy Custom Card."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

import probatio as vol
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.components.websocket_api.decorators import (
    async_response,
    websocket_command,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.ha_calories_buddy.const import DOMAIN
from custom_components.ha_calories_buddy.core.models import (
    FoodItem,
    FoodSource,
    LabelStandard,
    Macros,
    MealFeedbackRating,
    MealPlan,
    MealStatus,
    MealSuggestion,
    PersonFoodOverride,
    PersonProfile,
    PlanGenerationOptions,
    PlanItemStatus,
    PreferenceScope,
    PreferenceStance,
    PreparationState,
    ShoppingDraft,
    ShoppingDraftItem,
    TrainingDayOverride,
    WorkoutActivityType,
    WorkoutScheduleItem,
)
from custom_components.ha_calories_buddy.core.nutrition.atwater import check_atwater
from custom_components.ha_calories_buddy.core.nutrition.recipes import (
    parse_recipe_ingredient_inputs,
)
from custom_components.ha_calories_buddy.core.nutrition.yield_factors import (
    default_yield_for_category,
)
from custom_components.ha_calories_buddy.core.physiology.trends import (
    build_trends_summary,
)
from custom_components.ha_calories_buddy.core.planner.macro_scaler import (
    compute_smart_slot_budget,
)
from custom_components.ha_calories_buddy.core.planner.shopping import (
    aisle_for_category,
)
from custom_components.ha_calories_buddy.core.planner.slot_resolver import (
    default_slots_for_language,
    extract_logged_slot_keys,
    resolve_meal_slot,
)
from custom_components.ha_calories_buddy.core.storage.seeder import normalize_text
from custom_components.ha_calories_buddy.identity import (
    PersonAmbiguousError,
    PersonNotFoundError,
    resolve_person,
)
from custom_components.ha_calories_buddy.runtime import DomainRuntime
from custom_components.ha_calories_buddy.shopping_bridge import (
    async_commit_draft_to_todo,
)


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
        "target_weight_kg": profile.target_weight_kg,
        "target_body_fat_pct": profile.target_body_fat_pct,
        "has_manual_training_targets": bool(
            profile.manual_targets is not None
            and profile.manual_targets.training_kcal is not None
        ),
        "todo_entity": profile.entity_mappings.todo_entity,
        "workout_calendar_entity": profile.entity_mappings.workout_calendar_entity,
        "activities_entity": profile.entity_mappings.activities_entity,
        "llm_enabled": runtime.is_llm_enabled(profile),
        "meal_slots": [s.model_dump(mode="json") for s in profile.meal_slots],
    }


async def _serialize_plan_with_coverage(
    runtime: DomainRuntime,
    person_id: str,
    plan: MealPlan | None,
) -> dict[str, Any] | None:
    """Serialize a MealPlan annotating PLANNED items superseded by non-deleted logs."""
    if plan is None:
        return None
    range_meals = await runtime.meal_repo.list_meals_for_range(
        person_id, plan.start_date, plan.end_date
    )
    covered_by_date: dict[date, set[str]] = {}
    for meal in range_meals:
        if meal.status != MealStatus.DELETED:
            slot_key = normalize_text(meal.meal_slot)
            if slot_key:
                covered_by_date.setdefault(meal.log_date, set()).add(slot_key)

    payload = plan.model_dump(mode="json")
    for day_dict in payload.get("days", []):
        day_d = date.fromisoformat(day_dict["plan_date"])
        day_slots = covered_by_date.get(day_d, set())
        for item_dict in day_dict.get("items", []):
            item_dict["covered_by_log"] = (
                item_dict.get("status") == PlanItemStatus.PLANNED.value
                and normalize_text(item_dict.get("meal_slot", "")) in day_slots
            )
    return payload


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
        # Always read fresh SQLite totals/meals/plan so external Assist or service edits appear immediately
        totals = await runtime.meal_repo.get_daily_totals(profile.person_id, req_date)
        meals = await runtime.meal_repo.list_meals_for_date(profile.person_id, req_date)
        planned_pairs = await runtime.plan_repo.get_plan_items_for_range(
            profile.person_id, req_date, req_date
        )
        planned_today = [item for _, item in planned_pairs]
        logged_slots = extract_logged_slot_keys(meals)
        planned_payload = []
        for p in planned_today:
            item_dict = p.model_dump(mode="json")
            item_dict["covered_by_log"] = (
                p.status == PlanItemStatus.PLANNED
                and normalize_text(p.meal_slot) in logged_slots
            )
            planned_payload.append(item_dict)

        lean_mass_kg = (
            round(state.weight_kg * (1.0 - state.body_fat_pct / 100.0), 1)
            if state.body_fat_pct is not None and state.weight_kg > 0
            else None
        )
        fat_mass_kg = (
            round(state.weight_kg * (state.body_fat_pct / 100.0), 1)
            if state.body_fat_pct is not None and state.weight_kg > 0
            else None
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
                "planned_today": planned_payload,
                "latest_weight_kg": state.latest_weight_kg,
                "workouts": {
                    "is_training_day": state.is_training_day,
                    "source": state.training_day_source,
                    "today_scheduled": [
                        w.model_dump(mode="json") for w in state.today_workouts
                    ],
                    "today_calendar": [
                        w.model_dump(mode="json")
                        for w in state.calendar_workouts_by_date.get(state.today, [])
                    ],
                },
                "biometrics": {
                    "weight_kg": state.weight_kg,
                    "weight_ema_kg": state.weight_ema_kg,
                    "weight_source": state.weight_source,
                    "weight_entity_id": state.weight_entity_id,
                    "weight_entity_name": state.weight_entity_name,
                    "weight_sensor_available": state.weight_sensor_available,
                    "body_fat_pct": state.body_fat_pct,
                    "body_fat_ema_pct": state.body_fat_ema_pct,
                    "body_fat_source": state.body_fat_source,
                    "body_fat_entity_id": state.body_fat_entity_id,
                    "body_fat_entity_name": state.body_fat_entity_name,
                    "body_fat_sensor_available": state.body_fat_sensor_available,
                    "lean_mass_kg": lean_mass_kg,
                    "fat_mass_kg": fat_mass_kg,
                },
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
        local_now = dt_util.now()
        try:
            res = await runtime.logging_service.log_from_text(
                profile,
                msg["text"],
                log_date=(
                    date.fromisoformat(msg["date"])
                    if msg.get("date")
                    else local_now.date()
                ),
                meal_slot=msg.get("meal_slot"),
                source_channel="card_ai",
                current_local_time=local_now.strftime("%H:%M"),
            )
        except Exception as exc:  # noqa: BLE001
            connection.send_error(msg["id"], "log_text_failed", str(exc))
            return
        await runtime.async_notify_person_updated(profile.person_id, "ws_log_text")
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
            vol.Optional("meal_slot"): vol.Any(str, None),
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
            meal_slot=msg.get("meal_slot"),
            log_date=(
                date.fromisoformat(msg["date"])
                if msg.get("date")
                else dt_util.now().date()
            ),
            source_channel="card_manual",
        )
        await runtime.async_notify_person_updated(profile.person_id, "ws_log_item")
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
        await runtime.async_notify_person_updated(profile.person_id, "ws_confirm_meal")
        connection.send_result(msg["id"], {"meal_log": meal.model_dump(mode="json")})

    @websocket_command(
        {
            "type": f"{DOMAIN}/add_meal_item",
            vol.Required("log_id"): str,
            vol.Optional("food_id"): vol.Any(str, None),
            vol.Optional("barcode"): vol.Any(str, None),
            vol.Optional("name"): vol.Any(str, None),
            vol.Optional("brand"): vol.Any(str, None),
            vol.Required("quantity"): vol.Coerce(float),
            vol.Optional("unit", default="g"): str,
            vol.Optional("state", default="unspecified"): str,
            vol.Optional("person_id"): str,
        }
    )
    @async_response
    async def ws_add_meal_item(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        state_val = str(msg.get("state") or PreparationState.UNSPECIFIED.value)
        state = (
            PreparationState(state_val)
            if state_val in ("raw", "cooked", "ready_to_eat", "unspecified")
            else PreparationState.UNSPECIFIED
        )
        meal = await runtime.logging_service.add_item_to_meal(
            profile,
            str(msg["log_id"]),
            food_id=msg.get("food_id") or None,
            barcode=msg.get("barcode") or None,
            name=str(msg.get("name") or ""),
            brand=msg.get("brand") or None,
            quantity=float(msg["quantity"]),
            unit=str(msg.get("unit", "g")),
            state=state,
        )
        if meal is None:
            connection.send_error(msg["id"], "not_found", "Meal log not found")
            return
        await runtime.async_notify_person_updated(
            profile.person_id, "ws_add_meal_item"
        )
        connection.send_result(msg["id"], {"meal_log": meal.model_dump(mode="json")})

    @websocket_command(
        {
            "type": f"{DOMAIN}/update_meal_item",
            vol.Required("log_id"): str,
            vol.Required("item_id"): str,
            vol.Required("quantity"): vol.Coerce(float),
            vol.Optional("unit"): str,
            vol.Optional("state"): str,
            vol.Optional("food_id"): vol.Any(str, None),
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
            food_id=msg.get("food_id") or None,
        )
        if meal is None:
            connection.send_error(msg["id"], "not_found", "Meal item not found")
            return
        await runtime.async_notify_person_updated(
            profile.person_id, "ws_update_meal_item"
        )
        connection.send_result(msg["id"], {"meal_log": meal.model_dump(mode="json")})

    @websocket_command(
        {
            "type": f"{DOMAIN}/delete_meal_item",
            vol.Required("log_id"): str,
            vol.Required("item_id"): str,
            vol.Optional("person_id"): str,
        }
    )
    @async_response
    async def ws_delete_meal_item(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        meal = await runtime.logging_service.remove_meal_item(
            profile, msg["log_id"], msg["item_id"]
        )
        if meal is None:
            connection.send_error(msg["id"], "not_found", "Meal item not found")
            return
        await runtime.async_notify_person_updated(
            profile.person_id, "ws_delete_meal_item"
        )
        connection.send_result(
            msg["id"],
            {
                "meal_log": meal.model_dump(mode="json"),
                "meal_deleted": meal.status == MealStatus.DELETED,
            },
        )

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
        await runtime.async_notify_person_updated(profile.person_id, "ws_delete_meal")
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
        await runtime.async_notify_person_updated(profile.person_id, "ws_restore_meal")
        connection.send_result(msg["id"], {"restored": restored})

    @websocket_command(
        {
            "type": f"{DOMAIN}/search_foods",
            vol.Optional("person_id"): str,
            vol.Optional("query", default=""): str,
            vol.Optional("category"): vol.Any(str, None),
            vol.Optional("limit", default=20): vol.Coerce(int),
            vol.Optional("offset", default=0): vol.Coerce(int),
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
        safe_limit = max(1, min(100, int(msg.get("limit", 20))))
        safe_offset = max(0, int(msg.get("offset", 0)))
        foods, total_count = await runtime.resolver.search_foods_paginated(
            query=str(msg.get("query", "")),
            person_id=profile.person_id,
            category=msg.get("category"),
            language=profile.language,
            include_external=bool(msg.get("include_external", True)),
            limit=safe_limit,
            offset=safe_offset,
        )
        connection.send_result(
            msg["id"],
            {
                "foods": [f.model_dump(mode="json") for f in foods],
                "total_count": total_count,
                "offset": safe_offset,
                "limit": safe_limit,
                "has_more": (safe_offset + len(foods)) < total_count,
            },
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
        await runtime.async_notify_person_updated(profile.person_id, "ws_log_weight")
        connection.send_result(msg["id"], {"snapshot": snap.model_dump(mode="json")})

    @websocket_command(
        {
            "type": f"{DOMAIN}/update_meal_slot",
            vol.Required("log_id"): str,
            vol.Required("meal_slot"): str,
            vol.Optional("person_id"): str,
        }
    )
    @async_response
    async def ws_update_meal_slot(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        meal = await runtime.logging_service.change_meal_slot(
            profile, msg["log_id"], str(msg["meal_slot"])
        )
        if meal is None:
            connection.send_error(msg["id"], "not_found", "Meal log not found")
            return
        await runtime.async_notify_person_updated(
            profile.person_id, "ws_update_meal_slot"
        )
        connection.send_result(msg["id"], {"meal_log": meal.model_dump(mode="json")})

    @websocket_command(
        {
            "type": f"{DOMAIN}/get_plan",
            vol.Optional("person_id"): str,
            vol.Optional("reference_date"): str,
        }
    )
    @async_response
    async def ws_get_plan(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        ref_d = (
            date.fromisoformat(msg["reference_date"])
            if msg.get("reference_date")
            else dt_util.now().date()
        )
        plan = await runtime.plan_repo.get_active_plan(
            profile.person_id, reference_date=ref_d
        )
        plan_payload = await _serialize_plan_with_coverage(
            runtime, profile.person_id, plan
        )
        connection.send_result(
            msg["id"],
            {"plan": plan_payload},
        )

    @websocket_command(
        {
            "type": f"{DOMAIN}/generate_plan",
            vol.Optional("person_id"): str,
            vol.Optional("start_date"): str,
            vol.Optional("num_days", default=7): vol.Coerce(int),
            vol.Optional("meal_prep_mode", default=False): bool,
            vol.Optional("notes", default=""): str,
        }
    )
    @async_response
    async def ws_generate_plan(
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
        data = person_rt.coordinator.data
        if data is None:
            connection.send_error(msg["id"], "no_data", "Targets unavailable")
            return

        start_d = (
            date.fromisoformat(msg["start_date"])
            if msg.get("start_date")
            else data.today
        )
        opts = PlanGenerationOptions(
            num_days=max(1, min(7, int(msg.get("num_days", 7)))),
            meal_prep_mode=bool(msg.get("meal_prep_mode", False)),
            notes=str(msg.get("notes") or ""),
        )
        workout_ctx = await runtime.workout_repo.build_workout_prompt_context(
            profile.person_id,
            start_d,
            opts.num_days,
            language=profile.language,
            calendar_workouts_by_date=data.calendar_workouts_by_date,
        )
        try:
            plan = await runtime.planner_service.generate_meal_plan(
                profile,
                start_date=start_d,
                daily_targets=data.targets,
                options=opts,
                reference_today=data.today,
                workout_context=workout_ctx,
            )
        except Exception as exc:  # noqa: BLE001
            connection.send_error(msg["id"], "generate_plan_failed", str(exc))
            return
        await runtime.async_notify_person_updated(
            profile.person_id, "ws_generate_plan"
        )
        plan_payload = await _serialize_plan_with_coverage(
            runtime, profile.person_id, plan
        )
        connection.send_result(msg["id"], {"plan": plan_payload})

    @websocket_command(
        {
            "type": f"{DOMAIN}/suggest_meal",
            vol.Optional("person_id"): str,
            vol.Optional("meal_slot"): str,
            vol.Optional("hint", default=""): str,
            vol.Optional("max_cook_minutes"): vol.Coerce(int),
            vol.Optional("count", default=3): vol.Coerce(int),
            vol.Optional("budget_mode", default="auto"): str,
        }
    )
    @async_response
    async def ws_suggest_meal(
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
        data = person_rt.coordinator.data
        if data is None:
            connection.send_error(msg["id"], "no_data", "Targets unavailable")
            return

        slots = profile.meal_slots or default_slots_for_language(profile.language)
        slot_name = resolve_meal_slot(
            msg.get("meal_slot"), slots, language=profile.language
        )
        today = dt_util.now().date()
        totals = await runtime.meal_repo.get_daily_totals(profile.person_id, today)
        slot_macro, mode_used = compute_smart_slot_budget(
            data.targets,
            slots,
            slot_name,
            totals.consumed,
            set(totals.by_slot.keys()),
            budget_mode=str(msg.get("budget_mode") or "auto"),
        )
        workout_ctx = await runtime.workout_repo.build_workout_prompt_context(
            profile.person_id,
            today,
            1,
            language=profile.language,
            calendar_workouts_by_date=data.calendar_workouts_by_date,
        )
        try:
            suggestions = await runtime.planner_service.suggest_meal(
                profile,
                meal_slot=slot_name,
                target_macros=slot_macro,
                user_hint=str(msg.get("hint") or ""),
                max_cook_minutes=msg.get("max_cook_minutes"),
                count=max(1, min(3, int(msg.get("count", 3)))),
                reference_date=today,
                budget_mode=mode_used,
                workout_context=workout_ctx,
            )
        except Exception as exc:  # noqa: BLE001
            connection.send_error(msg["id"], "suggest_meal_failed", str(exc))
            return
        connection.send_result(
            msg["id"],
            {
                "suggestions": [s.model_dump(mode="json") for s in suggestions],
                "target_macros": slot_macro.model_dump(mode="json"),
                "budget_mode_used": mode_used,
                "meal_slot": slot_name,
            },
        )

    @websocket_command(
        {
            "type": f"{DOMAIN}/log_suggestion",
            vol.Required("suggestion"): dict,
            vol.Optional("scale", default=1.0): vol.Coerce(float),
            vol.Optional("date"): str,
            vol.Optional("person_id"): str,
        }
    )
    @async_response
    async def ws_log_suggestion(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        try:
            suggestion = MealSuggestion.model_validate(msg["suggestion"])
            target_date = (
                date.fromisoformat(msg["date"])
                if msg.get("date")
                else dt_util.now().date()
            )
            meal_log = await runtime.planner_service.log_suggestion_as_meal(
                profile,
                suggestion,
                log_date=target_date,
                actual_scale=float(msg.get("scale", 1.0)),
            )
        except Exception as exc:  # noqa: BLE001
            connection.send_error(msg["id"], "log_suggestion_failed", str(exc))
            return
        await runtime.async_notify_person_updated(
            profile.person_id, "ws_log_suggestion"
        )
        connection.send_result(
            msg["id"], {"meal_log": meal_log.model_dump(mode="json")}
        )

    @websocket_command(
        {
            "type": f"{DOMAIN}/swap_plan_meal",
            vol.Required("plan_item_id"): str,
            vol.Optional("instruction", default=""): str,
            vol.Optional("person_id"): str,
        }
    )
    @async_response
    async def ws_swap_plan_meal(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        try:
            item = await runtime.planner_service.swap_plan_meal(
                profile,
                str(msg["plan_item_id"]),
                user_instruction=str(msg.get("instruction") or ""),
            )
        except Exception as exc:  # noqa: BLE001
            connection.send_error(msg["id"], "swap_failed", str(exc))
            return
        await runtime.async_notify_person_updated(
            profile.person_id, "ws_swap_plan_meal"
        )
        connection.send_result(msg["id"], {"plan_item": item.model_dump(mode="json")})

    @websocket_command(
        {
            "type": f"{DOMAIN}/promote_plan_meal",
            vol.Required("plan_item_id"): str,
            vol.Optional("scale", default=1.0): vol.Coerce(float),
            vol.Optional("person_id"): str,
        }
    )
    @async_response
    async def ws_promote_plan_meal(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        try:
            meal_log = await runtime.planner_service.promote_plan_meal(
                profile,
                str(msg["plan_item_id"]),
                actual_scale=float(msg.get("scale", 1.0)),
            )
        except Exception as exc:  # noqa: BLE001
            connection.send_error(msg["id"], "promote_failed", str(exc))
            return
        await runtime.async_notify_person_updated(
            profile.person_id, "ws_promote_plan_meal"
        )
        connection.send_result(
            msg["id"], {"meal_log": meal_log.model_dump(mode="json")}
        )

    @websocket_command(
        {
            "type": f"{DOMAIN}/rebalance_today",
            vol.Optional("person_id"): str,
            vol.Optional("date"): str,
            vol.Optional("spread_days"): vol.Coerce(int),
            vol.Optional("allow_llm_swap", default=True): bool,
        }
    )
    @async_response
    async def ws_rebalance_today(
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
        data = person_rt.coordinator.data
        if data is None:
            connection.send_error(msg["id"], "no_data", "Targets unavailable")
            return

        target_d = (
            date.fromisoformat(msg["date"]) if msg.get("date") else data.today
        )
        try:
            if msg.get("spread_days") is not None:
                totals = await runtime.meal_repo.get_daily_totals(
                    profile.person_id, target_d
                )
                excess = totals.consumed.kcal - data.targets.target_kcal
                res = await runtime.rebalancer_service.rebalance_multiday(
                    profile,
                    start_date=target_d,
                    excess_kcal=excess,
                    bmr_kcal=data.tdee.bmr_kcal,
                    weight_kg=data.weight_kg,
                    spread_days=int(msg["spread_days"]),
                )
            else:
                res = await runtime.rebalancer_service.rebalance_intraday(
                    profile,
                    target_date=target_d,
                    daily_targets=data.targets,
                    allow_llm_swap=bool(msg.get("allow_llm_swap", True)),
                )
        except Exception as exc:  # noqa: BLE001
            connection.send_error(msg["id"], "rebalance_failed", str(exc))
            return
        await runtime.async_notify_person_updated(
            profile.person_id, "ws_rebalance_today"
        )
        connection.send_result(msg["id"], {"rebalance": res.model_dump(mode="json")})

    @websocket_command(
        {
            "type": f"{DOMAIN}/get_preferences",
            vol.Optional("person_id"): str,
        }
    )
    @async_response
    async def ws_get_preferences(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        ctx = await runtime.preference_engine.build_context(profile)
        all_entries = await runtime.preference_repo.list_preferences(
            profile.person_id, include_global=True
        )
        feedbacks = await runtime.preference_repo.list_feedback(profile.person_id)
        connection.send_result(
            msg["id"],
            {
                "context": ctx.model_dump(mode="json"),
                "entries": [e.model_dump(mode="json") for e in all_entries],
                "feedback": [f.model_dump(mode="json") for f in feedbacks],
            },
        )

    @websocket_command(
        {
            "type": f"{DOMAIN}/upsert_preference",
            vol.Optional("person_id"): str,
            vol.Required("scope"): str,
            vol.Required("target_type"): str,
            vol.Required("target_value"): str,
            vol.Required("stance"): str,
            vol.Optional("notes", default=""): str,
        }
    )
    @async_response
    async def ws_upsert_preference(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        scope = PreferenceScope(str(msg["scope"]))
        pref = await runtime.preference_repo.upsert_preference(
            scope=scope,
            person_id=profile.person_id if scope == PreferenceScope.PERSON else None,
            target_type=str(msg["target_type"]),
            target_value=str(msg["target_value"]),
            stance=PreferenceStance(str(msg["stance"])),
            notes=str(msg.get("notes") or ""),
        )
        connection.send_result(msg["id"], {"preference": pref.model_dump(mode="json")})

    @websocket_command(
        {
            "type": f"{DOMAIN}/delete_preference",
            vol.Required("pref_id"): str,
            vol.Optional("person_id"): str,
        }
    )
    @async_response
    async def ws_delete_preference(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        deleted = await runtime.preference_repo.delete_preference(
            str(msg["pref_id"]), person_id=profile.person_id
        )
        connection.send_result(msg["id"], {"deleted": deleted})

    @websocket_command(
        {
            "type": f"{DOMAIN}/set_meal_feedback",
            vol.Required("title"): str,
            vol.Required("rating"): str,
            vol.Optional("notes", default=""): str,
            vol.Optional("person_id"): str,
        }
    )
    @async_response
    async def ws_set_meal_feedback(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        fb = await runtime.preference_repo.upsert_feedback(
            person_id=profile.person_id,
            title=str(msg["title"]),
            rating=MealFeedbackRating(str(msg["rating"])),
            notes=str(msg.get("notes") or ""),
        )
        connection.send_result(msg["id"], {"feedback": fb.model_dump(mode="json")})

    @websocket_command(
        {
            "type": f"{DOMAIN}/list_recipes",
            vol.Optional("person_id"): str,
            vol.Optional("query", default=""): str,
            vol.Optional("limit", default=50): vol.Coerce(int),
            vol.Optional("offset", default=0): vol.Coerce(int),
        }
    )
    @async_response
    async def ws_list_recipes(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        safe_limit = max(1, min(100, int(msg.get("limit", 50))))
        safe_offset = max(0, int(msg.get("offset", 0)))
        recipes, total_count = await runtime.recipe_repo.list_recipes(
            query=str(msg.get("query") or ""),
            limit=safe_limit,
            offset=safe_offset,
        )
        connection.send_result(
            msg["id"],
            {
                "recipes": [r.model_dump(mode="json") for r in recipes],
                "total_count": total_count,
            },
        )

    @websocket_command(
        {
            "type": f"{DOMAIN}/save_recipe",
            vol.Optional("person_id"): str,
            vol.Optional("recipe_id"): vol.Any(str, None),
            vol.Required("name"): str,
            vol.Required("ingredients"): list,
            vol.Optional("servings", default=1.0): vol.Coerce(float),
            vol.Optional("serving_unit", default="serving"): str,
            vol.Optional("final_weight_g"): vol.Any(vol.Coerce(float), None),
            vol.Optional("category", default="other"): str,
            vol.Optional("allow_in_planner", default=False): bool,
            vol.Optional("notes", default=""): str,
        }
    )
    @async_response
    async def ws_save_recipe(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        try:
            ing_inputs = parse_recipe_ingredient_inputs(list(msg["ingredients"]))
            recipe = await runtime.recipe_service.save_recipe(
                profile,
                recipe_id=msg.get("recipe_id") or None,
                name=str(msg["name"]),
                ingredients=ing_inputs,
                servings=float(msg.get("servings", 1.0)),
                serving_unit=str(msg.get("serving_unit") or "serving"),
                final_weight_g=(
                    float(msg["final_weight_g"])
                    if msg.get("final_weight_g") not in (None, "")
                    else None
                ),
                category=str(msg.get("category") or "other"),
                allow_in_planner=bool(msg.get("allow_in_planner", False)),
                notes=str(msg.get("notes") or ""),
            )
        except Exception as exc:  # noqa: BLE001
            connection.send_error(msg["id"], "save_recipe_failed", str(exc))
            return
        await runtime.async_notify_person_updated(
            profile.person_id, "ws_save_recipe"
        )
        connection.send_result(msg["id"], {"recipe": recipe.model_dump(mode="json")})

    @websocket_command(
        {
            "type": f"{DOMAIN}/delete_recipe",
            vol.Required("recipe_id"): str,
            vol.Optional("person_id"): str,
        }
    )
    @async_response
    async def ws_delete_recipe(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        deleted = await runtime.recipe_service.delete_recipe(str(msg["recipe_id"]))
        if deleted:
            await runtime.async_notify_person_updated(
                profile.person_id, "ws_delete_recipe"
            )
        connection.send_result(msg["id"], {"deleted": deleted})

    @websocket_command(
        {
            "type": f"{DOMAIN}/parse_recipe_text",
            vol.Required("text"): str,
            vol.Optional("person_id"): str,
        }
    )
    @async_response
    async def ws_parse_recipe_text(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        try:
            draft = await runtime.recipe_service.parse_recipe_from_text(
                profile, str(msg["text"])
            )
        except Exception as exc:  # noqa: BLE001
            connection.send_error(msg["id"], "parse_recipe_failed", str(exc))
            return
        connection.send_result(msg["id"], {"draft": draft})

    @websocket_command(
        {
            "type": f"{DOMAIN}/get_shopping_draft",
            vol.Optional("person_id"): str,
            vol.Optional("include_committed", default=True): bool,
        }
    )
    @async_response
    async def ws_get_shopping_draft(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        draft = await runtime.shopping_repo.get_latest_draft(
            profile.person_id,
            include_committed=bool(msg.get("include_committed", True)),
        )
        connection.send_result(
            msg["id"],
            {
                "draft": draft.model_dump(mode="json") if draft else None,
                "todo_entity": profile.entity_mappings.todo_entity,
            },
        )

    @websocket_command(
        {
            "type": f"{DOMAIN}/generate_shopping_draft",
            vol.Optional("person_id"): str,
            vol.Optional("start_date"): str,
            vol.Optional("end_date"): str,
            vol.Optional("include_household", default=False): bool,
        }
    )
    @async_response
    async def ws_generate_shopping_draft(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        today = dt_util.now().date()
        start_d = (
            date.fromisoformat(msg["start_date"])
            if msg.get("start_date")
            else today
        )
        end_d = (
            date.fromisoformat(msg["end_date"])
            if msg.get("end_date")
            else start_d + timedelta(days=6)
        )
        profiles = (
            [prt.profile for prt in runtime.persons.values()]
            if bool(msg.get("include_household", False)) and runtime.persons
            else [profile]
        )
        draft = await runtime.shopping_service.generate_draft(
            profile, profiles, start_d, end_d
        )
        await runtime.async_notify_person_updated(
            profile.person_id, "ws_generate_shopping_draft"
        )
        connection.send_result(
            msg["id"],
            {
                "draft": draft.model_dump(mode="json"),
                "todo_entity": profile.entity_mappings.todo_entity,
            },
        )

    @websocket_command(
        {
            "type": f"{DOMAIN}/update_shopping_item",
            vol.Required("draft_id"): str,
            vol.Required("draft_item_id"): str,
            vol.Optional("label"): str,
            vol.Optional("quantity"): vol.Coerce(float),
            vol.Optional("unit"): str,
            vol.Optional("aisle"): str,
            vol.Optional("brand"): vol.Any(str, None),
            vol.Optional("checked"): bool,
            vol.Optional("person_id"): str,
        }
    )
    @async_response
    async def ws_update_shopping_item(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        updated = await runtime.shopping_repo.update_draft_item(
            str(msg["draft_id"]),
            str(msg["draft_item_id"]),
            label=msg.get("label"),
            quantity=float(msg["quantity"]) if msg.get("quantity") is not None else None,
            unit=msg.get("unit"),
            aisle=msg.get("aisle"),
            brand=msg.get("brand"),
            update_brand="brand" in msg,
            checked=bool(msg["checked"]) if "checked" in msg else None,
        )
        if updated is None:
            connection.send_error(msg["id"], "not_found", "Shopping item not found")
            return
        connection.send_result(msg["id"], {"draft": updated.model_dump(mode="json")})

    @websocket_command(
        {
            "type": f"{DOMAIN}/add_shopping_item",
            vol.Optional("draft_id"): str,
            vol.Required("label"): str,
            vol.Optional("quantity", default=1.0): vol.Coerce(float),
            vol.Optional("unit", default="pcs"): str,
            vol.Optional("aisle"): str,
            vol.Optional("brand"): str,
            vol.Optional("person_id"): str,
        }
    )
    @async_response
    async def ws_add_shopping_item(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        today = dt_util.now().date()
        draft_id = str(msg.get("draft_id") or "").strip()
        if not draft_id:
            existing = await runtime.shopping_repo.get_latest_draft(profile.person_id)
            if existing is None:
                existing = await runtime.shopping_repo.save_draft(
                    ShoppingDraft(
                        draft_id=f"draft_{uuid.uuid4().hex[:12]}",
                        person_id=profile.person_id,
                        start_date=today,
                        end_date=today + timedelta(days=6),
                        scope_person_ids=[profile.person_id],
                        items=[],
                    )
                )
            draft_id = existing.draft_id

        item = ShoppingDraftItem(
            draft_item_id=f"sitem_{uuid.uuid4().hex[:10]}",
            draft_id=draft_id,
            label=str(msg["label"]).strip(),
            brand=str(msg.get("brand") or "").strip() or None,
            quantity=max(0.1, float(msg.get("quantity", 1.0))),
            unit=str(msg.get("unit") or "pcs").strip() or "pcs",
            category="other",
            aisle=str(msg.get("aisle") or aisle_for_category("other")),
            checked=True,
        )
        updated = await runtime.shopping_repo.add_draft_item(draft_id, item)
        if updated is None:
            connection.send_error(msg["id"], "not_found", "Shopping draft not found")
            return
        connection.send_result(msg["id"], {"draft": updated.model_dump(mode="json")})

    @websocket_command(
        {
            "type": f"{DOMAIN}/delete_shopping_item",
            vol.Required("draft_id"): str,
            vol.Required("draft_item_id"): str,
            vol.Optional("person_id"): str,
        }
    )
    @async_response
    async def ws_delete_shopping_item(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        updated = await runtime.shopping_repo.delete_draft_item(
            str(msg["draft_id"]), str(msg["draft_item_id"])
        )
        if updated is None:
            connection.send_error(msg["id"], "not_found", "Shopping item not found")
            return
        connection.send_result(msg["id"], {"draft": updated.model_dump(mode="json")})

    @websocket_command(
        {
            "type": f"{DOMAIN}/commit_shopping_draft",
            vol.Optional("draft_id"): str,
            vol.Optional("todo_entity"): str,
            vol.Optional("person_id"): str,
        }
    )
    @async_response
    async def ws_commit_shopping_draft(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        try:
            res = await async_commit_draft_to_todo(
                hass,
                runtime,
                profile,
                draft_id=msg.get("draft_id") or None,
                todo_entity_override=msg.get("todo_entity") or None,
            )
        except Exception as exc:  # noqa: BLE001
            connection.send_error(msg["id"], "commit_shopping_failed", str(exc))
            return
        connection.send_result(msg["id"], res)

    @websocket_command(
        {
            "type": f"{DOMAIN}/get_trends",
            vol.Optional("person_id"): str,
            vol.Optional("days", default=30): vol.Coerce(int),
        }
    )
    @async_response
    async def ws_get_trends(
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
        data = person_rt.coordinator.data
        if data is None:
            connection.send_error(msg["id"], "no_data", "Dashboard state unavailable")
            return
        raw_days = int(msg.get("days", 30))
        norm_days = 7 if raw_days <= 10 else (30 if raw_days <= 45 else 90)
        start_d = data.today - timedelta(days=norm_days - 1)
        snaps = await runtime.profile_repo.get_biometric_snapshots_range(
            profile.person_id, start_d, data.today
        )
        daily_macros = await runtime.meal_repo.get_daily_macros_range(
            profile.person_id, start_d, data.today
        )
        summary = build_trends_summary(
            profile=profile,
            snapshots=snaps,
            daily_macros=daily_macros,
            current_tdee=data.tdee,
            current_targets=data.targets,
            today=data.today,
            days=norm_days,
        )
        connection.send_result(msg["id"], {"trends": summary.model_dump(mode="json")})

    @websocket_command(
        {
            "type": f"{DOMAIN}/get_workouts",
            vol.Optional("person_id"): str,
        }
    )
    @async_response
    async def ws_get_workouts(
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
        data = person_rt.coordinator.data
        if data is None:
            connection.send_error(msg["id"], "no_data", "Dashboard state unavailable")
            return
        schedule = await runtime.workout_repo.list_schedule(profile.person_id)
        override = await runtime.workout_repo.get_override(
            profile.person_id, data.today
        )
        connection.send_result(
            msg["id"],
            {
                "today": data.today.isoformat(),
                "is_training_day": data.is_training_day,
                "training_day_source": data.training_day_source,
                "has_manual_override": override is not None,
                "workout_calendar_entity": profile.entity_mappings.workout_calendar_entity,
                "activities_entity": profile.entity_mappings.activities_entity,
                "has_manual_training_targets": bool(
                    profile.manual_targets is not None
                    and profile.manual_targets.training_kcal is not None
                ),
                "schedule": [w.model_dump(mode="json") for w in schedule],
                "calendar_workouts": [
                    w.model_dump(mode="json") for w in data.calendar_workouts
                ],
                "recent_activities": [
                    a.model_dump(mode="json") for a in data.recent_activities
                ],
            },
        )

    @websocket_command(
        {
            "type": f"{DOMAIN}/save_workout_item",
            vol.Optional("person_id"): str,
            vol.Optional("schedule_id"): vol.Any(str, None),
            vol.Required("weekday"): vol.Coerce(int),
            vol.Required("title"): str,
            vol.Optional("activity_type", default="strength"): str,
            vol.Optional("duration_min", default=60): vol.Coerce(int),
            vol.Optional("estimated_kcal"): vol.Any(vol.Coerce(float), None),
            vol.Optional("time_of_day", default="18:00"): str,
            vol.Optional("notes", default=""): str,
            vol.Optional("enabled", default=True): bool,
        }
    )
    @async_response
    async def ws_save_workout_item(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        raw_type = str(msg.get("activity_type") or "strength").strip().lower()
        try:
            act_type = WorkoutActivityType(raw_type)
        except ValueError:
            act_type = WorkoutActivityType.OTHER
        item = WorkoutScheduleItem(
            schedule_id=str(msg.get("schedule_id") or ""),
            person_id=profile.person_id,
            weekday=max(0, min(6, int(msg["weekday"]))),
            title=str(msg["title"]).strip(),
            activity_type=act_type,
            duration_min=max(5, min(480, int(msg.get("duration_min", 60)))),
            estimated_kcal=(
                float(msg["estimated_kcal"])
                if msg.get("estimated_kcal") not in (None, "")
                else None
            ),
            time_of_day=str(msg.get("time_of_day") or "18:00"),
            notes=str(msg.get("notes") or ""),
            enabled=bool(msg.get("enabled", True)),
        )
        saved = await runtime.workout_repo.upsert_schedule_item(item)
        await runtime.async_notify_person_updated(
            profile.person_id, "ws_save_workout_item"
        )
        connection.send_result(msg["id"], {"workout": saved.model_dump(mode="json")})

    @websocket_command(
        {
            "type": f"{DOMAIN}/delete_workout_item",
            vol.Required("schedule_id"): str,
            vol.Optional("person_id"): str,
        }
    )
    @async_response
    async def ws_delete_workout_item(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        deleted = await runtime.workout_repo.delete_schedule_item(
            profile.person_id, str(msg["schedule_id"])
        )
        if deleted:
            await runtime.async_notify_person_updated(
                profile.person_id, "ws_delete_workout_item"
            )
        connection.send_result(msg["id"], {"deleted": deleted})

    @websocket_command(
        {
            "type": f"{DOMAIN}/set_training_day",
            vol.Optional("person_id"): str,
            vol.Optional("is_training_day"): vol.Any(bool, None),
            vol.Optional("date"): str,
            vol.Optional("clear_override", default=False): bool,
        }
    )
    @async_response
    async def ws_set_training_day(
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        runtime: DomainRuntime = hass.data[DOMAIN]
        profile = _resolve_ws_person(hass, connection, msg)
        if profile is None:
            return
        target_d = (
            date.fromisoformat(msg["date"])
            if msg.get("date")
            else dt_util.now().date()
        )
        if bool(msg.get("clear_override", False)) or msg.get("is_training_day") is None:
            await runtime.workout_repo.clear_override(profile.person_id, target_d)
        else:
            await runtime.workout_repo.set_override(
                TrainingDayOverride(
                    person_id=profile.person_id,
                    override_date=target_d,
                    is_training_day=bool(msg["is_training_day"]),
                    source="manual",
                )
            )
        await runtime.async_notify_person_updated(
            profile.person_id, "ws_set_training_day"
        )
        person_rt = runtime.persons.get(profile.person_id)
        data = person_rt.coordinator.data if person_rt else None
        connection.send_result(
            msg["id"],
            {
                "is_training_day": data.is_training_day if data else False,
                "training_day_source": data.training_day_source if data else "rest",
            },
        )

    for cmd in (
        ws_list_persons,
        ws_dashboard,
        ws_log_text,
        ws_log_item,
        ws_confirm_meal,
        ws_add_meal_item,
        ws_update_meal_item,
        ws_delete_meal_item,
        ws_update_meal_slot,
        ws_delete_meal,
        ws_restore_meal,
        ws_search_foods,
        ws_lookup_barcode,
        ws_save_food,
        ws_log_weight,
        ws_get_plan,
        ws_generate_plan,
        ws_suggest_meal,
        ws_log_suggestion,
        ws_swap_plan_meal,
        ws_promote_plan_meal,
        ws_rebalance_today,
        ws_get_preferences,
        ws_upsert_preference,
        ws_delete_preference,
        ws_set_meal_feedback,
        ws_list_recipes,
        ws_save_recipe,
        ws_delete_recipe,
        ws_parse_recipe_text,
        ws_get_shopping_draft,
        ws_generate_shopping_draft,
        ws_update_shopping_item,
        ws_add_shopping_item,
        ws_delete_shopping_item,
        ws_commit_shopping_draft,
        ws_get_trends,
        ws_get_workouts,
        ws_save_workout_item,
        ws_delete_workout_item,
        ws_set_training_day,
    ):
        websocket_api.async_register_command(hass, cmd)
