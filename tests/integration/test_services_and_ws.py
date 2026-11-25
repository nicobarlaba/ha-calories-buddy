"""Integration tests for HA services and authenticated WebSocket commands."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from homeassistant.core import Context, HomeAssistant
from pytest_homeassistant_custom_component.common import MockUser
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.ha_calories_buddy.const import (
    CONF_DISPLAY_NAME,
    CONF_HA_USER_ID,
    CONF_LANGUAGE_OVERRIDE,
    CONF_LLM_API_KEY,
    CONF_LLM_MODEL,
    CONF_LLM_PROVIDER,
    CONF_PERSON_ID,
    DOMAIN,
    LLM_PROVIDER_OLLAMA,
)
from tests.integration.conftest import setup_hub_and_persons


async def test_services_and_websocket_multi_user_isolation(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    hass_admin_user: MockUser,
) -> None:
    """Verify services, user_id WS routing, restore undo, Vault save, and zero secret leaks."""
    await setup_hub_and_persons(
        hass,
        hub_data={
            CONF_LLM_PROVIDER: LLM_PROVIDER_OLLAMA,
            CONF_LLM_MODEL: "qwen2.5:14b",
            CONF_LLM_API_KEY: "super-secret-do-not-leak",
        },
        persons=[
            {
                CONF_PERSON_ID: "person_admin",
                CONF_DISPLAY_NAME: "AdminUser",
                CONF_HA_USER_ID: hass_admin_user.id,
                CONF_LANGUAGE_OVERRIDE: "it",
            },
            {
                CONF_PERSON_ID: "person_giulia",
                CONF_DISPLAY_NAME: "Giulia",
                CONF_HA_USER_ID: "other_user_id",
                CONF_LANGUAGE_OVERRIDE: "en",
            },
        ],
    )

    # 1. Service log_meal_text via mocked Ollama response
    parsed_json = json.dumps(
        {
            "meal_slot": "Pranzo",
            "items": [
                {
                    "name": "lenticchie",
                    "quantity": 250,
                    "unit": "g",
                    "preparation_state": "unspecified",
                }
            ],
        }
    )
    with patch(
        "custom_components.ha_calories_buddy.core.llm.ollama_client.OllamaClient._raw_structured_completion",
        return_value=parsed_json,
    ):
        svc_resp = await hass.services.async_call(
            DOMAIN,
            "log_meal_text",
            {"text": "250g lenticchie"},
            context=Context(user_id=hass_admin_user.id),
            blocking=True,
            return_response=True,
        )
    assert svc_resp is not None
    assert svc_resp["needs_confirmation"] is True
    log_id = svc_resp["meal_log"]["log_id"]

    # Confirm pending meal via service
    conf_resp = await hass.services.async_call(
        DOMAIN,
        "confirm_meal",
        {"log_id": log_id},
        context=Context(user_id=hass_admin_user.id),
        blocking=True,
        return_response=True,
    )
    assert conf_resp is not None
    assert conf_resp["meal_log"]["status"] == "confirmed"

    # 2. WebSocket client automatically routes to hass_admin_user -> person_admin
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": f"{DOMAIN}/list_persons"})
    msg1 = await client.receive_json()
    assert msg1["success"] is True
    assert len(msg1["result"]["persons"]) == 2
    assert "super-secret-do-not-leak" not in json.dumps(msg1)

    await client.send_json({"id": 2, "type": f"{DOMAIN}/dashboard"})
    msg2 = await client.receive_json()
    assert msg2["success"] is True
    assert msg2["result"]["person"]["person_id"] == "person_admin"
    assert msg2["result"]["totals"]["confirmed_count"] == 1
    assert "super-secret-do-not-leak" not in json.dumps(msg2)

    # 3. WS soft delete & restore undo
    await client.send_json(
        {"id": 3, "type": f"{DOMAIN}/delete_meal", "log_id": log_id}
    )
    msg3 = await client.receive_json()
    assert msg3["success"] is True
    assert msg3["result"]["deleted"] is True

    await client.send_json(
        {"id": 4, "type": f"{DOMAIN}/restore_meal", "log_id": log_id}
    )
    msg4 = await client.receive_json()
    assert msg4["success"] is True
    assert msg4["result"]["restored"] is True

    # 4. WS save_food (global) and person override + log_weight
    await client.send_json(
        {
            "id": 5,
            "type": f"{DOMAIN}/save_food",
            "name": "Test Yogurt",
            "kcal_per_100g": 60,
            "protein_g_per_100g": 10,
            "carbs_g_per_100g": 4,
            "fat_g_per_100g": 0.5,
            "scope": "global",
        }
    )
    msg5 = await client.receive_json()
    assert msg5["success"] is True
    saved_food_id = msg5["result"]["food"]["food_id"]

    await client.send_json(
        {
            "id": 6,
            "type": f"{DOMAIN}/save_food",
            "food_id": saved_food_id,
            "name": "Test Yogurt",
            "preferred_alias": "Mio Yogurt",
            "kcal_per_100g": 65,
            "protein_g_per_100g": 10,
            "carbs_g_per_100g": 5,
            "fat_g_per_100g": 0.5,
            "scope": "person",
        }
    )
    msg6 = await client.receive_json()
    assert msg6["success"] is True
    assert msg6["result"]["food"]["has_override"] is True
    assert msg6["result"]["food"]["preferred_alias"] == "Mio Yogurt"

    # Giulia should NOT see AdminUser's personal override
    await client.send_json(
        {
            "id": 7,
            "type": f"{DOMAIN}/search_foods",
            "person_id": "person_giulia",
            "query": "Test Yogurt",
            "include_external": False,
            "limit": 10,
            "offset": 0,
        }
    )
    msg7 = await client.receive_json()
    assert msg7["success"] is True
    assert msg7["result"]["foods"][0]["has_override"] is False
    assert msg7["result"]["foods"][0]["macros_per_100g"]["kcal"] == 60.0
    assert msg7["result"]["total_count"] >= 1
    assert msg7["result"]["offset"] == 0
    assert msg7["result"]["limit"] == 10
    assert msg7["result"]["has_more"] is False
    assert "super-secret-do-not-leak" not in json.dumps(msg7)

    # Vault pagination & category group filter across seeded catalog
    await client.send_json(
        {
            "id": 70,
            "type": f"{DOMAIN}/search_foods",
            "person_id": "person_admin",
            "query": "",
            "category": "carbs",
            "include_external": False,
            "limit": 5,
            "offset": 0,
        }
    )
    msg70 = await client.receive_json()
    assert msg70["success"] is True
    assert len(msg70["result"]["foods"]) == 5
    assert msg70["result"]["total_count"] > 5
    assert msg70["result"]["has_more"] is True
    assert "super-secret-do-not-leak" not in json.dumps(msg70)

    # Update meal slot inline via WS
    await client.send_json(
        {
            "id": 71,
            "type": f"{DOMAIN}/update_meal_slot",
            "log_id": log_id,
            "meal_slot": "Cena",
        }
    )
    msg71 = await client.receive_json()
    assert msg71["success"] is True
    assert msg71["result"]["meal_log"]["meal_slot"] == "Cena"
    assert "super-secret-do-not-leak" not in json.dumps(msg71)

    await client.send_json(
        {"id": 72, "type": f"{DOMAIN}/log_weight", "weight_kg": 78.4}
    )
    msg8 = await client.receive_json()
    assert msg8["success"] is True
    assert msg8["result"]["snapshot"]["weight_kg"] == pytest.approx(78.4)
    assert "super-secret-do-not-leak" not in json.dumps(msg8)
