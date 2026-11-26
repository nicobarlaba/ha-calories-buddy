"""Integration tests for sensors, biometric fallback, unit conversion, and calendars."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.ha_calories_buddy.const import (
    CONF_ACTIVE_KCAL_ENTITY,
    CONF_ACTIVITY_LEVEL,
    CONF_BIRTH_YEAR,
    CONF_DISPLAY_NAME,
    CONF_GOAL_TYPE,
    CONF_HA_PERSON_ENTITY,
    CONF_HA_USER_ID,
    CONF_HEIGHT_CM,
    CONF_INITIAL_WEIGHT_KG,
    CONF_LANGUAGE_OVERRIDE,
    CONF_MEAL_SLOTS,
    CONF_PERSON_ID,
    CONF_SEX,
    CONF_TARGET_MODE,
    CONF_TDEE_ENGINE,
    CONF_WEEKLY_RATE_KG,
    CONF_WEIGHT_ENTITY,
    DOMAIN,
)
from custom_components.ha_calories_buddy.runtime import DomainRuntime
from tests.integration.conftest import setup_hub_and_persons


async def test_sensors_unit_conversion_unavailable_fallback_and_calendars(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Verify 10 sensors + 1 calendar per person, lb->kg conversion, fallback, fiber, and calendar."""
    hass.states.async_set(
        "sensor.marco_scale",
        "176.37",
        {"unit_of_measurement": "lb"},
    )
    hass.states.async_set(
        "sensor.marco_garmin_active",
        "450",
        {"unit_of_measurement": "kcal"},
    )

    _, person_entries = await setup_hub_and_persons(
        hass,
        persons=[
            {
                CONF_PERSON_ID: "person_marco",
                CONF_DISPLAY_NAME: "Marco",
                CONF_HA_PERSON_ENTITY: "person.marco",
                CONF_HA_USER_ID: "uid_marco",
                CONF_LANGUAGE_OVERRIDE: "it",
                CONF_SEX: "male",
                CONF_BIRTH_YEAR: 1990,
                CONF_HEIGHT_CM: 180.0,
                CONF_INITIAL_WEIGHT_KG: 75.0,
                CONF_TARGET_MODE: "physiology",
                CONF_GOAL_TYPE: "maintain",
                CONF_WEEKLY_RATE_KG: 0.0,
                CONF_TDEE_ENGINE: "wearable_dynamic",
                CONF_ACTIVITY_LEVEL: "moderate",
                CONF_WEIGHT_ENTITY: "sensor.marco_scale",
                CONF_ACTIVE_KCAL_ENTITY: "sensor.marco_garmin_active",
                CONF_MEAL_SLOTS: "Colazione, Pranzo, Cena",
            },
            {
                CONF_PERSON_ID: "person_giulia",
                CONF_DISPLAY_NAME: "Giulia",
                CONF_HA_PERSON_ENTITY: "person.giulia",
                CONF_HA_USER_ID: "uid_giulia",
                CONF_LANGUAGE_OVERRIDE: "en",
                CONF_SEX: "female",
                CONF_BIRTH_YEAR: 1993,
                CONF_HEIGHT_CM: 165.0,
                CONF_INITIAL_WEIGHT_KG: 58.0,
                CONF_TARGET_MODE: "physiology",
                CONF_GOAL_TYPE: "maintain",
                CONF_WEEKLY_RATE_KG: 0.0,
                CONF_TDEE_ENGINE: "static",
                CONF_ACTIVITY_LEVEL: "light",
                CONF_MEAL_SLOTS: "Breakfast, Lunch, Dinner",
            },
        ],
    )

    ent_reg = er.async_get(hass)
    marco_entities = er.async_entries_for_config_entry(ent_reg, person_entries[0].entry_id)
    giulia_entities = er.async_entries_for_config_entry(ent_reg, person_entries[1].entry_id)

    marco_sensors = [e for e in marco_entities if e.domain == "sensor"]
    marco_cals = [e for e in marco_entities if e.domain == "calendar"]
    marco_switches = [e for e in marco_entities if e.domain == "switch"]
    giulia_sensors = [e for e in giulia_entities if e.domain == "sensor"]
    giulia_cals = [e for e in giulia_entities if e.domain == "calendar"]
    giulia_switches = [e for e in giulia_entities if e.domain == "switch"]

    assert len(marco_sensors) == 10
    assert len(giulia_sensors) == 10
    assert len(marco_cals) == 1
    assert len(giulia_cals) == 1
    assert len(marco_switches) == 1
    assert len(giulia_switches) == 1

    marco_fiber_ent = next(
        e.entity_id for e in marco_sensors if e.unique_id == "person_marco_fiber_consumed"
    )
    fiber_init = hass.states.get(marco_fiber_ent)
    assert fiber_init is not None
    assert 20.0 <= float(fiber_init.attributes["target_g"]) <= 45.0

    # Distinct initial calendar colors assigned per person
    marco_cal_opts = marco_cals[0].options.get("calendar", {})
    giulia_cal_opts = giulia_cals[0].options.get("calendar", {})
    assert marco_cal_opts.get("color") != giulia_cal_opts.get("color")

    # Verify lb -> kg conversion (176.37 lb -> 80.0 kg) recorded on TDEE sensor attributes
    marco_tdee_ent = next(
        e.entity_id for e in marco_sensors if e.unique_id == "person_marco_tdee"
    )
    tdee_state = hass.states.get(marco_tdee_ent)
    assert tdee_state is not None
    assert tdee_state.attributes["weight_kg"] == pytest.approx(80.0, abs=0.1)

    ws_client = await hass_ws_client(hass)
    await ws_client.send_json(
        {"id": 1, "type": f"{DOMAIN}/dashboard", "person_id": "person_marco"}
    )
    ws_dash1 = await ws_client.receive_json()
    assert ws_dash1["success"] is True
    assert ws_dash1["result"]["biometrics"]["weight_entity_id"] == "sensor.marco_scale"
    assert ws_dash1["result"]["biometrics"]["weight_sensor_available"] is True
    assert ws_dash1["result"]["biometrics"]["weight_kg"] == pytest.approx(80.0, abs=0.1)

    # Persist today's snapshot, then simulate weight sensor becoming unavailable -> retains 80.0 kg!
    runtime: DomainRuntime = hass.data[DOMAIN]
    await runtime.persons["person_marco"].coordinator.async_persist_today_snapshot()
    hass.states.async_set("sensor.marco_scale", "unavailable")
    await runtime.persons["person_marco"].coordinator.async_refresh()
    await hass.async_block_till_done()

    await ws_client.send_json(
        {"id": 2, "type": f"{DOMAIN}/dashboard", "person_id": "person_marco"}
    )
    ws_dash2 = await ws_client.receive_json()
    assert ws_dash2["success"] is True
    assert ws_dash2["result"]["biometrics"]["weight_sensor_available"] is False
    assert ws_dash2["result"]["biometrics"]["weight_source"] == "snapshot"

    tdee_after = hass.states.get(marco_tdee_ent)
    assert tdee_after is not None
    assert tdee_after.attributes["weight_kg"] == pytest.approx(80.0, abs=0.1)

    # Log a meal for Marco and verify calendar API returns both meal event & daily summary
    await hass.services.async_call(
        DOMAIN,
        "log_meal_item",
        {
            "person": "Marco",
            "food_id": "seed_basmati_rice_raw",
            "quantity": 80,
            "unit": "g",
            "state": "raw",
            "meal_slot": "Pranzo",
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    marco_consumed_ent = next(
        e.entity_id
        for e in marco_sensors
        if e.unique_id == "person_marco_calories_consumed"
    )
    consumed_state = hass.states.get(marco_consumed_ent)
    assert consumed_state is not None
    assert float(consumed_state.state) == pytest.approx(281.6, abs=0.2)

    # Log lentils too so measurable fiber hits fiber_consumed sensor & calendar description
    await hass.services.async_call(
        DOMAIN,
        "log_meal_item",
        {
            "person": "Marco",
            "food_id": "seed_lentils_cooked",
            "quantity": 100,
            "unit": "g",
            "state": "cooked",
            "meal_slot": "Colazione",
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    fiber_after = hass.states.get(marco_fiber_ent)
    assert fiber_after is not None
    assert float(fiber_after.state) >= 5.0

    # Add a retrospective Colazione log logged at 22:00 local -> must anchor to 08:00 slot time
    from datetime import datetime, time

    from custom_components.ha_calories_buddy.core.models import (
        ConfidenceLevel,
        Macros,
        MealLog,
        MealLogItem,
        MealPlan,
        MealPlanDay,
        MealPlanItem,
        MealStatus,
        PlanItemStatus,
        PreparationState,
    )

    today = dt_util.now().date()
    retro_logged_at = datetime.combine(
        today, time(hour=22, minute=0), tzinfo=dt_util.get_default_time_zone()
    )
    retro_meal = MealLog(
        log_id="retro_colazione_test",
        person_id="person_marco",
        log_date=today,
        logged_at=retro_logged_at,
        meal_slot="Colazione",
        status=MealStatus.CONFIRMED,
        total_macros=Macros(kcal=120, protein_g=4, carbs_g=20, fat_g=2, fiber_g=3.5),
        confidence=ConfidenceLevel.HIGH,
        source_channel="manual",
        items=[
            MealLogItem(
                item_id="retro_item_1",
                log_id="retro_colazione_test",
                display_name="Avena test",
                input_quantity=30,
                input_unit="g",
                input_state=PreparationState.RAW,
                resolved_grams_ref=30,
                macros=Macros(kcal=120, protein_g=4, carbs_g=20, fat_g=2, fiber_g=3.5),
                confidence=ConfidenceLevel.HIGH,
            )
        ],
    )
    await runtime.meal_repo.create_meal_log(retro_meal)

    # Save an active meal plan item for today so 📋 projection renders on calendar
    plan = MealPlan(
        plan_id="plan_cal_test",
        person_id="person_marco",
        start_date=today,
        end_date=today,
        status="active",
        days=[
            MealPlanDay(
                plan_day_id="pday_cal_test",
                plan_id="plan_cal_test",
                plan_date=today,
                target_macros=Macros(
                    kcal=2200.0, protein_g=140.0, carbs_g=250.0, fat_g=70.0, fiber_g=31.0
                ),
                items=[
                    MealPlanItem(
                        plan_item_id="pitem_cal_test",
                        plan_day_id="pday_cal_test",
                        meal_slot="Cena",
                        title="Salmone al forno",
                        macros=Macros(
                            kcal=620, protein_g=42, carbs_g=45, fat_g=28, fiber_g=8.0
                        ),
                        status=PlanItemStatus.PLANNED,
                    )
                ],
            )
        ],
    )
    await runtime.plan_repo.save_plan(plan)

    now = dt_util.now()
    events = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": marco_cals[0].entity_id,
            "start_date_time": (now - timedelta(hours=24)).isoformat(),
            "end_date_time": (now + timedelta(hours=24)).isoformat(),
        },
        blocking=True,
        return_response=True,
    )
    cal_events = events[marco_cals[0].entity_id]["events"]
    assert any("Pranzo" in ev["summary"] for ev in cal_events)
    assert any("📊" in ev["summary"] for ev in cal_events)
    assert any("📋" in ev["summary"] and "Salmone al forno" in ev["summary"] for ev in cal_events)
    assert any("Fib" in ev.get("description", "") for ev in cal_events)
    retro_ev = next(ev for ev in cal_events if "Avena test" in ev["summary"])
    assert retro_ev["start"][11:16] == "08:00"

    # Log an off-plan Dinner -> planned '📋 [Piano - Cena]' disappears dynamically from calendar
    await hass.services.async_call(
        DOMAIN,
        "log_meal_item",
        {
            "person": "Marco",
            "food_id": "seed_chicken_breast_raw",
            "quantity": 150,
            "unit": "g",
            "state": "raw",
            "meal_slot": "Cena",
        },
        blocking=True,
    )
    await hass.async_block_till_done()
    events_after_offplan = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": marco_cals[0].entity_id,
            "start_date_time": (now - timedelta(hours=24)).isoformat(),
            "end_date_time": (now + timedelta(hours=24)).isoformat(),
        },
        blocking=True,
        return_response=True,
    )
    cal_after = events_after_offplan[marco_cals[0].entity_id]["events"]
    assert not any("Salmone al forno" in ev["summary"] for ev in cal_after)
    assert any("🍽️" in ev["summary"] and "Cena" in ev["summary"] for ev in cal_after)
