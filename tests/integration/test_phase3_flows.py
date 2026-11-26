"""End-to-end Home Assistant integration tests for Phase 3 features."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.ha_calories_buddy.biometrics_backfill import (
    async_backfill_biometrics_from_recorder,
)
from custom_components.ha_calories_buddy.const import (
    CONF_ACTIVITIES_ENTITY,
    CONF_BODY_FAT_ENTITY,
    CONF_DISPLAY_NAME,
    CONF_GOAL_TYPE,
    CONF_HA_PERSON_ENTITY,
    CONF_HA_USER_ID,
    CONF_HEIGHT_CM,
    CONF_INITIAL_WEIGHT_KG,
    CONF_LANGUAGE_OVERRIDE,
    CONF_MANUAL_CARBS_G,
    CONF_MANUAL_FAT_G,
    CONF_MANUAL_KCAL,
    CONF_MANUAL_PROTEIN_G,
    CONF_MANUAL_TRAINING_CARBS_G,
    CONF_MANUAL_TRAINING_FAT_G,
    CONF_MANUAL_TRAINING_KCAL,
    CONF_MANUAL_TRAINING_PROTEIN_G,
    CONF_NOTIFY_SERVICE,
    CONF_PERSON_ID,
    CONF_SEX,
    CONF_STEPS_ENTITY,
    CONF_TARGET_BODY_FAT_PCT,
    CONF_TARGET_MODE,
    CONF_TARGET_WEIGHT_KG,
    CONF_TDEE_ENGINE,
    CONF_TODO_ENTITY,
    CONF_TOTAL_KCAL_ENTITY,
    CONF_WEEKLY_RATE_KG,
    CONF_WEIGHT_ENTITY,
    CONF_WORKOUT_CALENDAR_ENTITY,
    DOMAIN,
)
from custom_components.ha_calories_buddy.core.models import (
    BiometricSnapshot,
    ConfidenceLevel,
    IngredientRole,
    Macros,
    MealLog,
    MealLogItem,
    MealPlan,
    MealPlanDay,
    MealPlanItem,
    MealStatus,
    PlannedIngredient,
    PreparationState,
    TDEEEngineType,
    WarningCode,
)
from custom_components.ha_calories_buddy.notifications import (
    ACTION_CONFIRM_PREFIX,
    ACTION_DELETE_PREFIX,
    async_send_meal_notification,
)
from custom_components.ha_calories_buddy.runtime import DomainRuntime
from tests.integration.conftest import setup_hub_and_persons


async def test_phase3_shopping_workouts_switch_trends_and_notifications(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Verify switch.training_day, calendar/activities sync, Butler todo.* dedup, Trends WS, and actionable notify."""
    today = dt_util.now().date()
    today_iso = today.isoformat()

    hass.states.async_set("calendar.garmin_coach", "off")
    hass.states.async_set(
        "sensor.garmin_activities",
        "2",
        {
            "last_activities": [
                {
                    "activityId": 1001,
                    "activityName": "Trani Corsa",
                    "activityType": {"typeKey": "running"},
                    "startTimeLocal": f"{today_iso} 07:15:00",
                    "duration": 2400,  # 40 min
                    "calories": 480,
                    "distance": 8200,
                }
            ]
        },
    )

    cal_calls: list[dict[str, object]] = []

    async def handle_cal_get_events(call: object) -> dict[str, object]:
        cal_calls.append(dict(getattr(call, "data", {})))
        return {
            "calendar.garmin_coach": {
                "events": [
                    {
                        "summary": "Upper Strength Garmin",
                        "start": f"{today_iso}T18:00:00",
                        "end": f"{today_iso}T19:00:00",
                        "description": "Bench + Row",
                    }
                ]
            }
        }

    hass.services.async_register(
        "calendar",
        "get_events",
        handle_cal_get_events,
        supports_response=True,  # type: ignore[arg-type]
    )

    added_todos: list[str] = []

    async def handle_todo_get_items(call: object) -> dict[str, object]:
        return {
            "todo.spesa_casa": {
                "items": [{"summary": "Riso Basmati (1 kg)", "status": "needs_action"}]
            }
        }

    async def handle_todo_add_item(call: object) -> None:
        data = getattr(call, "data", {})
        added_todos.append(str(data["item"]))

    hass.services.async_register(
        "todo",
        "get_items",
        handle_todo_get_items,
        supports_response=True,  # type: ignore[arg-type]
    )
    hass.services.async_register("todo", "add_item", handle_todo_add_item)

    sent_notifications: list[dict[str, object]] = []

    async def handle_notify_phone(call: object) -> None:
        sent_notifications.append(dict(getattr(call, "data", {})))

    hass.services.async_register("notify", "mobile_app_phone", handle_notify_phone)

    _, person_entries = await setup_hub_and_persons(
        hass,
        persons=[
            {
                CONF_PERSON_ID: "person_nicola",
                CONF_DISPLAY_NAME: "Nicola",
                CONF_HA_PERSON_ENTITY: "person.nicola",
                CONF_HA_USER_ID: "uid_nicola",
                CONF_LANGUAGE_OVERRIDE: "it",
                CONF_SEX: "male",
                CONF_HEIGHT_CM: 178.0,
                CONF_INITIAL_WEIGHT_KG: 78.0,
                CONF_TARGET_WEIGHT_KG: 74.0,
                CONF_TARGET_BODY_FAT_PCT: 15.0,
                CONF_TARGET_MODE: "manual",
                CONF_GOAL_TYPE: "lose_weight",
                CONF_WEEKLY_RATE_KG: -0.4,
                CONF_TDEE_ENGINE: TDEEEngineType.ADAPTIVE.value,
                CONF_MANUAL_KCAL: 1900.0,
                CONF_MANUAL_PROTEIN_G: 150.0,
                CONF_MANUAL_CARBS_G: 190.0,
                CONF_MANUAL_FAT_G: 60.0,
                CONF_MANUAL_TRAINING_KCAL: 2350.0,
                CONF_MANUAL_TRAINING_PROTEIN_G: 165.0,
                CONF_MANUAL_TRAINING_CARBS_G: 280.0,
                CONF_MANUAL_TRAINING_FAT_G: 62.0,
                CONF_TODO_ENTITY: "todo.spesa_casa",
                CONF_NOTIFY_SERVICE: "notify.mobile_app_phone",
                CONF_WORKOUT_CALENDAR_ENTITY: "calendar.garmin_coach",
                CONF_ACTIVITIES_ENTITY: "sensor.garmin_activities",
            }
        ],
    )
    assert len(person_entries) == 1
    runtime: DomainRuntime = hass.data[DOMAIN]

    # Setting up ha_calories_buddy loads HA's built-in calendar platform, which replaces
    # calendar.get_events; re-attach our stub so subsequent refreshes see Garmin events.
    hass.services.async_register(
        "calendar",
        "get_events",
        handle_cal_get_events,
        supports_response=True,  # type: ignore[arg-type]
    )

    # 1. Garmin calendar made today a Training Day automatically -> Training targets (2350 kcal) active
    switch_st = hass.states.get("switch.calories_buddy_nicola_training_day")
    assert switch_st is not None
    assert switch_st.state == "on"
    assert switch_st.attributes["source"] == "calendar"
    assert switch_st.attributes["training_targets_active"] is True

    target_st = hass.states.get("sensor.calories_buddy_nicola_calories_target")
    assert target_st is not None
    assert float(target_st.state) == pytest.approx(2350.0)
    assert target_st.attributes["is_training_day"] is True

    # Toggle switch OFF -> switches to Rest Day targets (1900 kcal)
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.calories_buddy_nicola_training_day"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get("switch.calories_buddy_nicola_training_day").state == "off"  # type: ignore[union-attr]
    assert float(hass.states.get("sensor.calories_buddy_nicola_calories_target").state) == pytest.approx(1900.0)  # type: ignore[union-attr,arg-type]

    # Clear override -> calendar restores ON (2350 kcal)
    await hass.services.async_call(
        DOMAIN,
        "set_training_day",
        {"person": "person_nicola", "clear_override": True},
        blocking=True,
    )
    assert hass.states.get("switch.calories_buddy_nicola_training_day").state == "on"  # type: ignore[union-attr]

    # 2. Workouts WS & recurring schedule service
    ws = await hass_ws_client(hass)
    await hass.services.async_call(
        DOMAIN,
        "save_workout_schedule",
        {
            "person": "person_nicola",
            "weekday": (today + timedelta(days=2)).weekday(),
            "title": "Leg Day Heavy",
            "activity_type": "strength",
            "duration_min": 75,
        },
        blocking=True,
    )
    await ws.send_json({"id": 10, "type": f"{DOMAIN}/get_workouts", "person_id": "person_nicola"})
    wrk_resp = await ws.receive_json()
    assert wrk_resp["success"] is True
    assert wrk_resp["result"]["schedule"][0]["title"] == "Leg Day Heavy"
    assert wrk_resp["result"]["recent_activities"][0]["name"] == "Trani Corsa"
    assert wrk_resp["result"]["recent_activities"][0]["duration_min"] == 40.0

    # 3. Butler Shopping Draft generation + edit + todo.* commit with dedup
    rice_food = (await runtime.food_repo.search("basmati", limit=1))[0]
    chicken_food = (await runtime.food_repo.search("chicken breast", limit=1))[0]

    await runtime.plan_repo.save_plan(
        MealPlan(
            plan_id="plan_shop_test",
            person_id="person_nicola",
            start_date=today,
            end_date=today,
            days=[
                MealPlanDay(
                    plan_day_id="day_shop_0",
                    plan_id="plan_shop_test",
                    plan_date=today,
                    target_macros=Macros(kcal=2350, protein_g=165, carbs_g=280, fat_g=62),
                    items=[
                        MealPlanItem(
                            plan_item_id="pi_shop_1",
                            plan_day_id="day_shop_0",
                            meal_slot="Lunch",
                            title="Pollo e Riso",
                            ingredients_json=json.dumps(
                                [
                                    PlannedIngredient(
                                        food_id=rice_food.food_id,
                                        name=rice_food.name,
                                        grams=250.0,
                                        preparation_state=PreparationState.COOKED,
                                        role=IngredientRole.CARB_ANCHOR,
                                        macros=Macros(kcal=350, protein_g=7.5, carbs_g=78, fat_g=0.8),
                                    ).model_dump(mode="json"),
                                    PlannedIngredient(
                                        food_id=chicken_food.food_id,
                                        name=chicken_food.name,
                                        grams=150.0,
                                        preparation_state=PreparationState.COOKED,
                                        role=IngredientRole.PROTEIN_ANCHOR,
                                        macros=Macros(kcal=220, protein_g=46, carbs_g=0, fat_g=2.4),
                                    ).model_dump(mode="json"),
                                ]
                            ),
                            macros=Macros(kcal=570, protein_g=53.5, carbs_g=78, fat_g=3.2),
                        )
                    ],
                )
            ],
        )
    )
    await ws.send_json(
        {
            "id": 11,
            "type": f"{DOMAIN}/generate_shopping_draft",
            "person_id": "person_nicola",
            "start_date": today_iso,
            "end_date": today_iso,
        }
    )
    shop_resp = await ws.receive_json()
    assert shop_resp["success"] is True
    draft = shop_resp["result"]["draft"]
    assert len(draft["items"]) == 2

    await ws.send_json(
        {
            "id": 12,
            "type": f"{DOMAIN}/commit_shopping_draft",
            "person_id": "person_nicola",
            "draft_id": draft["draft_id"],
        }
    )
    commit_resp = await ws.receive_json()
    assert commit_resp["success"] is True
    # Riso Basmati was already on todo.spesa_casa -> skipped as duplicate; Chicken Breast added!
    assert commit_resp["result"]["skipped_duplicates_count"] == 1
    assert commit_resp["result"]["added_count"] == 1
    assert len(added_todos) == 1

    # 4. Trends WS (seed 10 snapshots and verify 30d series)
    nicola = runtime.persons["person_nicola"].profile
    for d_ago in range(1, 12):
        d = today - timedelta(days=d_ago)
        await runtime.profile_repo.upsert_biometric_snapshot(
            BiometricSnapshot(
                snapshot_id=f"sn_{d_ago}",
                person_id="person_nicola",
                snap_date=d,
                weight_kg=round(79.0 - (11 - d_ago) * 0.06, 2),
                weight_ema_kg=round(79.0 - (11 - d_ago) * 0.06, 2),
                body_fat_pct=19.5,
                computed_tdee=2450.0,
                target_kcal=1950.0,
            )
        )
    # Default get_trends uses 14 days and injects today's live weight when sensor is available
    hass.states.async_set("sensor.nicola_weight_live", "78.1", {"unit_of_measurement": "kg"})
    nicola_rt = runtime.persons["person_nicola"]
    nicola_rt.profile = nicola_rt.profile.model_copy(
        update={
            "entity_mappings": nicola_rt.profile.entity_mappings.model_copy(
                update={"weight_entity": "sensor.nicola_weight_live"}
            )
        }
    )
    nicola_rt.coordinator.profile = nicola_rt.profile
    await nicola_rt.coordinator.async_refresh()

    await ws.send_json({"id": 13, "type": f"{DOMAIN}/get_trends", "person_id": "person_nicola"})
    trends_14_resp = await ws.receive_json()
    assert trends_14_resp["success"] is True
    t14 = trends_14_resp["result"]["trends"]
    assert t14["days"] == 14
    assert t14["points"][-1]["point_date"] == today_iso
    assert t14["points"][-1]["weight_kg"] == pytest.approx(78.1)

    await ws.send_json({"id": 14, "type": f"{DOMAIN}/get_trends", "person_id": "person_nicola", "days": 30})
    trends_resp = await ws.receive_json()
    assert trends_resp["success"] is True
    t_data = trends_resp["result"]["trends"]
    assert t_data["days"] == 30
    assert t_data["expected_vs_actual"]["status"] == "on_track"
    assert t_data["expected_vs_actual"]["eta_date"] is not None

    # 5. Actionable Notifications dispatch & mobile_app_notification_action confirm/delete handling
    pending_meal = MealLog(
        log_id="log_notify_1",
        person_id="person_nicola",
        log_date=today,
        meal_slot="Cena",
        status=MealStatus.PENDING_CONFIRMATION,
        source_channel="voice",
        raw_input_text="Fetta ciambellone",
        total_macros=Macros(kcal=280, protein_g=5, carbs_g=38, fat_g=12),
        items=[
            MealLogItem(
                item_id="it_n1",
                log_id="log_notify_1",
                display_name="Ciambellone",
                input_quantity=1,
                input_unit="slice",
                input_state=PreparationState.READY_TO_EAT,
                resolved_grams_ref=80,
                macros=Macros(kcal=280, protein_g=5, carbs_g=38, fat_g=12),
                confidence=ConfidenceLevel.LOW,
                warning_code=WarningCode.LLM_ESTIMATED,
            )
        ],
    )
    await runtime.meal_repo.create_meal_log(pending_meal)
    ok = await async_send_meal_notification(
        hass, nicola, pending_meal, needs_confirmation=True, warnings=["AI estimated"]
    )
    assert ok is True
    assert sent_notifications[-1]["data"]["actions"][0]["action"] == f"{ACTION_CONFIRM_PREFIX}log_notify_1"  # type: ignore[index]

    hass.bus.async_fire(
        "mobile_app_notification_action",
        {"action": f"{ACTION_CONFIRM_PREFIX}log_notify_1"},
    )
    await hass.async_block_till_done()
    confirmed = await runtime.meal_repo.get_meal_log("log_notify_1")
    assert confirmed is not None and confirmed.status == MealStatus.CONFIRMED

    hass.bus.async_fire(
        "mobile_app_notification_action",
        {"action": f"{ACTION_DELETE_PREFIX}log_notify_1"},
    )
    await hass.async_block_till_done()
    deleted = await runtime.meal_repo.get_meal_log("log_notify_1")
    assert deleted is not None and deleted.status == MealStatus.DELETED


async def test_config_flow_supports_phase3_adaptive_and_workout_entities(
    hass: HomeAssistant,
) -> None:
    """Verify ConfigFlow exposes adaptive engine, target goals, workout calendar, and activities sensors."""
    from homeassistant import config_entries
    from homeassistant.data_entry_flow import FlowResultType

    from custom_components.ha_calories_buddy.const import (
        CONF_LANGUAGE,
        CONF_LLM_PROVIDER,
        LLM_PROVIDER_NONE,
    )

    # Hub
    res = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"],
        {CONF_LANGUAGE: "en", CONF_LLM_PROVIDER: LLM_PROVIDER_NONE},
    )
    assert res["type"] == FlowResultType.CREATE_ENTRY

    # Person with adaptive + targets + workout entities
    p_res = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    p_res = await hass.config_entries.flow.async_configure(
        p_res["flow_id"], {CONF_DISPLAY_NAME: "Elena"}
    )
    p_res = await hass.config_entries.flow.async_configure(
        p_res["flow_id"],
        {
            CONF_SEX: "female",
            CONF_HEIGHT_CM: 165.0,
            CONF_INITIAL_WEIGHT_KG: 62.0,
            CONF_TARGET_MODE: "physiology",
            CONF_GOAL_TYPE: "lose_weight",
            CONF_WEEKLY_RATE_KG: -0.3,
            CONF_TARGET_WEIGHT_KG: 57.0,
            CONF_TARGET_BODY_FAT_PCT: 21.0,
            CONF_TDEE_ENGINE: TDEEEngineType.ADAPTIVE.value,
        },
    )
    p_res = await hass.config_entries.flow.async_configure(
        p_res["flow_id"],
        {
            CONF_WORKOUT_CALENDAR_ENTITY: "calendar.elena_garmin",
            CONF_ACTIVITIES_ENTITY: "sensor.elena_activities",
        },
    )
    p_res = await hass.config_entries.flow.async_configure(p_res["flow_id"], {})
    p_res = await hass.config_entries.flow.async_configure(p_res["flow_id"], {})
    assert p_res["type"] == FlowResultType.CREATE_ENTRY
    runtime: DomainRuntime = hass.data[DOMAIN]
    elena = next(p.profile for p in runtime.persons.values() if p.profile.display_name == "Elena")
    assert elena.tdee_engine == TDEEEngineType.ADAPTIVE
    assert elena.target_weight_kg == pytest.approx(57.0)
    assert elena.entity_mappings.workout_calendar_entity == "calendar.elena_garmin"
    assert elena.entity_mappings.activities_entity == "sensor.elena_activities"


async def test_recorder_biometric_backfill_dedup_conversions_and_ema(
    hass: HomeAssistant,
) -> None:
    """Verify HA Recorder backfill deduplicates Garmin carry-forward plateaus, prefers `change`, converts units, and updates EMAs."""
    await setup_hub_and_persons(
        hass,
        persons=[
            {
                CONF_PERSON_ID: "person_bf",
                CONF_DISPLAY_NAME: "Bruno",
                CONF_HA_PERSON_ENTITY: "person.bruno",
                CONF_WEIGHT_ENTITY: "sensor.bruno_weight_lb",
                CONF_BODY_FAT_ENTITY: "sensor.bruno_bf",
                CONF_TOTAL_KCAL_ENTITY: "sensor.bruno_total_kj",
                CONF_STEPS_ENTITY: "sensor.bruno_steps",
            }
        ],
    )
    runtime: DomainRuntime = hass.data[DOMAIN]
    bruno = runtime.persons["person_bf"].profile

    hass.states.async_set("sensor.bruno_weight_lb", "176.4", {"unit_of_measurement": "lb"})
    hass.states.async_set("sensor.bruno_bf", "19.5", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.bruno_total_kj", "10460", {"unit_of_measurement": "kJ"})
    hass.states.async_set("sensor.bruno_steps", "8500", {})

    today = dt_util.now().date()
    d5 = today - timedelta(days=5)
    d4 = today - timedelta(days=4)
    d3 = today - timedelta(days=3)
    d2 = today - timedelta(days=2)

    def _ts(d: object) -> float:
        return datetime(d.year, d.month, d.day, 12, 0, tzinfo=dt_util.DEFAULT_TIME_ZONE).timestamp()  # type: ignore[attr-defined]

    stats_payload = {
        # Day -5: 180 lb; Day -4: carry-forward 180 lb (SKIPPED!); Day -3: transition to 178 lb
        "sensor.bruno_weight_lb": [
            {"start": _ts(d5), "mean": 180.0, "min": 180.0, "max": 180.0},
            {"start": _ts(d4), "mean": 180.0, "min": 180.0, "max": 180.0},
            {"start": _ts(d3), "mean": 179.2, "min": 178.0, "max": 180.0},
        ],
        "sensor.bruno_bf": [
            {"start": _ts(d5), "mean": 20.0, "min": 20.0, "max": 20.0},
            {"start": _ts(d4), "mean": 20.0, "min": 20.0, "max": 20.0},
        ],
        "sensor.bruno_total_kj": [
            # Prefers change (10460 kJ -> ~2500 kcal) over end state
            {"start": _ts(d4), "state": 6000.0, "change": 10460.0},
        ],
        "sensor.bruno_steps": [
            {"start": _ts(d4), "state": 4200.0, "change": 9100.0},
        ],
    }
    hist_payload = {
        # Raw history overrides d2 precisely for weight & fills missing steps if absent from stats
        "sensor.bruno_weight_lb": [
            State("sensor.bruno_weight_lb", "176.4", last_changed=datetime(d2.year, d2.month, d2.day, 8, 30, tzinfo=UTC)),
        ],
    }

    fake_recorder = MagicMock()

    async def _run_job(fn: object, *args: object) -> object:
        return fn(*args)  # type: ignore[operator]

    fake_recorder.async_add_executor_job = _run_job
    hass.config.components.add("recorder")

    with (
        patch("homeassistant.helpers.recorder.get_instance", return_value=fake_recorder),
        patch(
            "homeassistant.components.recorder.statistics.statistics_during_period",
            return_value=stats_payload,
        ),
        patch(
            "homeassistant.components.recorder.history.get_significant_states",
            return_value=hist_payload,
        ),
    ):
        written = await async_backfill_biometrics_from_recorder(
            hass, runtime, bruno, days=14, today=today
        )

    assert written == 4  # d5, d4 (totals only), d3, d2
    snaps = await runtime.profile_repo.get_biometric_snapshots_range(
        "person_bf", today - timedelta(days=10), today
    )
    by_date = {s.snap_date: s for s in snaps}
    assert by_date[d5].weight_kg == pytest.approx(81.65, abs=0.05)  # 180 lb
    assert by_date[d4].weight_kg is None  # Garmin flat carry-forward skipped!
    assert by_date[d4].total_kcal == pytest.approx(2500.0, abs=1.0)  # 10460 kJ via `change`
    assert by_date[d4].steps == 9100
    assert by_date[d3].weight_kg == pytest.approx(80.74, abs=0.05)  # 178 lb extracted from min/max
    assert by_date[d2].weight_kg == pytest.approx(80.01, abs=0.05)  # Exact history 176.4 lb
    assert by_date[d2].weight_ema_kg is not None
