"""Integration tests for sensors, biometric fallback, unit conversion, and calendars."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

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
) -> None:
    """Verify 9 sensors + 1 calendar per person, lb->kg conversion, unavailable fallback, and colors."""
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
    giulia_sensors = [e for e in giulia_entities if e.domain == "sensor"]
    giulia_cals = [e for e in giulia_entities if e.domain == "calendar"]

    assert len(marco_sensors) == 9
    assert len(giulia_sensors) == 9
    assert len(marco_cals) == 1
    assert len(giulia_cals) == 1

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

    # Persist today's snapshot, then simulate weight sensor becoming unavailable -> retains 80.0 kg!
    runtime: DomainRuntime = hass.data[DOMAIN]
    await runtime.persons["person_marco"].coordinator.async_persist_today_snapshot()
    hass.states.async_set("sensor.marco_scale", "unavailable")
    await runtime.persons["person_marco"].coordinator.async_refresh()
    await hass.async_block_till_done()

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

    now = dt_util.now()
    events = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": marco_cals[0].entity_id,
            "start_date_time": (now - timedelta(hours=12)).isoformat(),
            "end_date_time": (now + timedelta(hours=12)).isoformat(),
        },
        blocking=True,
        return_response=True,
    )
    cal_events = events[marco_cals[0].entity_id]["events"]
    assert any("Pranzo" in ev["summary"] for ev in cal_events)
    assert any("📊" in ev["summary"] for ev in cal_events)
