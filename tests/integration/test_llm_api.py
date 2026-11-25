"""Integration tests for Home Assistant Assist llm.API tools."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import llm

from custom_components.ha_calories_buddy.const import (
    CONF_DISPLAY_NAME,
    CONF_HA_USER_ID,
    CONF_LANGUAGE_OVERRIDE,
    CONF_LLM_MODEL,
    CONF_LLM_PROVIDER,
    CONF_PERSON_ID,
    DOMAIN,
    LLM_PROVIDER_OLLAMA,
)
from tests.integration.conftest import setup_hub_and_persons


async def test_assist_llm_api_tools(hass: HomeAssistant) -> None:
    """Verify Assist llm.API registers 7 tools returning ToolResult cleanly."""
    await setup_hub_and_persons(
        hass,
        hub_data={
            CONF_LLM_PROVIDER: LLM_PROVIDER_OLLAMA,
            CONF_LLM_MODEL: "qwen2.5:14b",
        },
        persons=[
            {
                CONF_PERSON_ID: "person_marco",
                CONF_DISPLAY_NAME: "Marco",
                CONF_HA_USER_ID: "uid_marco",
                CONF_LANGUAGE_OVERRIDE: "it",
            },
            {
                CONF_PERSON_ID: "person_giulia",
                CONF_DISPLAY_NAME: "Giulia",
                CONF_HA_USER_ID: "uid_giulia",
                CONF_LANGUAGE_OVERRIDE: "en",
            },
        ],
    )

    llm_ctx = llm.LLMContext(
        platform="conversation",
        context=Context(user_id="uid_marco"),
        language="it",
        assistant="conversation",
        device_id=None,
    )
    api_instance = await llm.async_get_api(hass, DOMAIN, llm_ctx)
    tool_names = {tool.name for tool in api_instance.tools}
    assert tool_names == {
        "log_meal",
        "correct_last_meal",
        "get_today_nutrition_summary",
        "suggest_meal",
        "generate_meal_plan",
        "swap_plan_meal",
        "rebalance_today",
    }

    parsed_lunch = json.dumps(
        {
            "meal_slot": "Pranzo",
            "items": [
                {
                    "name": "riso basmati",
                    "quantity": 80,
                    "unit": "g",
                    "preparation_state": "raw",
                },
                {
                    "name": "olio di oliva",
                    "quantity": 1,
                    "unit": "tbsp",
                    "preparation_state": "ready_to_eat",
                },
                {
                    "name": "lenticchie cotte",
                    "quantity": 250,
                    "unit": "g",
                    "preparation_state": "cooked",
                },
            ],
        }
    )
    fake_conv = SimpleNamespace(
        ConversationTraceEventType=SimpleNamespace(TOOL_CALL="tool_call"),
        async_conversation_trace_append=lambda *_args, **_kwargs: None,
    )
    with (
        patch.dict(sys.modules, {"homeassistant.components.conversation": fake_conv}),
        patch(
            "custom_components.ha_calories_buddy.core.llm.ollama_client.OllamaClient._raw_structured_completion",
            return_value=parsed_lunch,
        ),
    ):
        log_res = await api_instance.async_call_tool(
            llm.ToolInput(
                tool_name="log_meal",
                tool_args={
                    "text": "A pranzo 80g riso basmati, 1 cucchiaio olio di oliva, 250g lenticchie cotte"
                },
            )
        )

        assert isinstance(log_res, llm.ToolResult)
        assert log_res.error is False
        assert log_res.data["person"] == "Marco"
        assert log_res.data["meal_slot"] == "Pranzo"
        assert log_res.data["status"] == "confirmed"
        assert log_res.data["kcal"] == pytest.approx(660.0, abs=1.0)
        assert float(log_res.data["fiber_g"]) > 0.0

        # Correct lentils from 250g to 150g AND move meal_slot to Cena via Assist tool
        corr_res = await api_instance.async_call_tool(
            llm.ToolInput(
                tool_name="correct_last_meal",
                tool_args={
                    "item_name": "Lenticchie",
                    "new_quantity": 150,
                    "new_meal_slot": "Cena",
                },
            )
        )
        assert corr_res.error is False
        assert corr_res.data["meal_slot"] == "Cena"
        assert corr_res.data["new_meal_kcal"] == pytest.approx(544.0, abs=1.0)

        # Replace Basmati Rice with Pasta and remove Lentils via CorrectLastMealTool
        swap_res = await api_instance.async_call_tool(
            llm.ToolInput(
                tool_name="correct_last_meal",
                tool_args={
                    "item_name": "Riso Basmati",
                    "replacement_food_name": "Pasta di Semola (Cruda)",
                },
            )
        )
        assert swap_res.error is False
        assert float(swap_res.data["new_meal_kcal"]) > 500.0

        remove_res = await api_instance.async_call_tool(
            llm.ToolInput(
                tool_name="correct_last_meal",
                tool_args={
                    "item_name": "Lenticchie",
                    "remove_item": True,
                },
            )
        )
        assert remove_res.error is False
        assert remove_res.data["removed"] is True
        assert remove_res.data["meal_deleted"] is False
        assert float(remove_res.data["new_meal_kcal"]) < 420.0

        # Query today summary including fiber metrics
        summary_res = await api_instance.async_call_tool(
            llm.ToolInput(tool_name="get_today_nutrition_summary", tool_args={})
        )
        assert summary_res.error is False
        assert summary_res.data["person"] == "Marco"
        assert summary_res.data["consumed_kcal"] == pytest.approx(
            float(remove_res.data["new_meal_kcal"]), abs=1.0
        )
        assert float(summary_res.data["fiber_consumed_g"]) > 0.0
        assert float(summary_res.data["fiber_target_g"]) >= 20.0

        # Shared satellite without user_id -> returns clean ambiguity error
        anon_ctx = llm.LLMContext(
            platform="conversation",
            context=Context(user_id=None),
            language="it",
            assistant="conversation",
            device_id=None,
        )
        anon_api = await llm.async_get_api(hass, DOMAIN, anon_ctx)
        amb_res = await anon_api.async_call_tool(
            llm.ToolInput(tool_name="get_today_nutrition_summary", tool_args={})
        )
        assert amb_res.error is True
        assert "Multiple person profiles" in str(amb_res.data["error"])
