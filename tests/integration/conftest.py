"""Shared fixtures for ha-calories-buddy Home Assistant integration tests."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_calories_buddy.const import (
    CONF_ACTIVITY_LEVEL,
    CONF_BIRTH_YEAR,
    CONF_DISPLAY_NAME,
    CONF_GOAL_TYPE,
    CONF_HA_PERSON_ENTITY,
    CONF_HA_USER_ID,
    CONF_HEIGHT_CM,
    CONF_INITIAL_WEIGHT_KG,
    CONF_LANGUAGE,
    CONF_LANGUAGE_OVERRIDE,
    CONF_LLM_PROVIDER,
    CONF_MEAL_SLOTS,
    CONF_PERSON_ID,
    CONF_SEX,
    CONF_TARGET_MODE,
    CONF_TDEE_ENGINE,
    CONF_WEEKLY_RATE_KG,
    CONF_WEIGHT_ENTITY,
    DOMAIN,
    ENTRY_KIND,
    ENTRY_KIND_HUB,
    ENTRY_KIND_PERSON,
    LLM_PROVIDER_NONE,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Allow Home Assistant test harness to load custom_components/ha_calories_buddy."""
    yield


@pytest.fixture(autouse=True)
def isolate_integration_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[None]:
    """Give every integration test an isolated SQLite database under tmp_path."""
    monkeypatch.setattr(
        "custom_components.ha_calories_buddy.STORAGE_DB_FILENAME",
        str(tmp_path / "ha_calories_buddy.db"),
    )
    yield


@pytest.fixture
def mock_llm_health_check() -> Generator[None]:
    """Patch LLM health_check to succeed during ConfigFlow tests."""
    with patch(
        "custom_components.ha_calories_buddy.core.llm.ollama_client.OllamaClient.health_check",
        return_value=True,
    ):
        yield


async def setup_hub_and_persons(
    hass: HomeAssistant,
    *,
    hub_data: dict[str, object] | None = None,
    persons: list[dict[str, object]] | None = None,
) -> tuple[MockConfigEntry, list[MockConfigEntry]]:
    """Create and set up a Hub ConfigEntry plus 1..N Person ConfigEntries."""
    hub_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Calories Buddy — Household Hub",
        unique_id="hub",
        data={
            ENTRY_KIND: ENTRY_KIND_HUB,
            CONF_LANGUAGE: "en",
            CONF_LLM_PROVIDER: LLM_PROVIDER_NONE,
            **(hub_data or {}),
        },
    )
    hub_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(hub_entry.entry_id)

    person_entries: list[MockConfigEntry] = []
    default_persons = persons or [
        {
            CONF_PERSON_ID: "person_marco",
            CONF_DISPLAY_NAME: "Marco",
            CONF_HA_PERSON_ENTITY: "person.marco",
            CONF_HA_USER_ID: "uid_marco",
            CONF_LANGUAGE_OVERRIDE: "it",
            CONF_SEX: "male",
            CONF_BIRTH_YEAR: 1990,
            CONF_HEIGHT_CM: 180.0,
            CONF_INITIAL_WEIGHT_KG: 80.0,
            CONF_TARGET_MODE: "physiology",
            CONF_GOAL_TYPE: "lose_weight",
            CONF_WEEKLY_RATE_KG: -0.5,
            CONF_TDEE_ENGINE: "static",
            CONF_ACTIVITY_LEVEL: "moderate",
            CONF_MEAL_SLOTS: "Colazione, Pranzo, Spuntino, Cena",
        }
    ]
    for pdata in default_persons:
        pid = str(pdata[CONF_PERSON_ID])
        name = str(pdata[CONF_DISPLAY_NAME])
        p_entry = MockConfigEntry(
            domain=DOMAIN,
            title=f"Calories Buddy — {name}",
            unique_id=pid,
            data={
                ENTRY_KIND: ENTRY_KIND_PERSON,
                CONF_WEIGHT_ENTITY: None,
                **pdata,
            },
        )
        p_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(p_entry.entry_id)
        person_entries.append(p_entry)

    await hass.async_block_till_done()
    return hub_entry, person_entries
