"""Integration tests for Home Assistant AI Task LLM, ConfigFlow, and Phase 2 Plan/Rebalance/Preference flows."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.ha_calories_buddy import _extract_llm_config
from custom_components.ha_calories_buddy.const import (
    CONF_ACTIVITY_LEVEL,
    CONF_AI_TASK_ENTITY,
    CONF_BIRTH_YEAR,
    CONF_DISPLAY_NAME,
    CONF_GOAL_TYPE,
    CONF_HA_USER_ID,
    CONF_HEIGHT_CM,
    CONF_INITIAL_WEIGHT_KG,
    CONF_LANGUAGE,
    CONF_LANGUAGE_OVERRIDE,
    CONF_LLM_PROVIDER,
    CONF_LLM_TIMEOUT_S,
    CONF_MEAL_SLOTS,
    CONF_PERSON_ID,
    CONF_SEX,
    CONF_TARGET_MODE,
    CONF_TDEE_ENGINE,
    DOMAIN,
    ENTRY_KIND,
    ENTRY_KIND_HUB,
    ENTRY_KIND_PERSON,
    LLM_PROVIDER_HA_AI_TASK,
    LLM_PROVIDER_NONE,
    LLM_PROVIDER_OLLAMA,
    LLM_PROVIDER_OPENAI,
)
from custom_components.ha_calories_buddy.core.llm.prompts.meal_plan import (
    DayPlanProposalSchema,
    MealSuggestionListSchema,
    PlannedIngredientSpec,
    PlannedMealSkeleton,
    SingleMealSwapSchema,
)
from custom_components.ha_calories_buddy.core.models import (
    IngredientRole,
    LLMConfig,
    PreparationState,
)
from custom_components.ha_calories_buddy.ha_llm_client import HAAITaskLLMClient
from tests.integration.conftest import setup_hub_and_persons


def _install_fake_ai_task(
    hass: HomeAssistant,
    queue_by_task: dict[str, list[Any]],
) -> list[dict[str, Any]]:
    """Register a mocked ai_task.generate_data service on hass recording calls."""
    calls: list[dict[str, Any]] = []

    async def _handle_generate_data(call: ServiceCall) -> dict[str, Any]:
        payload = dict(call.data)
        calls.append(payload)
        tname = str(payload.get("task_name") or "")
        q = queue_by_task.get(tname) or queue_by_task.get("*") or []
        val = q.pop(0) if q else {}
        return {"data": val}

    hass.services.async_register(
        "ai_task",
        "generate_data",
        _handle_generate_data,
        supports_response=SupportsResponse.ONLY,
    )
    return calls


async def test_ha_ai_task_client_and_hub_config_flow(hass: HomeAssistant) -> None:
    """Verify HAAITaskLLMClient health checks, JSON repair, and zero-key Hub ConfigFlow."""
    cfg = LLMConfig(
        provider=LLM_PROVIDER_HA_AI_TASK,
        ai_task_entity="ai_task.google_ai_task",
        max_retries=1,
    )
    session = aiohttp_client.async_get_clientsession(hass)
    client = HAAITaskLLMClient(hass, cfg, session)

    # 1. Missing service -> unhealthy
    assert await client.health_check() is False

    good_swap = SingleMealSwapSchema(
        meal=PlannedMealSkeleton(
            meal_slot="Cena",
            title="Merluzzo grigliato",
            prep_minutes=15,
            ingredients=[
                PlannedIngredientSpec(
                    name="Merluzzo",
                    grams=180,
                    preparation_state=PreparationState.RAW,
                    role=IngredientRole.PROTEIN_ANCHOR,
                )
            ],
        )
    ).model_dump(mode="json")

    calls = _install_fake_ai_task(
        hass,
        {
            "ha_calories_buddy_SingleMealSwapSchema": [
                "not valid json at all",
                good_swap,
            ]
        },
    )

    # 2. Missing entity -> unhealthy; unavailable -> unhealthy; unknown & idle -> healthy
    assert await client.health_check() is False
    hass.states.async_set("ai_task.google_ai_task", "unavailable")
    assert await client.health_check() is False
    hass.states.async_set("ai_task.google_ai_task", "unknown")
    assert await client.health_check() is True
    hass.states.async_set("ai_task.google_ai_task", "idle")
    assert await client.health_check() is True

    # 3. Malformed response repaired cleanly on retry via LLMClient base loop
    parsed = await client.generate_structured(
        system_prompt="Chef",
        user_prompt="Propose dinner",
        response_model=SingleMealSwapSchema,
    )
    assert parsed.meal.title == "Merluzzo grigliato"
    assert len(calls) == 2
    assert calls[0]["entity_id"] == "ai_task.google_ai_task"

    # 4. _extract_llm_config timeout defaults: 180s for Ollama, 60s for cloud/ha_ai_task
    assert _extract_llm_config({CONF_LLM_PROVIDER: LLM_PROVIDER_OLLAMA}).timeout_s == 180.0
    assert _extract_llm_config({CONF_LLM_PROVIDER: LLM_PROVIDER_OPENAI}).timeout_s == 60.0
    assert _extract_llm_config({CONF_LLM_PROVIDER: LLM_PROVIDER_HA_AI_TASK}).timeout_s == 60.0
    assert (
        _extract_llm_config(
            {CONF_LLM_PROVIDER: LLM_PROVIDER_OLLAMA, CONF_LLM_TIMEOUT_S: 240.0}
        ).timeout_s
        == 240.0
    )

    # 5. Hub 2-step ConfigFlow with ha_ai_task requires no API key, URL, or model field
    flow_res = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert flow_res["type"] is FlowResultType.FORM
    assert flow_res["step_id"] == "hub"

    llm_step = await hass.config_entries.flow.async_configure(
        flow_res["flow_id"],
        {
            CONF_LANGUAGE: "it",
            CONF_LLM_PROVIDER: LLM_PROVIDER_HA_AI_TASK,
        },
    )
    assert llm_step["type"] is FlowResultType.FORM
    assert llm_step["step_id"] == "hub_llm"
    assert {str(k) for k in llm_step["data_schema"].schema} == {CONF_AI_TASK_ENTITY}

    done_res = await hass.config_entries.flow.async_configure(
        flow_res["flow_id"],
        {CONF_AI_TASK_ENTITY: "ai_task.google_ai_task"},
    )
    assert done_res["type"] is FlowResultType.CREATE_ENTRY
    assert done_res["data"][ENTRY_KIND] == ENTRY_KIND_HUB
    assert done_res["data"][CONF_LLM_PROVIDER] == LLM_PROVIDER_HA_AI_TASK
    assert done_res["data"][CONF_AI_TASK_ENTITY] == "ai_task.google_ai_task"


async def test_end_to_end_phase2_plan_calendar_rebalance_promote_and_preferences(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    hass_admin_user: MockUser,
) -> None:
    """Exercise full Phase 2 flow through HA WebSockets, Calendar, Sensors, and AI Task."""
    day_proposal = DayPlanProposalSchema(
        meals=[
            PlannedMealSkeleton(
                meal_slot="Colazione",
                title="Porridge proteico e mandorle",
                prep_minutes=10,
                steps=["Cuoci avena", "Aggiungi yogurt e mandorle"],
                ingredients=[
                    PlannedIngredientSpec(name="Fiocchi d'avena", grams=60, preparation_state=PreparationState.RAW, role=IngredientRole.CARB_ANCHOR),
                    PlannedIngredientSpec(name="Yogurt greco 0%", grams=170, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.PROTEIN_ANCHOR),
                    PlannedIngredientSpec(name="Mandorle", grams=15, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.FAT_ANCHOR),
                ],
            ),
            PlannedMealSkeleton(
                meal_slot="Pranzo",
                title="Pollo con riso basmati e zucchine",
                prep_minutes=20,
                steps=["Cuoci riso", "Griglia pollo"],
                ingredients=[
                    PlannedIngredientSpec(name="Riso basmati", grams=80, preparation_state=PreparationState.RAW, role=IngredientRole.CARB_ANCHOR),
                    PlannedIngredientSpec(name="Petto di pollo", grams=160, preparation_state=PreparationState.RAW, role=IngredientRole.PROTEIN_ANCHOR),
                    PlannedIngredientSpec(name="Olio extravergine di oliva", grams=12, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.FAT_ANCHOR),
                    PlannedIngredientSpec(name="Zucchine", grams=150, preparation_state=PreparationState.RAW, role=IngredientRole.PRODUCE_FIXED),
                ],
            ),
            PlannedMealSkeleton(
                meal_slot="Spuntino",
                title="Yogurt greco e pane integrale",
                prep_minutes=5,
                ingredients=[
                    PlannedIngredientSpec(name="Yogurt greco 0%", grams=150, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.PROTEIN_ANCHOR),
                    PlannedIngredientSpec(name="Pane integrale", grams=40, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.CARB_ANCHOR),
                ],
            ),
            PlannedMealSkeleton(
                meal_slot="Cena",
                title="Salmone al forno con patate",
                prep_minutes=25,
                ingredients=[
                    PlannedIngredientSpec(name="Salmone fresco", grams=160, preparation_state=PreparationState.RAW, role=IngredientRole.PROTEIN_ANCHOR),
                    PlannedIngredientSpec(name="Patate", grams=220, preparation_state=PreparationState.RAW, role=IngredientRole.CARB_ANCHOR),
                    PlannedIngredientSpec(name="Olio extravergine di oliva", grams=8, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.FAT_ANCHOR),
                ],
            ),
        ]
    ).model_dump(mode="json")

    suggest_payload = MealSuggestionListSchema(
        suggestions=[
            PlannedMealSkeleton(
                meal_slot="Cena",
                title="Insalata di tonno e lenticchie",
                prep_minutes=10,
                ingredients=[
                    PlannedIngredientSpec(name="Tonno all'olio", grams=120, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.PROTEIN_ANCHOR),
                    PlannedIngredientSpec(name="Lenticchie cotte", grams=180, preparation_state=PreparationState.COOKED, role=IngredientRole.CARB_ANCHOR),
                ],
            )
        ]
    ).model_dump(mode="json")

    swap_payload = SingleMealSwapSchema(
        meal=PlannedMealSkeleton(
            meal_slot="Cena",
            title="Merluzzo e patate al rosmarino",
            prep_minutes=20,
            ingredients=[
                PlannedIngredientSpec(name="Merluzzo", grams=200, preparation_state=PreparationState.RAW, role=IngredientRole.PROTEIN_ANCHOR),
                PlannedIngredientSpec(name="Patate", grams=220, preparation_state=PreparationState.RAW, role=IngredientRole.CARB_ANCHOR),
                PlannedIngredientSpec(name="Olio extravergine di oliva", grams=10, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.FAT_ANCHOR),
            ],
        )
    ).model_dump(mode="json")

    rebalance_breakfast_swap_payload = SingleMealSwapSchema(
        meal=PlannedMealSkeleton(
            meal_slot="Colazione",
            title="Yogurt greco e fiocchi d'avena",
            prep_minutes=5,
            ingredients=[
                PlannedIngredientSpec(name="Yogurt greco 0%", grams=180, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.PROTEIN_ANCHOR),
                PlannedIngredientSpec(name="Fiocchi d'avena", grams=70, preparation_state=PreparationState.RAW, role=IngredientRole.CARB_ANCHOR),
            ],
        )
    ).model_dump(mode="json")

    rebalance_swap_payload = SingleMealSwapSchema(
        meal=PlannedMealSkeleton(
            meal_slot="Cena",
            title="Pollo leggero con zucchine",
            prep_minutes=15,
            ingredients=[
                PlannedIngredientSpec(name="Petto di pollo", grams=120, preparation_state=PreparationState.RAW, role=IngredientRole.PROTEIN_ANCHOR),
                PlannedIngredientSpec(name="Zucchine", grams=200, preparation_state=PreparationState.RAW, role=IngredientRole.PRODUCE_FIXED),
            ],
        )
    ).model_dump(mode="json")

    _install_fake_ai_task(
        hass,
        {
            "ha_calories_buddy_DayPlanProposalSchema": [day_proposal, day_proposal],
            "ha_calories_buddy_MealSuggestionListSchema": [suggest_payload],
            "ha_calories_buddy_SingleMealSwapSchema": [
                swap_payload,
                rebalance_breakfast_swap_payload,
                rebalance_swap_payload,
            ],
        },
    )
    hass.states.async_set("ai_task.google_ai_task", "idle")

    _, person_entries = await setup_hub_and_persons(
        hass,
        hub_data={
            CONF_LANGUAGE: "it",
            CONF_LLM_PROVIDER: LLM_PROVIDER_HA_AI_TASK,
            CONF_AI_TASK_ENTITY: "ai_task.google_ai_task",
        },
        persons=[
            {
                CONF_PERSON_ID: "person_admin",
                CONF_DISPLAY_NAME: "AdminUser",
                CONF_HA_USER_ID: hass_admin_user.id,
                CONF_LANGUAGE_OVERRIDE: "it",
                CONF_MEAL_SLOTS: "Colazione, Pranzo, Spuntino, Cena",
            }
        ],
    )

    ent_reg = er.async_get(hass)
    admin_entities = er.async_entries_for_config_entry(ent_reg, person_entries[0].entry_id)
    cal_entity_id = next(e.entity_id for e in admin_entities if e.domain == "calendar")
    fiber_entity_id = next(
        e.entity_id for e in admin_entities if e.unique_id == "person_admin_fiber_consumed"
    )

    client = await hass_ws_client(hass)

    # 1. Preferences & Meal Feedback CRUD via WS
    await client.send_json(
        {
            "id": 1,
            "type": f"{DOMAIN}/upsert_preference",
            "scope": "person",
            "target_type": "ingredient",
            "target_value": "Crostacei",
            "stance": "allergy",
            "notes": "Severe allergy",
        }
    )
    m1 = await client.receive_json()
    assert m1["success"] is True
    pref_id = m1["result"]["preference"]["pref_id"]

    await client.send_json(
        {
            "id": 2,
            "type": f"{DOMAIN}/set_meal_feedback",
            "title": "Pollo con riso basmati e zucchine",
            "rating": "like",
        }
    )
    m2 = await client.receive_json()
    assert m2["success"] is True

    await client.send_json({"id": 3, "type": f"{DOMAIN}/get_preferences"})
    m3 = await client.receive_json()
    assert m3["success"] is True
    assert "Crostacei" in m3["result"]["context"]["hard_constraints"]
    assert len(m3["result"]["feedback"]) == 1

    # Add & delete a temporary preference
    await client.send_json(
        {
            "id": 4,
            "type": f"{DOMAIN}/upsert_preference",
            "scope": "person",
            "target_type": "ingredient",
            "target_value": "Coriandolo",
            "stance": "dislike",
        }
    )
    m4 = await client.receive_json()
    tmp_pref_id = m4["result"]["preference"]["pref_id"]
    await client.send_json(
        {"id": 5, "type": f"{DOMAIN}/delete_preference", "pref_id": tmp_pref_id}
    )
    m5 = await client.receive_json()
    assert m5["success"] is True
    assert m5["result"]["deleted"] is True

    # 2. Generate 2-day meal plan via WS + check get_plan & Calendar 📋 projection
    await client.send_json(
        {
            "id": 6,
            "type": f"{DOMAIN}/generate_plan",
            "num_days": 2,
            "meal_prep_mode": False,
        }
    )
    m6 = await client.receive_json()
    assert m6["success"] is True
    plan = m6["result"]["plan"]
    assert len(plan["days"]) == 2
    day0_items = plan["days"][0]["items"]
    lunch_item = next(it for it in day0_items if it["meal_slot"] == "Pranzo")
    dinner_item = next(it for it in day0_items if it["meal_slot"] == "Cena")

    await client.send_json({"id": 7, "type": f"{DOMAIN}/get_plan"})
    m7 = await client.receive_json()
    assert m7["success"] is True
    assert m7["result"]["plan"]["plan_id"] == plan["plan_id"]

    now = dt_util.now()
    cal_resp = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": cal_entity_id,
            "start_date_time": (now - timedelta(hours=12)).isoformat(),
            "end_date_time": (now + timedelta(days=2)).isoformat(),
        },
        blocking=True,
        return_response=True,
    )
    cal_events = cal_resp[cal_entity_id]["events"]
    assert any("📋" in ev["summary"] and "Salmone al forno" in ev["summary"] for ev in cal_events)

    # 3. Suggest meal on demand via WS -> includes smart target_macros & budget_mode_used
    await client.send_json(
        {
            "id": 8,
            "type": f"{DOMAIN}/suggest_meal",
            "meal_slot": "Cena",
            "hint": "leggero e veloce",
            "count": 1,
            "budget_mode": "auto",
        }
    )
    m8 = await client.receive_json()
    assert m8["success"] is True
    assert len(m8["result"]["suggestions"]) == 1
    assert m8["result"]["suggestions"][0]["macros"]["kcal"] > 0
    assert m8["result"]["target_macros"]["kcal"] > 0
    assert m8["result"]["budget_mode_used"] in {"slot_standard", "remaining_last_slot", "remaining_shared", "remaining_full", "over_budget_light"}

    # 4. Swap planned Cena via WS
    await client.send_json(
        {
            "id": 9,
            "type": f"{DOMAIN}/swap_plan_meal",
            "plan_item_id": dinner_item["plan_item_id"],
            "instruction": "pesce bianco",
        }
    )
    m9 = await client.receive_json()
    assert m9["success"] is True
    assert "Merluzzo" in m9["result"]["plan_item"]["title"]

    # 5. Promote Pranzo in 1 tap via WS -> MealLog confirmed + Calendar 🍽️ + fiber sensor updated
    await client.send_json(
        {
            "id": 10,
            "type": f"{DOMAIN}/promote_plan_meal",
            "plan_item_id": lunch_item["plan_item_id"],
            "scale": 1.0,
        }
    )
    m10 = await client.receive_json()
    assert m10["success"] is True
    assert m10["result"]["meal_log"]["source_channel"] == "plan_promote"
    await hass.async_block_till_done()

    fiber_state = hass.states.get(fiber_entity_id)
    assert fiber_state is not None
    assert float(fiber_state.state) > 0.0

    # 6. Log an extra high-kcal item -> Rebalance remaining planned meals today
    await client.send_json(
        {
            "id": 11,
            "type": f"{DOMAIN}/log_item",
            "food_id": "seed_olive_oil",
            "quantity": 60,
            "unit": "g",
            "state": "ready_to_eat",
            "meal_slot": "Spuntino",
        }
    )
    m11 = await client.receive_json()
    assert m11["success"] is True

    await client.send_json(
        {
            "id": 12,
            "type": f"{DOMAIN}/rebalance_today",
            "allow_llm_swap": True,
        }
    )
    m12 = await client.receive_json()
    assert m12["success"] is True
    assert m12["result"]["rebalance"]["mode"] in {"intraday_rescale", "intraday_swap"}
    updated_slots = {it["meal_slot"] for it in m12["result"]["rebalance"]["updated_items"]}
    assert "Spuntino" not in updated_slots
    assert "api_key" not in json.dumps(m12)

    cal_after = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": cal_entity_id,
            "start_date_time": (now - timedelta(hours=12)).isoformat(),
            "end_date_time": (now + timedelta(hours=12)).isoformat(),
        },
        blocking=True,
        return_response=True,
    )
    events_today = cal_after[cal_entity_id]["events"]
    assert any("🍽️" in ev["summary"] and "Pranzo" in ev["summary"] for ev in events_today)

    # 7. Log suggested meal in 1 grouped tap via WS (id=13)
    await client.send_json(
        {
            "id": 13,
            "type": f"{DOMAIN}/log_suggestion",
            "suggestion": m8["result"]["suggestions"][0],
            "scale": 1.0,
        }
    )
    m13 = await client.receive_json()
    assert m13["success"] is True
    assert m13["result"]["meal_log"]["source_channel"] == "suggestion_accept"
    assert len(m13["result"]["meal_log"]["items"]) == len(
        m8["result"]["suggestions"][0]["ingredients"]
    )
    assert pref_id


async def test_concurrent_hub_and_person_setup_no_race(hass: HomeAssistant) -> None:
    """Verify concurrent async_setup of Hub + Person shares one locked runtime cleanly."""
    hub_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Calories Buddy — Household Hub",
        unique_id="hub_race",
        data={
            ENTRY_KIND: ENTRY_KIND_HUB,
            CONF_LANGUAGE: "it",
            CONF_LLM_PROVIDER: LLM_PROVIDER_NONE,
        },
    )
    person_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Calories Buddy — RaceUser",
        unique_id="person_race",
        data={
            ENTRY_KIND: ENTRY_KIND_PERSON,
            CONF_PERSON_ID: "person_race",
            CONF_DISPLAY_NAME: "RaceUser",
            CONF_HA_USER_ID: "uid_race",
            CONF_LANGUAGE_OVERRIDE: "inherit",
            CONF_SEX: "male",
            CONF_BIRTH_YEAR: 1990,
            CONF_HEIGHT_CM: 178.0,
            CONF_INITIAL_WEIGHT_KG: 75.0,
            CONF_TARGET_MODE: "physiology",
            CONF_GOAL_TYPE: "maintain",
            CONF_TDEE_ENGINE: "static",
            CONF_ACTIVITY_LEVEL: "moderate",
        },
    )
    hub_entry.add_to_hass(hass)
    person_entry.add_to_hass(hass)

    # Launch Person and Hub setup concurrently (Person intentionally scheduled first)
    p_ok, h_ok = await asyncio.gather(
        hass.config_entries.async_setup(person_entry.entry_id),
        hass.config_entries.async_setup(hub_entry.entry_id),
    )
    await hass.async_block_till_done()

    assert p_ok is True
    assert h_ok is True
    runtime = hass.data[DOMAIN]
    assert runtime.persons["person_race"].profile.language == "it"

    ent_reg = er.async_get(hass)
    sensors = [
        e
        for e in er.async_entries_for_config_entry(ent_reg, person_entry.entry_id)
        if e.domain == "sensor"
    ]
    assert len(sensors) == 10
    for ent in sensors:
        st = hass.states.get(ent.entity_id)
        assert st is not None
        assert st.state not in {"unavailable", "unknown"}
