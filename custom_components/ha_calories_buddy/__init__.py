"""HA Calories Buddy custom integration setup and lifecycle management."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import slugify

from custom_components.ha_calories_buddy.config_flow import merged_entry_config
from custom_components.ha_calories_buddy.const import (
    CONF_ACTIVE_KCAL_ENTITY,
    CONF_ACTIVITY_LEVEL,
    CONF_AI_TASK_ENTITY,
    CONF_ALLERGIES,
    CONF_BIRTH_YEAR,
    CONF_BLACKLIST,
    CONF_BODY_FAT_ENTITY,
    CONF_BODY_FAT_PCT,
    CONF_DIETARY_REGIME,
    CONF_DISPLAY_NAME,
    CONF_GLOBAL_BASELINE_NOTES,
    CONF_GOAL_TYPE,
    CONF_HA_PERSON_ENTITY,
    CONF_HA_USER_ID,
    CONF_HEIGHT_CM,
    CONF_INITIAL_WEIGHT_KG,
    CONF_INTOLERANCES,
    CONF_LANGUAGE,
    CONF_LANGUAGE_OVERRIDE,
    CONF_LLM_API_KEY,
    CONF_LLM_BASE_URL,
    CONF_LLM_MODEL,
    CONF_LLM_OVERRIDE_ENABLED,
    CONF_LLM_PROVIDER,
    CONF_MANUAL_CARBS_G,
    CONF_MANUAL_FAT_G,
    CONF_MANUAL_FIBER_G,
    CONF_MANUAL_KCAL,
    CONF_MANUAL_PROTEIN_G,
    CONF_MANUAL_TRAINING_CARBS_G,
    CONF_MANUAL_TRAINING_FAT_G,
    CONF_MANUAL_TRAINING_FIBER_G,
    CONF_MANUAL_TRAINING_KCAL,
    CONF_MANUAL_TRAINING_PROTEIN_G,
    CONF_MEAL_SLOTS,
    CONF_NOTIFY_SERVICE,
    CONF_PERSON_ID,
    CONF_SEX,
    CONF_STEPS_ENTITY,
    CONF_TARGET_MODE,
    CONF_TDEE_ENGINE,
    CONF_TODO_ENTITY,
    CONF_TOTAL_KCAL_ENTITY,
    CONF_USDA_API_KEY,
    CONF_WEARABLE_CORRECTION_FACTOR,
    CONF_WEEKLY_RATE_KG,
    CONF_WEIGHT_ENTITY,
    DEFAULT_LANGUAGE,
    DEFAULT_MEAL_SLOTS_EN,
    DEFAULT_MEAL_SLOTS_IT,
    DEFAULT_WEARABLE_CORRECTION_FACTOR,
    DOMAIN,
    ENTRY_KIND,
    ENTRY_KIND_HUB,
    ENTRY_KIND_PERSON,
    LLM_PROVIDER_NONE,
    STORAGE_DB_FILENAME,
)
from custom_components.ha_calories_buddy.coordinator import PersonNutritionCoordinator
from custom_components.ha_calories_buddy.core.logging import get_logger, log_event
from custom_components.ha_calories_buddy.core.models import (
    ActivityLevel,
    EntityMappings,
    GoalType,
    LLMConfig,
    ManualMacroTarget,
    MealSlotConfig,
    PersonProfile,
    Sex,
    TargetMode,
    TDEEEngineType,
)
from custom_components.ha_calories_buddy.core.nutrition.providers.open_food_facts import (
    OpenFoodFactsProvider,
)
from custom_components.ha_calories_buddy.core.nutrition.providers.usda import USDAProvider
from custom_components.ha_calories_buddy.core.nutrition.resolver import FoodResolver
from custom_components.ha_calories_buddy.core.planner.meal_parser import (
    MealLoggingService,
)
from custom_components.ha_calories_buddy.core.planner.meal_planner import (
    MealPlannerService,
)
from custom_components.ha_calories_buddy.core.planner.preferences import (
    PreferenceEngine,
)
from custom_components.ha_calories_buddy.core.planner.rebalancer import (
    MealRebalancerService,
)
from custom_components.ha_calories_buddy.core.planner.slot_resolver import (
    slot_approx_minutes,
)
from custom_components.ha_calories_buddy.core.storage.database import Database
from custom_components.ha_calories_buddy.core.storage.food_repo import FoodRepository
from custom_components.ha_calories_buddy.core.storage.meal_repo import MealRepository
from custom_components.ha_calories_buddy.core.storage.plan_repo import (
    MealPlanRepository,
)
from custom_components.ha_calories_buddy.core.storage.preference_repo import (
    PreferenceRepository,
)
from custom_components.ha_calories_buddy.core.storage.profile_repo import ProfileRepository
from custom_components.ha_calories_buddy.core.storage.seeder import seed_database_if_empty
from custom_components.ha_calories_buddy.frontend import async_register_frontend
from custom_components.ha_calories_buddy.llm_api import async_register_llm_api
from custom_components.ha_calories_buddy.runtime import (
    DomainRuntime,
    HubRuntimeData,
    PersonRuntimeData,
)
from custom_components.ha_calories_buddy.services import async_register_services
from custom_components.ha_calories_buddy.websocket_api import async_register_websocket_api

_LOGGER = get_logger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.CALENDAR]


def _split_csv(value: Any) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _extract_llm_config(cfg: dict[str, Any]) -> LLMConfig:
    provider = str(cfg.get(CONF_LLM_PROVIDER) or LLM_PROVIDER_NONE).strip()
    api_key = str(cfg.get(CONF_LLM_API_KEY) or "").strip() or None
    base_url = str(cfg.get(CONF_LLM_BASE_URL) or "").strip() or None
    model = str(cfg.get(CONF_LLM_MODEL) or "").strip()
    ai_task_entity = str(cfg.get(CONF_AI_TASK_ENTITY) or "").strip() or None
    return LLMConfig(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        ai_task_entity=ai_task_entity,
    )


def _slot_hhmm(name: str) -> str:
    mins = slot_approx_minutes(MealSlotConfig(slot_id="tmp", name=name))
    return f"{mins // 60:02d}:{mins % 60:02d}"


def _build_person_profile(
    entry: ConfigEntry, hub_language: str
) -> PersonProfile:
    cfg = merged_entry_config(entry)
    lang_ovr = str(cfg.get(CONF_LANGUAGE_OVERRIDE) or "inherit")
    language = hub_language if lang_ovr == "inherit" else lang_ovr

    default_slots = (
        DEFAULT_MEAL_SLOTS_IT if language == "it" else DEFAULT_MEAL_SLOTS_EN
    )
    slot_names = _split_csv(cfg.get(CONF_MEAL_SLOTS) or default_slots)
    meal_slots = [
        MealSlotConfig(
            slot_id=slugify(name) or f"slot_{i}",
            name=name,
            approx_time=_slot_hhmm(name),
        )
        for i, name in enumerate(slot_names)
    ]

    target_mode = TargetMode(cfg.get(CONF_TARGET_MODE, TargetMode.PHYSIOLOGY.value))
    manual_targets: ManualMacroTarget | None = None
    if target_mode == TargetMode.MANUAL and cfg.get(CONF_MANUAL_KCAL) is not None:
        manual_targets = ManualMacroTarget(
            kcal=float(cfg[CONF_MANUAL_KCAL]),
            protein_g=float(cfg.get(CONF_MANUAL_PROTEIN_G, 150.0)),
            carbs_g=float(cfg.get(CONF_MANUAL_CARBS_G, 200.0)),
            fat_g=float(cfg.get(CONF_MANUAL_FAT_G, 65.0)),
            fiber_g=(
                float(cfg[CONF_MANUAL_FIBER_G])
                if cfg.get(CONF_MANUAL_FIBER_G) is not None
                else None
            ),
            training_kcal=(
                float(cfg[CONF_MANUAL_TRAINING_KCAL])
                if cfg.get(CONF_MANUAL_TRAINING_KCAL) is not None
                else None
            ),
            training_protein_g=(
                float(cfg[CONF_MANUAL_TRAINING_PROTEIN_G])
                if cfg.get(CONF_MANUAL_TRAINING_PROTEIN_G) is not None
                else None
            ),
            training_carbs_g=(
                float(cfg[CONF_MANUAL_TRAINING_CARBS_G])
                if cfg.get(CONF_MANUAL_TRAINING_CARBS_G) is not None
                else None
            ),
            training_fat_g=(
                float(cfg[CONF_MANUAL_TRAINING_FAT_G])
                if cfg.get(CONF_MANUAL_TRAINING_FAT_G) is not None
                else None
            ),
            training_fiber_g=(
                float(cfg[CONF_MANUAL_TRAINING_FIBER_G])
                if cfg.get(CONF_MANUAL_TRAINING_FIBER_G) is not None
                else None
            ),
        )

    mappings = EntityMappings(
        weight_entity=cfg.get(CONF_WEIGHT_ENTITY) or None,
        body_fat_entity=cfg.get(CONF_BODY_FAT_ENTITY) or None,
        active_kcal_entity=cfg.get(CONF_ACTIVE_KCAL_ENTITY) or None,
        total_kcal_entity=cfg.get(CONF_TOTAL_KCAL_ENTITY) or None,
        steps_entity=cfg.get(CONF_STEPS_ENTITY) or None,
        todo_entity=cfg.get(CONF_TODO_ENTITY) or None,
        notify_service=str(cfg.get(CONF_NOTIFY_SERVICE) or "").strip() or None,
    )

    body_fat = cfg.get(CONF_BODY_FAT_PCT)
    return PersonProfile(
        person_id=str(cfg[CONF_PERSON_ID]),
        config_entry_id=entry.entry_id,
        ha_person_entity=cfg.get(CONF_HA_PERSON_ENTITY) or None,
        ha_user_id=str(cfg.get(CONF_HA_USER_ID) or "").strip() or None,
        display_name=str(cfg[CONF_DISPLAY_NAME]),
        language=language,
        sex=Sex(cfg.get(CONF_SEX, Sex.MALE.value)),
        birth_year=int(cfg.get(CONF_BIRTH_YEAR, 1990)),
        height_cm=float(cfg.get(CONF_HEIGHT_CM, 175.0)),
        initial_weight_kg=float(cfg.get(CONF_INITIAL_WEIGHT_KG, 75.0)),
        body_fat_pct=float(body_fat) if body_fat is not None else None,
        target_mode=target_mode,
        goal_type=GoalType(cfg.get(CONF_GOAL_TYPE, GoalType.MAINTAIN.value)),
        weekly_rate_kg=float(cfg.get(CONF_WEEKLY_RATE_KG, 0.0)),
        tdee_engine=TDEEEngineType(
            cfg.get(CONF_TDEE_ENGINE, TDEEEngineType.STATIC.value)
        ),
        activity_level=ActivityLevel(
            cfg.get(CONF_ACTIVITY_LEVEL, ActivityLevel.MODERATE.value)
        ),
        wearable_correction_factor=float(
            cfg.get(CONF_WEARABLE_CORRECTION_FACTOR, DEFAULT_WEARABLE_CORRECTION_FACTOR)
        ),
        manual_targets=manual_targets,
        entity_mappings=mappings,
        dietary_regime=str(cfg.get(CONF_DIETARY_REGIME, "omnivore")),
        allergies=_split_csv(cfg.get(CONF_ALLERGIES)),
        intolerances=_split_csv(cfg.get(CONF_INTOLERANCES)),
        blacklist=_split_csv(cfg.get(CONF_BLACKLIST)),
        meal_slots=meal_slots,
        llm_override_enabled=bool(cfg.get(CONF_LLM_OVERRIDE_ENABLED, False)),
    )


async def _ensure_runtime(hass: HomeAssistant) -> DomainRuntime:
    lock: asyncio.Lock = hass.data.setdefault(f"{DOMAIN}_init_lock", asyncio.Lock())
    async with lock:
        if DOMAIN in hass.data:
            existing: DomainRuntime = hass.data[DOMAIN]
            return existing

        db_path = hass.config.path(".storage", STORAGE_DB_FILENAME)
        db = Database(db_path)
        await db.initialize()
        await seed_database_if_empty(db)

        session = async_get_clientsession(hass)
        food_repo = FoodRepository(db)
        meal_repo = MealRepository(db)
        profile_repo = ProfileRepository(db)
        preference_repo = PreferenceRepository(db)
        plan_repo = MealPlanRepository(db)
        preference_engine = PreferenceEngine(preference_repo, meal_repo)
        off_provider = OpenFoodFactsProvider(session)
        usda_provider = USDAProvider(session, api_key=None)
        resolver = FoodResolver(food_repo, off_provider=off_provider, usda_provider=usda_provider)

        placeholder_logging = MealLoggingService(
            meal_repo=meal_repo,
            food_repo=food_repo,
            resolver=resolver,
            llm_client_getter=lambda _p: None,
        )
        placeholder_planner = MealPlannerService(
            plan_repo=plan_repo,
            meal_repo=meal_repo,
            preference_engine=preference_engine,
            resolver=resolver,
            llm_client_getter=lambda _p: None,
        )
        placeholder_rebalancer = MealRebalancerService(
            plan_repo=plan_repo,
            meal_repo=meal_repo,
            resolver=resolver,
            planner_service=placeholder_planner,
        )

        runtime = DomainRuntime(
            hass=hass,
            db=db,
            food_repo=food_repo,
            meal_repo=meal_repo,
            profile_repo=profile_repo,
            preference_repo=preference_repo,
            plan_repo=plan_repo,
            preference_engine=preference_engine,
            session=session,
            off_provider=off_provider,
            usda_provider=usda_provider,
            resolver=resolver,
            logging_service=placeholder_logging,
            planner_service=placeholder_planner,
            rebalancer_service=placeholder_rebalancer,
        )
        runtime.update_usda_api_key(None)

        async def _on_ha_stop(_event: Event) -> None:
            await db.close()

        runtime.stop_unsub = hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, _on_ha_stop
        )

        if not runtime.frontend_registered:
            await async_register_frontend(hass)
            runtime.frontend_registered = True

        if not runtime.services_registered:
            async_register_services(hass)
            runtime.services_registered = True

        if not runtime.ws_registered:
            async_register_websocket_api(hass)
            runtime.ws_registered = True

        if runtime.llm_api_unsub is None:
            runtime.llm_api_unsub = async_register_llm_api(hass)

        hass.data[DOMAIN] = runtime
        return runtime


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up either the Household Hub or a Person Profile ConfigEntry."""
    runtime = await _ensure_runtime(hass)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    kind = entry.data.get(ENTRY_KIND, ENTRY_KIND_PERSON)
    if kind == ENTRY_KIND_HUB:
        cfg = merged_entry_config(entry)
        usda_key = str(cfg.get(CONF_USDA_API_KEY) or "").strip() or None
        hub_language = str(cfg.get(CONF_LANGUAGE, DEFAULT_LANGUAGE))
        runtime.hub = HubRuntimeData(
            entry_id=entry.entry_id,
            llm_config=_extract_llm_config(cfg),
            language=hub_language,
            usda_api_key=usda_key,
            global_baseline_notes=str(cfg.get(CONF_GLOBAL_BASELINE_NOTES) or ""),
        )
        runtime.update_usda_api_key(usda_key)
        # Synchronize language & default slots for person entries that loaded before Hub
        for person_rt in runtime.persons.values():
            p_entry = hass.config_entries.async_get_entry(person_rt.entry_id)
            if p_entry is not None:
                p_cfg = merged_entry_config(p_entry)
                if str(p_cfg.get(CONF_LANGUAGE_OVERRIDE, "inherit")) == "inherit":
                    updated_profile = _build_person_profile(p_entry, hub_language)
                    if updated_profile.language != person_rt.profile.language:
                        person_rt.profile = updated_profile
                        person_rt.coordinator.profile = updated_profile
                        await runtime.profile_repo.upsert_person(updated_profile)
            await person_rt.coordinator.async_request_refresh()
        log_event(_LOGGER, logging.INFO, "hub_entry_loaded", entry_id=entry.entry_id)
        return True

    hub_lang = runtime.hub.language if runtime.hub else DEFAULT_LANGUAGE
    profile = _build_person_profile(entry, hub_lang)
    await runtime.profile_repo.upsert_person(profile)

    cfg = merged_entry_config(entry)
    llm_ovr = (
        _extract_llm_config(cfg) if profile.llm_override_enabled else None
    )

    coordinator = PersonNutritionCoordinator(hass, runtime, profile)
    await coordinator.async_config_entry_first_refresh()
    coordinator.setup_listeners()

    runtime.persons[profile.person_id] = PersonRuntimeData(
        entry_id=entry.entry_id,
        person_id=profile.person_id,
        profile=profile,
        llm_config=llm_ovr,
        coordinator=coordinator,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    log_event(
        _LOGGER,
        logging.INFO,
        "person_entry_loaded",
        person_id=profile.person_id,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Hub or Person Profile ConfigEntry cleanly."""
    runtime: DomainRuntime | None = hass.data.get(DOMAIN)
    if runtime is None:
        return True

    kind = entry.data.get(ENTRY_KIND, ENTRY_KIND_PERSON)
    if kind == ENTRY_KIND_HUB:
        runtime.hub = None
        unload_ok = True
    else:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        person_id = entry.data.get(CONF_PERSON_ID)
        if person_id and person_id in runtime.persons:
            person_rt = runtime.persons.pop(person_id)
            await person_rt.coordinator.async_shutdown()

    if runtime.hub is None and not runtime.persons:
        if runtime.llm_api_unsub is not None:
            runtime.llm_api_unsub()
            runtime.llm_api_unsub = None
        if runtime.stop_unsub is not None:
            runtime.stop_unsub()
            runtime.stop_unsub = None
        await runtime.db.close()
        hass.data.pop(DOMAIN, None)
        hass.data.pop(f"{DOMAIN}_init_lock", None)

    return unload_ok
