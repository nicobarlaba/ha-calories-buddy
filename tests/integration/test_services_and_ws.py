"""Integration tests for HA services and authenticated WebSocket commands."""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util
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
    EVENT_DATA_UPDATED,
    LLM_PROVIDER_OLLAMA,
)
from custom_components.ha_calories_buddy.core.models import (
    Macros as CoreMacros,
)
from custom_components.ha_calories_buddy.core.models import (
    MealPlan as CoreMealPlan,
)
from custom_components.ha_calories_buddy.core.models import (
    MealPlanDay as CoreMealPlanDay,
)
from custom_components.ha_calories_buddy.core.models import (
    MealPlanItem as CoreMealPlanItem,
)
from custom_components.ha_calories_buddy.core.models import (
    PlanItemStatus,
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

    events_received: list[dict[str, object]] = []
    unsub_events = hass.bus.async_listen(
        EVENT_DATA_UPDATED, lambda ev: events_received.append(dict(ev.data))
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
    await hass.async_block_till_done()
    assert svc_resp is not None
    assert svc_resp["needs_confirmation"] is True
    assert any(e.get("person_id") == "person_admin" for e in events_received)
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
    unsub_events()

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

    # 5. WS update_meal_item with Vault food replacement + WS/service add & delete_meal_item
    await client.send_json(
        {
            "id": 73,
            "type": f"{DOMAIN}/log_item",
            "food_id": "seed_olive_oil",
            "quantity": 10,
            "unit": "g",
            "state": "ready_to_eat",
            "meal_slot": "Spuntino",
        }
    )
    msg73 = await client.receive_json()
    assert msg73["success"] is True
    extra_log = msg73["result"]["meal_log"]
    extra_item_id = extra_log["items"][0]["item_id"]

    # Add a 2-item meal via AI parse so deleting 1 item keeps the meal alive
    two_item_json = json.dumps(
        {
            "meal_slot": "Cena",
            "items": [
                {"name": "pane integrale", "quantity": 70, "unit": "g", "preparation_state": "ready_to_eat"},
                {"name": "banana", "quantity": 120, "unit": "g", "preparation_state": "ready_to_eat"},
            ],
        }
    )
    with patch(
        "custom_components.ha_calories_buddy.core.llm.ollama_client.OllamaClient._raw_structured_completion",
        return_value=two_item_json,
    ):
        await client.send_json(
            {"id": 74, "type": f"{DOMAIN}/log_text", "text": "70g pane integrale e 1 banana"}
        )
        msg74 = await client.receive_json()
    assert msg74["success"] is True
    combo_log = msg74["result"]["meal_log"]
    combo_log_id = combo_log["log_id"]
    bread_item_id = combo_log["items"][0]["item_id"]
    banana_item_id = combo_log["items"][1]["item_id"]

    # Replace bread item with white bread via WS update_meal_item(food_id=...)
    await client.send_json(
        {
            "id": 75,
            "type": f"{DOMAIN}/update_meal_item",
            "log_id": combo_log_id,
            "item_id": bread_item_id,
            "quantity": 80,
            "unit": "g",
            "food_id": "seed_white_bread",
        }
    )
    msg75 = await client.receive_json()
    assert msg75["success"] is True
    swapped_item = next(i for i in msg75["result"]["meal_log"]["items"] if i["item_id"] == bread_item_id)
    assert swapped_item["food_id"] == "seed_white_bread"

    # Delete the unwanted extra banana item via WS delete_meal_item
    await client.send_json(
        {
            "id": 76,
            "type": f"{DOMAIN}/delete_meal_item",
            "log_id": combo_log_id,
            "item_id": banana_item_id,
        }
    )
    msg76 = await client.receive_json()
    assert msg76["success"] is True
    assert msg76["result"]["meal_deleted"] is False
    assert len(msg76["result"]["meal_log"]["items"]) == 1

    # Delete sole remaining item from extra_log via HA service -> parent meal soft-deleted
    del_svc = await hass.services.async_call(
        DOMAIN,
        "delete_meal_item",
        {"log_id": extra_log["log_id"], "item_id": extra_item_id},
        context=Context(user_id=hass_admin_user.id),
        blocking=True,
        return_response=True,
    )
    assert del_svc is not None
    assert del_svc["meal_deleted"] is True
    assert del_svc["meal_log"]["status"] == "deleted"

    # 6. Add forgotten ingredient via WS add_meal_item, HA service add_meal_item & same-slot merge
    await client.send_json(
        {
            "id": 77,
            "type": f"{DOMAIN}/add_meal_item",
            "log_id": combo_log_id,
            "food_id": "seed_olive_oil",
            "quantity": 10,
            "unit": "g",
            "state": "ready_to_eat",
        }
    )
    msg77 = await client.receive_json()
    assert msg77["success"] is True
    assert len(msg77["result"]["meal_log"]["items"]) == 2
    kcal_after_ws_add = msg77["result"]["meal_log"]["total_macros"]["kcal"]

    add_svc = await hass.services.async_call(
        DOMAIN,
        "add_meal_item",
        {
            "log_id": combo_log_id,
            "food_id": "seed_banana",
            "quantity": 100,
            "unit": "g",
            "state": "ready_to_eat",
        },
        context=Context(user_id=hass_admin_user.id),
        blocking=True,
        return_response=True,
    )
    assert add_svc is not None
    assert len(add_svc["meal_log"]["items"]) == 3
    assert add_svc["meal_log"]["total_macros"]["kcal"] > kcal_after_ws_add

    # Manual log_item on an already-confirmed slot merges automatically into that slot's MealLog
    await client.send_json(
        {
            "id": 78,
            "type": f"{DOMAIN}/log_item",
            "food_id": "seed_almonds",
            "quantity": 15,
            "unit": "g",
            "state": "ready_to_eat",
            "meal_slot": "Cena",
        }
    )
    msg78 = await client.receive_json()
    assert msg78["success"] is True
    assert msg78["result"]["meal_log"]["log_id"] == combo_log_id
    assert len(msg78["result"]["meal_log"]["items"]) == 4

    # 7. Dashboard exposes structured biometrics fallback when no sensors are mapped
    await client.send_json({"id": 79, "type": f"{DOMAIN}/dashboard"})
    msg79 = await client.receive_json()
    assert msg79["success"] is True
    bio = msg79["result"]["biometrics"]
    assert bio["weight_kg"] == pytest.approx(78.4)
    assert bio["weight_entity_id"] is None
    assert bio["weight_sensor_available"] is False

    # 8. v0.2.6 Homemade recipes WS & service CRUD + dynamic covered_by_log sync
    await client.send_json(
        {
            "id": 80,
            "type": f"{DOMAIN}/save_recipe",
            "name": "Ciambellone integrale",
            "category": "snack",
            "servings": 10,
            "serving_unit": "slice",
            "final_weight_g": 800,
            "allow_in_planner": False,
            "notes": "Cotto 35 min",
            "ingredients": [
                {"food_id": "seed_oats_raw", "name": "Fiocchi d'avena", "quantity": 300, "unit": "g", "state": "raw"},
                {"food_id": "seed_banana", "name": "Banana", "quantity": 250, "unit": "g", "state": "ready_to_eat"},
            ],
        }
    )
    msg80 = await client.receive_json()
    assert msg80["success"] is True
    rec = msg80["result"]["recipe"]
    assert rec["grams_per_serving"] == pytest.approx(80.0, abs=0.1)
    assert rec["macros_per_serving"]["kcal"] > 0

    await client.send_json({"id": 81, "type": f"{DOMAIN}/list_recipes", "query": "ciambellone"})
    msg81 = await client.receive_json()
    assert msg81["success"] is True
    assert msg81["result"]["total_count"] == 1

    # Save an active plan item on "Colazione" (uncovered) and verify covered_by_log toggles cleanly
    today_d = dt_util.now().date()
    runtime = hass.data[DOMAIN]
    await runtime.plan_repo.save_plan(
        CoreMealPlan(
            plan_id="plan_cov_test",
            person_id="person_admin",
            start_date=today_d,
            end_date=today_d + timedelta(days=1),
            status="active",
            days=[
                CoreMealPlanDay(
                    plan_day_id="day_cov_0",
                    plan_id="plan_cov_test",
                    plan_date=today_d,
                    target_macros=CoreMacros(kcal=2000, protein_g=140, carbs_g=220, fat_g=60, fiber_g=28),
                    items=[
                        CoreMealPlanItem(
                            plan_item_id="pitem_cov_col",
                            plan_day_id="day_cov_0",
                            meal_slot="Colazione",
                            title="Porridge pianificato",
                            macros=CoreMacros(kcal=400, protein_g=25, carbs_g=50, fat_g=10, fiber_g=6),
                            status=PlanItemStatus.PLANNED,
                        )
                    ],
                )
            ],
        )
    )

    await client.send_json({"id": 82, "type": f"{DOMAIN}/get_plan"})
    msg82 = await client.receive_json()
    assert msg82["success"] is True
    assert msg82["result"]["plan"]["days"][0]["items"][0]["covered_by_log"] is False

    # Log an off-plan Colazione item -> dashboard & get_plan report covered_by_log = True
    await client.send_json(
        {
            "id": 83,
            "type": f"{DOMAIN}/log_item",
            "food_id": rec["food_id"],
            "quantity": 1,
            "unit": "slice",
            "state": "ready_to_eat",
            "meal_slot": "Colazione",
        }
    )
    msg83 = await client.receive_json()
    assert msg83["success"] is True
    off_col_log_id = msg83["result"]["meal_log"]["log_id"]

    await client.send_json({"id": 84, "type": f"{DOMAIN}/dashboard"})
    msg84 = await client.receive_json()
    assert msg84["success"] is True
    assert msg84["result"]["planned_today"][0]["covered_by_log"] is True

    # Delete off-plan Colazione -> covered_by_log flips back to False automatically
    await client.send_json({"id": 85, "type": f"{DOMAIN}/delete_meal", "log_id": off_col_log_id})
    msg85 = await client.receive_json()
    assert msg85["success"] is True

    await client.send_json({"id": 86, "type": f"{DOMAIN}/get_plan"})
    msg86 = await client.receive_json()
    assert msg86["success"] is True
    assert msg86["result"]["plan"]["days"][0]["items"][0]["covered_by_log"] is False

    # HA service save_recipe & delete_recipe
    svc_rec = await hass.services.async_call(
        DOMAIN,
        "save_recipe",
        {
            "name": "Porridge espresso",
            "servings": 1,
            "ingredients": [{"food_id": "seed_oats_raw", "name": "Fiocchi d'avena", "quantity": 60, "unit": "g"}],
        },
        context=Context(user_id=hass_admin_user.id),
        blocking=True,
        return_response=True,
    )
    assert svc_rec is not None
    svc_rec_id = svc_rec["recipe"]["recipe_id"]

    del_rec = await hass.services.async_call(
        DOMAIN,
        "delete_recipe",
        {"recipe_id": svc_rec_id},
        context=Context(user_id=hass_admin_user.id),
        blocking=True,
        return_response=True,
    )
    assert del_rec == {"deleted": True}

    await client.send_json({"id": 87, "type": f"{DOMAIN}/delete_recipe", "recipe_id": rec["recipe_id"]})
    msg87 = await client.receive_json()
    assert msg87["success"] is True
    assert msg87["result"]["deleted"] is True
