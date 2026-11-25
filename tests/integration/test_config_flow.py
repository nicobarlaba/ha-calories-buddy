"""ConfigFlow and OptionsFlow integration tests."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ha_calories_buddy.const import (
    CONF_ACTIVITY_LEVEL,
    CONF_AI_TASK_ENTITY,
    CONF_BIRTH_YEAR,
    CONF_DISPLAY_NAME,
    CONF_GOAL_TYPE,
    CONF_HA_PERSON_ENTITY,
    CONF_HA_USER_ID,
    CONF_HEIGHT_CM,
    CONF_INITIAL_WEIGHT_KG,
    CONF_LANGUAGE,
    CONF_LANGUAGE_OVERRIDE,
    CONF_LLM_MODEL,
    CONF_LLM_OVERRIDE_ENABLED,
    CONF_LLM_PROVIDER,
    CONF_LLM_TIMEOUT_S,
    CONF_MANUAL_CARBS_G,
    CONF_MANUAL_FAT_G,
    CONF_MANUAL_KCAL,
    CONF_MANUAL_PROTEIN_G,
    CONF_MEAL_SLOTS,
    CONF_SEX,
    CONF_TARGET_MODE,
    CONF_TDEE_ENGINE,
    CONF_WEARABLE_CORRECTION_FACTOR,
    CONF_WEEKLY_RATE_KG,
    DOMAIN,
    ENTRY_KIND,
    ENTRY_KIND_HUB,
    ENTRY_KIND_PERSON,
    LLM_PROVIDER_HA_AI_TASK,
    LLM_PROVIDER_NONE,
    LLM_PROVIDER_OLLAMA,
)


async def test_hub_then_two_persons_config_flow(
    hass: HomeAssistant, mock_llm_health_check: None
) -> None:
    """Verify first flow creates Hub, subsequent flows create independent Person entries."""
    # Pre-create person.marco state with linked user_id
    hass.states.async_set(
        "person.marco",
        "home",
        {"friendly_name": "Marco", "user_id": "ha_uid_marco"},
    )

    # 1. First run creates the singleton Hub (2-step wizard: hub -> hub_llm)
    res1 = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert res1["type"] is FlowResultType.FORM
    assert res1["step_id"] == "hub"

    res1_llm = await hass.config_entries.flow.async_configure(
        res1["flow_id"],
        {
            CONF_LANGUAGE: "it",
            CONF_LLM_PROVIDER: LLM_PROVIDER_OLLAMA,
        },
    )
    assert res1_llm["type"] is FlowResultType.FORM
    assert res1_llm["step_id"] == "hub_llm"

    res1_done = await hass.config_entries.flow.async_configure(
        res1["flow_id"],
        {
            CONF_LLM_MODEL: "qwen2.5:14b",
            CONF_LLM_TIMEOUT_S: 180.0,
        },
    )
    assert res1_done["type"] is FlowResultType.CREATE_ENTRY
    assert res1_done["data"][ENTRY_KIND] == ENTRY_KIND_HUB

    # 2. Second run creates Person 1 (Marco, physiology mode)
    res2 = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert res2["type"] is FlowResultType.FORM
    assert res2["step_id"] == "person_identity"

    res2_bio = await hass.config_entries.flow.async_configure(
        res2["flow_id"],
        {
            CONF_HA_PERSON_ENTITY: "person.marco",
            CONF_LANGUAGE_OVERRIDE: "inherit",
        },
    )
    assert res2_bio["step_id"] == "person_biometrics"

    res2_ent = await hass.config_entries.flow.async_configure(
        res2["flow_id"],
        {
            CONF_SEX: "male",
            CONF_BIRTH_YEAR: 1990,
            CONF_HEIGHT_CM: 180.0,
            CONF_INITIAL_WEIGHT_KG: 80.0,
            CONF_TARGET_MODE: "physiology",
            CONF_GOAL_TYPE: "lose_weight",
            CONF_WEEKLY_RATE_KG: -0.4,
            CONF_TDEE_ENGINE: "wearable_dynamic",
            CONF_ACTIVITY_LEVEL: "moderate",
            CONF_WEARABLE_CORRECTION_FACTOR: 0.9,
        },
    )
    assert res2_ent["step_id"] == "person_entities"

    res2_llm = await hass.config_entries.flow.async_configure(res2["flow_id"], {})
    assert res2_llm["step_id"] == "person_llm"

    res2_diet = await hass.config_entries.flow.async_configure(
        res2["flow_id"],
        {CONF_LLM_OVERRIDE_ENABLED: False, CONF_LLM_PROVIDER: LLM_PROVIDER_NONE},
    )
    assert res2_diet["step_id"] == "person_diet"

    res2_done = await hass.config_entries.flow.async_configure(
        res2["flow_id"],
        {CONF_MEAL_SLOTS: "Colazione, Pranzo, Cena"},
    )
    assert res2_done["type"] is FlowResultType.CREATE_ENTRY
    assert res2_done["data"][ENTRY_KIND] == ENTRY_KIND_PERSON
    assert res2_done["data"][CONF_DISPLAY_NAME] == "Marco"
    assert res2_done["data"][CONF_HA_USER_ID] == "ha_uid_marco"

    # 3. Third run creates Person 2 (Giulia, manual mode)
    res3 = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    res3_bio = await hass.config_entries.flow.async_configure(
        res3["flow_id"],
        {CONF_DISPLAY_NAME: "Giulia", CONF_HA_USER_ID: "ha_uid_giulia"},
    )
    assert res3_bio["step_id"] == "person_biometrics"

    res3_man = await hass.config_entries.flow.async_configure(
        res3["flow_id"],
        {
            CONF_SEX: "female",
            CONF_BIRTH_YEAR: 1993,
            CONF_HEIGHT_CM: 165.0,
            CONF_INITIAL_WEIGHT_KG: 58.0,
            CONF_TARGET_MODE: "manual",
            CONF_GOAL_TYPE: "maintain",
            CONF_WEEKLY_RATE_KG: 0.0,
            CONF_TDEE_ENGINE: "static",
            CONF_ACTIVITY_LEVEL: "light",
            CONF_WEARABLE_CORRECTION_FACTOR: 1.0,
        },
    )
    assert res3_man["step_id"] == "person_manual_targets"

    res3_ent = await hass.config_entries.flow.async_configure(
        res3["flow_id"],
        {
            CONF_MANUAL_KCAL: 1850.0,
            CONF_MANUAL_PROTEIN_G: 120.0,
            CONF_MANUAL_CARBS_G: 200.0,
            CONF_MANUAL_FAT_G: 60.0,
        },
    )
    assert res3_ent["step_id"] == "person_entities"

    await hass.config_entries.flow.async_configure(res3["flow_id"], {})
    await hass.config_entries.flow.async_configure(
        res3["flow_id"],
        {CONF_LLM_OVERRIDE_ENABLED: False, CONF_LLM_PROVIDER: LLM_PROVIDER_NONE},
    )
    res3_done = await hass.config_entries.flow.async_configure(res3["flow_id"], {})
    assert res3_done["type"] is FlowResultType.CREATE_ENTRY
    assert res3_done["data"][CONF_DISPLAY_NAME] == "Giulia"

    # OptionsFlow works for both Hub and Person (switching Ollama -> HA AI Task & None)
    hub_entry = res1_done["result"]
    hass.states.async_set("ai_task.google_ai_task_3_8", "unknown")
    hass.services.async_register("ai_task", "generate_data", lambda _c: {"data": {}})

    opt_hub = await hass.config_entries.options.async_init(hub_entry.entry_id)
    assert opt_hub["step_id"] == "hub_options"
    opt_hub_llm = await hass.config_entries.options.async_configure(
        opt_hub["flow_id"],
        {CONF_LANGUAGE: "it", CONF_LLM_PROVIDER: LLM_PROVIDER_HA_AI_TASK},
    )
    assert opt_hub_llm["step_id"] == "hub_options_llm"
    schema_keys = {str(k) for k in opt_hub_llm["data_schema"].schema}
    assert CONF_AI_TASK_ENTITY in schema_keys
    assert "llm_base_url" not in schema_keys
    assert CONF_LLM_MODEL not in schema_keys

    opt_hub_ai_done = await hass.config_entries.options.async_configure(
        opt_hub["flow_id"],
        {CONF_AI_TASK_ENTITY: "ai_task.google_ai_task_3_8"},
    )
    assert opt_hub_ai_done["type"] is FlowResultType.CREATE_ENTRY
    assert opt_hub_ai_done["data"][CONF_LLM_PROVIDER] == LLM_PROVIDER_HA_AI_TASK
    assert opt_hub_ai_done["data"][CONF_AI_TASK_ENTITY] == "ai_task.google_ai_task_3_8"

    opt_none = await hass.config_entries.options.async_init(hub_entry.entry_id)
    opt_none_done = await hass.config_entries.options.async_configure(
        opt_none["flow_id"],
        {CONF_LANGUAGE: "en", CONF_LLM_PROVIDER: LLM_PROVIDER_NONE},
    )
    assert opt_none_done["type"] is FlowResultType.CREATE_ENTRY

    person_entry = res3_done["result"]
    opt_person = await hass.config_entries.options.async_init(person_entry.entry_id)
    assert opt_person["type"] is FlowResultType.MENU
