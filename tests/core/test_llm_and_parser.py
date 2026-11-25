"""Unit tests for LLM providers, Gemini schema sanitization, repair retries, and hybrid meal logging."""

from __future__ import annotations

import json
from pathlib import Path

import aiohttp
import pytest

from custom_components.ha_calories_buddy.core.llm.base import (
    LLMConnectionError,
    LLMSchemaValidationError,
)
from custom_components.ha_calories_buddy.core.llm.factory import create_llm_client
from custom_components.ha_calories_buddy.core.llm.gemini_client import (
    sanitize_gemini_schema,
)
from custom_components.ha_calories_buddy.core.llm.prompts.meal_parse import (
    ParsedMealResponseSchema,
)
from custom_components.ha_calories_buddy.core.models import (
    LLMConfig,
    MealSlotConfig,
    MealStatus,
    PersonProfile,
    PreparationState,
)
from custom_components.ha_calories_buddy.core.nutrition.resolver import FoodResolver
from custom_components.ha_calories_buddy.core.planner.meal_parser import (
    MealLoggingService,
)
from custom_components.ha_calories_buddy.core.storage.database import Database
from custom_components.ha_calories_buddy.core.storage.food_repo import FoodRepository
from custom_components.ha_calories_buddy.core.storage.meal_repo import MealRepository
from custom_components.ha_calories_buddy.core.storage.profile_repo import ProfileRepository
from custom_components.ha_calories_buddy.core.storage.seeder import seed_database_if_empty
from tests.core.http_mock import FakeClientSession


def test_gemini_schema_sanitizer_removes_unsupported_keys() -> None:
    """Verify Gemini sanitizer strips $defs/$ref/additionalProperties and handles nullables."""
    raw_schema = ParsedMealResponseSchema.model_json_schema()
    sanitized = sanitize_gemini_schema(raw_schema)

    serialized = json.dumps(sanitized)
    assert "$defs" not in serialized
    assert "$ref" not in serialized
    assert "additionalProperties" not in serialized
    assert '"title"' not in serialized

    item_props = sanitized["properties"]["items"]["items"]["properties"]
    assert item_props["brand"]["nullable"] is True
    assert item_props["brand"]["type"] == "STRING"
    assert "raw" in item_props["preparation_state"]["enum"]


async def test_all_four_llm_providers_and_repair_loop() -> None:
    """Verify OpenAI, Ollama, Anthropic, Gemini clients and schema repair retries."""
    valid_payload = {
        "meal_slot": "Lunch",
        "items": [
            {
                "name": "basmati rice",
                "brand": "Scotti",
                "quantity": 80,
                "unit": "g",
                "preparation_state": "raw",
            }
        ],
    }

    http = FakeClientSession()
    session = http.as_aiohttp_session()
    assert create_llm_client(LLMConfig(provider="none"), session) is None

    # 1. OpenAI-compatible: fallback from json_schema 400 -> json_object 200, plus malformed JSON repair
    http.add_post("https://api.openai.com/v1/chat/completions", status=400, body_text="bad fmt")
    http.add_post(
        "https://api.openai.com/v1/chat/completions",
        payload={"choices": [{"message": {"content": "{bad json"}}]},
    )
    http.add_post(
        "https://api.openai.com/v1/chat/completions",
        payload={
            "choices": [
                {
                    "message": {
                        "content": f"```json\n{json.dumps(valid_payload)}\n```"
                    }
                }
            ]
        },
    )
    http.add_get("https://api.openai.com/v1/models", status=200, payload={"data": []})
    openai_client = create_llm_client(
        LLMConfig(provider="openai", api_key="k", model="gpt-4o-mini"), session
    )
    assert openai_client is not None
    assert await openai_client.health_check() is True
    res_oai = await openai_client.generate_structured(
        "sys", "usr", ParsedMealResponseSchema
    )
    assert res_oai.items[0].brand == "Scotti"

    # 2. Ollama native /api/chat + health_check
    http.add_post(
        "http://localhost:11434/api/chat",
        payload={"message": {"content": json.dumps(valid_payload)}},
    )
    http.add_get(
        "http://localhost:11434/api/tags",
        payload={"models": [{"name": "qwen2.5:14b"}]},
    )
    ollama_client = create_llm_client(
        LLMConfig(provider="ollama", model="qwen2.5:14b"), session
    )
    assert ollama_client is not None
    assert await ollama_client.health_check() is True
    res_oll = await ollama_client.generate_structured(
        "sys", "usr", ParsedMealResponseSchema
    )
    assert res_oll.items[0].quantity == 80.0

    # 3. Anthropic forced tool output + health_check
    http.add_post(
        "https://api.anthropic.com/v1/messages",
        payload={"content": [{"type": "tool_use", "input": valid_payload}]},
    )
    http.add_get("https://api.anthropic.com/v1/models", status=200, payload={"data": []})
    anth_client = create_llm_client(
        LLMConfig(provider="anthropic", api_key="ak", model="claude-sonnet-4-5"),
        session,
    )
    assert anth_client is not None
    assert await anth_client.health_check() is True
    res_anth = await anth_client.generate_structured(
        "sys", "usr", ParsedMealResponseSchema
    )
    assert res_anth.meal_slot == "Lunch"

    # 4. Gemini generateContent + health_check
    http.add_post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
        payload={
            "candidates": [
                {"content": {"parts": [{"text": json.dumps(valid_payload)}]}}
            ]
        },
    )
    http.add_get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        status=200,
        payload={"models": []},
    )
    gem_client = create_llm_client(
        LLMConfig(provider="gemini", api_key="gk", model="gemini-2.5-flash"),
        session,
    )
    assert gem_client is not None
    assert await gem_client.health_check() is True
    res_gem = await gem_client.generate_structured(
        "sys", "usr", ParsedMealResponseSchema
    )
    assert res_gem.items[0].preparation_state == PreparationState.RAW

    # 5. Exhausted retries raise LLMSchemaValidationError
    http.add_post(
        "http://localhost:11434/api/chat",
        payload={"message": {"content": "still invalid"}},
        repeat=True,
    )
    with pytest.raises(LLMSchemaValidationError):
        await ollama_client.generate_structured(
            "sys", "usr", ParsedMealResponseSchema
        )

    # 6. Network errors raise LLMConnectionError
    http_err = FakeClientSession()
    http_err.add_post("http://localhost:11434/api/chat", exc=aiohttp.ClientError("down"))
    down_client = create_llm_client(
        LLMConfig(provider="ollama", model="qwen2.5:14b"), http_err.as_aiohttp_session()
    )
    assert down_client is not None
    with pytest.raises(LLMConnectionError):
        await down_client.generate_structured("sys", "usr", ParsedMealResponseSchema)


async def test_hybrid_meal_logging_and_offline_manual_flow(tmp_path: Path) -> None:
    """Verify confirmed vs pending hybrid status, edits, vault promotion, and offline logging."""
    db = Database(tmp_path / "meal_service.db")
    await db.initialize()
    try:
        await seed_database_if_empty(db)
        profile_repo = ProfileRepository(db)
        food_repo = FoodRepository(db)
        meal_repo = MealRepository(db)

        profile = PersonProfile(
            person_id="p_nico",
            config_entry_id="e_nico",
            display_name="Nico",
            language="it",
            meal_slots=[
                MealSlotConfig(slot_id="lunch", name="Pranzo", approx_time="13:00"),
                MealSlotConfig(slot_id="dinner", name="Cena", approx_time="20:00"),
            ],
        )
        await profile_repo.upsert_person(profile)

        http = FakeClientSession()
        llm_client = create_llm_client(
            LLMConfig(provider="ollama", model="qwen2.5:14b"), http.as_aiohttp_session()
        )
        resolver = FoodResolver(food_repo, off_provider=None)
        service = MealLoggingService(
            meal_repo=meal_repo,
            food_repo=food_repo,
            resolver=resolver,
            llm_client_getter=lambda _p: llm_client,
        )

        # Case A: Clear lunch from local seed -> immediate CONFIRMED
        high_conf_parse = {
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
        http.add_post(
            "http://localhost:11434/api/chat",
            payload={"message": {"content": json.dumps(high_conf_parse)}},
        )
        res_ok = await service.log_from_text(
            profile,
            "A pranzo 80g riso basmati, 1 cucchiaio olio di oliva, 250g lenticchie cotte",
        )
        assert res_ok.needs_confirmation is False
        assert res_ok.meal_log.status == MealStatus.CONFIRMED
        assert res_ok.meal_log.total_macros.kcal == pytest.approx(660.0, abs=1.0)
        assert "Pranzo registrato" in res_ok.summary_message

        # Update first item from 80g to 100g
        updated = await service.update_item_measurement(
            profile,
            res_ok.meal_log.log_id,
            res_ok.meal_log.items[0].item_id,
            quantity=100.0,
        )
        assert updated is not None
        assert updated.total_macros.kcal > res_ok.meal_log.total_macros.kcal

        # Case B: Unspecified lentils -> PENDING_CONFIRMATION
        ambiguous_parse = {
            "meal_slot": "Cena",
            "items": [
                {
                    "name": "lenticchie",
                    "quantity": 250,
                    "unit": "g",
                    "preparation_state": "unspecified",
                }
            ],
        }
        http.add_post(
            "http://localhost:11434/api/chat",
            payload={"message": {"content": json.dumps(ambiguous_parse)}},
        )
        res_amb = await service.log_from_text(profile, "A cena 250g lenticchie")
        assert res_amb.needs_confirmation is True
        assert res_amb.meal_log.status == MealStatus.PENDING_CONFIRMATION
        assert res_amb.warnings

        confirmed = await service.confirm_pending_meal(profile, res_amb.meal_log.log_id)
        assert confirmed is not None
        assert confirmed.status == MealStatus.CONFIRMED

        # Case C: LLM food estimation fallback + promote to Vault on confirmation
        est_parse = {
            "meal_slot": "Cena",
            "items": [
                {
                    "name": "Torta della Nonna Artigianale",
                    "quantity": 100,
                    "unit": "g",
                    "preparation_state": "ready_to_eat",
                }
            ],
        }
        est_food = {
            "name": "Torta della Nonna Artigianale",
            "category": "snack",
            "preparation_state": "ready_to_eat",
            "kcal_per_100g": 320.0,
            "protein_g_per_100g": 6.0,
            "carbs_g_per_100g": 42.0,
            "fat_g_per_100g": 14.0,
        }
        http.add_post(
            "http://localhost:11434/api/chat",
            payload={"message": {"content": json.dumps(est_parse)}},
        )
        http.add_post(
            "http://localhost:11434/api/chat",
            payload={"message": {"content": json.dumps(est_food)}},
        )
        res_est = await service.log_from_text(profile, "100g torta della nonna artigianale")
        assert res_est.needs_confirmation is True
        promoted = await service.confirm_pending_meal(
            profile, res_est.meal_log.log_id, promote_to_vault=True
        )
        assert promoted is not None
        assert promoted.status == MealStatus.CONFIRMED
        assert promoted.items[0].food_id is not None

        # Case D: Offline manual logging works when LLM is completely disabled
        offline_service = MealLoggingService(
            meal_repo=meal_repo,
            food_repo=food_repo,
            resolver=resolver,
            llm_client_getter=lambda _p: None,
        )
        manual_res = await offline_service.log_manual_item(
            profile,
            food_id="seed_greek_yogurt_0",
            quantity=150,
            unit="g",
            meal_slot="Pranzo",
            merge_existing_slot=False,
        )
        assert manual_res.meal_log.status == MealStatus.CONFIRMED
        assert manual_res.meal_log.total_macros.protein_g == pytest.approx(15.5, abs=0.2)
    finally:
        await db.close()
