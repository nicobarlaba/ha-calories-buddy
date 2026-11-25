"""ConfigFlow and OptionsFlow for ha-calories-buddy (1 Hub + N Person Profiles)."""

from __future__ import annotations

import uuid
from typing import Any

import probatio as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

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
    LLM_PROVIDER_ANTHROPIC,
    LLM_PROVIDER_GEMINI,
    LLM_PROVIDER_HA_AI_TASK,
    LLM_PROVIDER_NONE,
    LLM_PROVIDER_OLLAMA,
    LLM_PROVIDER_OPENAI,
    LLM_PROVIDER_OPENAI_COMPAT,
    LLM_PROVIDERS,
    SUPPORTED_LANGUAGES,
)
from custom_components.ha_calories_buddy.core.llm.factory import create_llm_client
from custom_components.ha_calories_buddy.core.models import (
    ActivityLevel,
    GoalType,
    LLMConfig,
    Sex,
    TargetMode,
    TDEEEngineType,
)
from custom_components.ha_calories_buddy.ha_llm_client import HAAITaskLLMClient

DEFAULT_MODELS_BY_PROVIDER: dict[str, str] = {
    LLM_PROVIDER_OPENAI: "gpt-4o-mini",
    LLM_PROVIDER_ANTHROPIC: "claude-sonnet-4-5",
    LLM_PROVIDER_GEMINI: "gemini-2.5-flash",
    LLM_PROVIDER_OLLAMA: "qwen2.5:14b",
    LLM_PROVIDER_OPENAI_COMPAT: "local-model",
}

DEFAULT_BASE_URLS_BY_PROVIDER: dict[str, str] = {
    LLM_PROVIDER_OLLAMA: "http://localhost:11434",
    LLM_PROVIDER_OPENAI_COMPAT: "http://localhost:1234/v1",
}


def merged_entry_config(entry: config_entries.ConfigEntry) -> dict[str, Any]:
    """Return merged ConfigEntry data and options."""
    return {**entry.data, **entry.options}


def _find_hub_entry(hass: HomeAssistant) -> config_entries.ConfigEntry | None:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(ENTRY_KIND) == ENTRY_KIND_HUB:
            return entry
    return None


async def _validate_llm_input(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> dict[str, str]:
    """Validate LLM credentials and connectivity if a provider is active."""
    provider = str(user_input.get(CONF_LLM_PROVIDER, LLM_PROVIDER_NONE))
    if provider in ("", LLM_PROVIDER_NONE):
        return {}

    if provider == LLM_PROVIDER_HA_AI_TASK:
        ai_task_entity = str(user_input.get(CONF_AI_TASK_ENTITY) or "").strip() or None
        cfg = LLMConfig(
            provider=provider,
            ai_task_entity=ai_task_entity,
            timeout_s=10.0,
        )
        client = HAAITaskLLMClient(hass, cfg, async_get_clientsession(hass))
        if not await client.health_check():
            return {"base": "cannot_connect"}
        return {}

    api_key = str(user_input.get(CONF_LLM_API_KEY) or "").strip() or None
    base_url = str(user_input.get(CONF_LLM_BASE_URL) or "").strip() or DEFAULT_BASE_URLS_BY_PROVIDER.get(provider)
    model = str(user_input.get(CONF_LLM_MODEL) or "").strip() or DEFAULT_MODELS_BY_PROVIDER.get(provider, "")

    if provider in (LLM_PROVIDER_OPENAI, LLM_PROVIDER_ANTHROPIC, LLM_PROVIDER_GEMINI) and not api_key:
        return {"base": "api_key_required"}
    if not model:
        return {"base": "model_required"}

    user_input[CONF_LLM_MODEL] = model
    if base_url:
        user_input[CONF_LLM_BASE_URL] = base_url

    cfg = LLMConfig(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout_s=10.0,
    )
    direct_client = create_llm_client(cfg, async_get_clientsession(hass))
    if direct_client is None:
        return {}
    if not await direct_client.health_check():
        return {"base": "cannot_connect"}
    return {}


def _suggested_ai_task_entity(defaults: dict[str, Any], hass: HomeAssistant | None) -> str | None:
    if defaults.get(CONF_AI_TASK_ENTITY):
        return str(defaults[CONF_AI_TASK_ENTITY])
    if hass is not None:
        discovered = hass.states.async_entity_ids("ai_task")
        if len(discovered) == 1:
            return discovered[0]
    return None


def _hub_schema(defaults: dict[str, Any], hass: HomeAssistant | None = None) -> vol.Schema:
    suggested_ai_task = _suggested_ai_task_entity(defaults, hass)
    return vol.Schema(
        {
            vol.Required(
                CONF_LANGUAGE, default=defaults.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=SUPPORTED_LANGUAGES,
                    translation_key="language",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_LLM_PROVIDER,
                default=defaults.get(CONF_LLM_PROVIDER, LLM_PROVIDER_NONE),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=LLM_PROVIDERS,
                    translation_key="llm_provider",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_AI_TASK_ENTITY,
                description={"suggested_value": suggested_ai_task},
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="ai_task")
            ),
            vol.Optional(
                CONF_LLM_API_KEY, default=defaults.get(CONF_LLM_API_KEY, "")
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Optional(
                CONF_LLM_BASE_URL, default=defaults.get(CONF_LLM_BASE_URL, "")
            ): selector.TextSelector(),
            vol.Optional(
                CONF_LLM_MODEL, default=defaults.get(CONF_LLM_MODEL, "")
            ): selector.TextSelector(),
            vol.Optional(
                CONF_USDA_API_KEY, default=defaults.get(CONF_USDA_API_KEY, "")
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Optional(
                CONF_GLOBAL_BASELINE_NOTES,
                default=defaults.get(CONF_GLOBAL_BASELINE_NOTES, ""),
            ): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
        }
    )


def _biometrics_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_SEX, default=defaults.get(CONF_SEX, Sex.MALE.value)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[Sex.MALE.value, Sex.FEMALE.value],
                    translation_key="sex",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_BIRTH_YEAR, default=int(defaults.get(CONF_BIRTH_YEAR, 1990))
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1920, max=2020, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_HEIGHT_CM, default=float(defaults.get(CONF_HEIGHT_CM, 175.0))
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=80.0,
                    max=240.0,
                    step=0.5,
                    unit_of_measurement="cm",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_INITIAL_WEIGHT_KG,
                default=float(defaults.get(CONF_INITIAL_WEIGHT_KG, 75.0)),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=25.0,
                    max=350.0,
                    step=0.1,
                    unit_of_measurement="kg",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_BODY_FAT_PCT,
                description={"suggested_value": defaults.get(CONF_BODY_FAT_PCT)},
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=3.0,
                    max=65.0,
                    step=0.1,
                    unit_of_measurement="%",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_TARGET_MODE,
                default=defaults.get(CONF_TARGET_MODE, TargetMode.PHYSIOLOGY.value),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[TargetMode.PHYSIOLOGY.value, TargetMode.MANUAL.value],
                    translation_key="target_mode",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_GOAL_TYPE,
                default=defaults.get(CONF_GOAL_TYPE, GoalType.MAINTAIN.value),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[g.value for g in GoalType],
                    translation_key="goal_type",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_WEEKLY_RATE_KG,
                default=float(defaults.get(CONF_WEEKLY_RATE_KG, 0.0)),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-1.5,
                    max=1.5,
                    step=0.05,
                    unit_of_measurement="kg/wk",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_TDEE_ENGINE,
                default=defaults.get(CONF_TDEE_ENGINE, TDEEEngineType.STATIC.value),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        TDEEEngineType.STATIC.value,
                        TDEEEngineType.WEARABLE_DYNAMIC.value,
                    ],
                    translation_key="tdee_engine",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_ACTIVITY_LEVEL,
                default=defaults.get(CONF_ACTIVITY_LEVEL, ActivityLevel.MODERATE.value),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[a.value for a in ActivityLevel],
                    translation_key="activity_level",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_WEARABLE_CORRECTION_FACTOR,
                default=float(
                    defaults.get(
                        CONF_WEARABLE_CORRECTION_FACTOR,
                        DEFAULT_WEARABLE_CORRECTION_FACTOR,
                    )
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.4, max=1.4, step=0.05, mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
    )


def _manual_targets_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_MANUAL_KCAL, default=float(defaults.get(CONF_MANUAL_KCAL, 2000.0))
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=500.0, max=8000.0, step=10.0, unit_of_measurement="kcal", mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_MANUAL_PROTEIN_G,
                default=float(defaults.get(CONF_MANUAL_PROTEIN_G, 150.0)),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0, max=500.0, step=1.0, unit_of_measurement="g", mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_MANUAL_CARBS_G,
                default=float(defaults.get(CONF_MANUAL_CARBS_G, 200.0)),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0, max=1000.0, step=1.0, unit_of_measurement="g", mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_MANUAL_FAT_G, default=float(defaults.get(CONF_MANUAL_FAT_G, 65.0))
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0, max=400.0, step=1.0, unit_of_measurement="g", mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_MANUAL_FIBER_G,
                description={"suggested_value": defaults.get(CONF_MANUAL_FIBER_G)},
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0, max=150.0, step=1.0, unit_of_measurement="g", mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_MANUAL_TRAINING_KCAL,
                description={"suggested_value": defaults.get(CONF_MANUAL_TRAINING_KCAL)},
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=500.0, max=8000.0, step=10.0, unit_of_measurement="kcal", mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_MANUAL_TRAINING_PROTEIN_G,
                description={"suggested_value": defaults.get(CONF_MANUAL_TRAINING_PROTEIN_G)},
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0, max=500.0, step=1.0, unit_of_measurement="g", mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_MANUAL_TRAINING_CARBS_G,
                description={"suggested_value": defaults.get(CONF_MANUAL_TRAINING_CARBS_G)},
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0, max=1000.0, step=1.0, unit_of_measurement="g", mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_MANUAL_TRAINING_FAT_G,
                description={"suggested_value": defaults.get(CONF_MANUAL_TRAINING_FAT_G)},
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0, max=400.0, step=1.0, unit_of_measurement="g", mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_MANUAL_TRAINING_FIBER_G,
                description={"suggested_value": defaults.get(CONF_MANUAL_TRAINING_FIBER_G)},
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0, max=150.0, step=1.0, unit_of_measurement="g", mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
    )


def _entities_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(
                CONF_WEIGHT_ENTITY,
                description={"suggested_value": defaults.get(CONF_WEIGHT_ENTITY)},
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(
                CONF_BODY_FAT_ENTITY,
                description={"suggested_value": defaults.get(CONF_BODY_FAT_ENTITY)},
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(
                CONF_ACTIVE_KCAL_ENTITY,
                description={"suggested_value": defaults.get(CONF_ACTIVE_KCAL_ENTITY)},
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(
                CONF_TOTAL_KCAL_ENTITY,
                description={"suggested_value": defaults.get(CONF_TOTAL_KCAL_ENTITY)},
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(
                CONF_STEPS_ENTITY,
                description={"suggested_value": defaults.get(CONF_STEPS_ENTITY)},
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(
                CONF_TODO_ENTITY,
                description={"suggested_value": defaults.get(CONF_TODO_ENTITY)},
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="todo")),
            vol.Optional(
                CONF_NOTIFY_SERVICE,
                default=defaults.get(CONF_NOTIFY_SERVICE, ""),
            ): selector.TextSelector(),
        }
    )


def _llm_override_schema(defaults: dict[str, Any], hass: HomeAssistant | None = None) -> vol.Schema:
    suggested_ai_task = _suggested_ai_task_entity(defaults, hass)
    return vol.Schema(
        {
            vol.Required(
                CONF_LLM_OVERRIDE_ENABLED,
                default=bool(defaults.get(CONF_LLM_OVERRIDE_ENABLED, False)),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_LLM_PROVIDER,
                default=defaults.get(CONF_LLM_PROVIDER, LLM_PROVIDER_NONE),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=LLM_PROVIDERS,
                    translation_key="llm_provider",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_AI_TASK_ENTITY,
                description={"suggested_value": suggested_ai_task},
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="ai_task")
            ),
            vol.Optional(
                CONF_LLM_API_KEY, default=defaults.get(CONF_LLM_API_KEY, "")
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Optional(
                CONF_LLM_BASE_URL, default=defaults.get(CONF_LLM_BASE_URL, "")
            ): selector.TextSelector(),
            vol.Optional(
                CONF_LLM_MODEL, default=defaults.get(CONF_LLM_MODEL, "")
            ): selector.TextSelector(),
        }
    )


def _diet_schema(defaults: dict[str, Any], language: str) -> vol.Schema:
    default_slots = (
        DEFAULT_MEAL_SLOTS_IT if language == "it" else DEFAULT_MEAL_SLOTS_EN
    )
    return vol.Schema(
        {
            vol.Required(
                CONF_MEAL_SLOTS,
                default=defaults.get(CONF_MEAL_SLOTS, default_slots),
            ): selector.TextSelector(),
            vol.Required(
                CONF_DIETARY_REGIME,
                default=defaults.get(CONF_DIETARY_REGIME, "omnivore"),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        "omnivore",
                        "mediterranean",
                        "vegetarian",
                        "vegan",
                        "pescatarian",
                        "keto",
                    ],
                    translation_key="dietary_regime",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_ALLERGIES, default=defaults.get(CONF_ALLERGIES, "")
            ): selector.TextSelector(),
            vol.Optional(
                CONF_INTOLERANCES, default=defaults.get(CONF_INTOLERANCES, "")
            ): selector.TextSelector(),
            vol.Optional(
                CONF_BLACKLIST, default=defaults.get(CONF_BLACKLIST, "")
            ): selector.TextSelector(),
        }
    )


class CaloriesBuddyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow creating either the singleton Hub or a new Person Profile."""

    VERSION = 1

    def __init__(self) -> None:
        self._person_data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the OptionsFlow handler."""
        return CaloriesBuddyOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Route to Hub creation if none exists, otherwise add a Person Profile."""
        if _find_hub_entry(self.hass) is None:
            return await self.async_step_hub(user_input)
        return await self.async_step_person_identity(user_input)

    async def async_step_hub(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure the singleton Household Hub (with LLM Disclosure Notice)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await _validate_llm_input(self.hass, user_input)
            if not errors:
                await self.async_set_unique_id("hub")
                self._abort_if_unique_id_configured()
                data = {ENTRY_KIND: ENTRY_KIND_HUB, **user_input}
                return self.async_create_entry(
                    title="Calories Buddy — Household Hub", data=data
                )

        return self.async_show_form(
            step_id="hub",
            data_schema=_hub_schema(user_input or {}, self.hass),
            errors=errors,
        )

    async def async_step_person_identity(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: Map Person Profile to Home Assistant person.* entity and user_id."""
        errors: dict[str, str] = {}
        if user_input is not None:
            ha_person_entity = user_input.get(CONF_HA_PERSON_ENTITY)
            display_name = str(user_input.get(CONF_DISPLAY_NAME) or "").strip()
            ha_user_id = str(user_input.get(CONF_HA_USER_ID) or "").strip() or None

            if ha_person_entity:
                state = self.hass.states.get(ha_person_entity)
                if state is not None:
                    if not display_name:
                        display_name = str(
                            state.attributes.get("friendly_name") or state.name
                        ).strip()
                    if not ha_user_id and state.attributes.get("user_id"):
                        ha_user_id = str(state.attributes["user_id"])

            if not display_name:
                errors[CONF_DISPLAY_NAME] = "name_required"
            else:
                self._person_data = {
                    ENTRY_KIND: ENTRY_KIND_PERSON,
                    CONF_PERSON_ID: f"person_{uuid.uuid4().hex[:10]}",
                    CONF_HA_PERSON_ENTITY: ha_person_entity,
                    CONF_HA_USER_ID: ha_user_id,
                    CONF_DISPLAY_NAME: display_name,
                    CONF_LANGUAGE_OVERRIDE: user_input.get(
                        CONF_LANGUAGE_OVERRIDE, "inherit"
                    ),
                }
                return await self.async_step_person_biometrics()

        schema = vol.Schema(
            {
                vol.Optional(CONF_HA_PERSON_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="person")
                ),
                vol.Optional(CONF_DISPLAY_NAME, default=""): selector.TextSelector(),
                vol.Optional(CONF_HA_USER_ID, default=""): selector.TextSelector(),
                vol.Required(
                    CONF_LANGUAGE_OVERRIDE, default="inherit"
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=["inherit", *SUPPORTED_LANGUAGES],
                        translation_key="language_override",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="person_identity", data_schema=schema, errors=errors
        )

    async def async_step_person_biometrics(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: Biometrics, Target Mode, Goal, and TDEE Engine."""
        if user_input is not None:
            self._person_data.update(user_input)
            if user_input.get(CONF_TARGET_MODE) == TargetMode.MANUAL.value:
                return await self.async_step_person_manual_targets()
            return await self.async_step_person_entities()

        return self.async_show_form(
            step_id="person_biometrics",
            data_schema=_biometrics_schema(self._person_data),
        )

    async def async_step_person_manual_targets(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 3 (conditional): Manual / Nutritionist kcal and macro targets."""
        if user_input is not None:
            self._person_data.update(user_input)
            return await self.async_step_person_entities()

        return self.async_show_form(
            step_id="person_manual_targets",
            data_schema=_manual_targets_schema(self._person_data),
        )

    async def async_step_person_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 4: Energy-Dashboard-style sensor, todo, and notify mappings."""
        if user_input is not None:
            self._person_data.update(user_input)
            return await self.async_step_person_llm()

        return self.async_show_form(
            step_id="person_entities",
            data_schema=_entities_schema(self._person_data),
        )

    async def async_step_person_llm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 5: Optional per-person LLM provider override."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_LLM_OVERRIDE_ENABLED):
                errors = await _validate_llm_input(self.hass, user_input)
            if not errors:
                self._person_data.update(user_input)
                return await self.async_step_person_diet()

        return self.async_show_form(
            step_id="person_llm",
            data_schema=_llm_override_schema(user_input or self._person_data, self.hass),
            errors=errors,
        )

    async def async_step_person_diet(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 6: Meal slots, dietary regime, allergies, and food blacklist."""
        hub = _find_hub_entry(self.hass)
        hub_lang = (
            merged_entry_config(hub).get(CONF_LANGUAGE, DEFAULT_LANGUAGE)
            if hub
            else DEFAULT_LANGUAGE
        )
        lang_ovr = self._person_data.get(CONF_LANGUAGE_OVERRIDE, "inherit")
        eff_lang = hub_lang if lang_ovr == "inherit" else str(lang_ovr)

        if user_input is not None:
            self._person_data.update(user_input)
            await self.async_set_unique_id(self._person_data[CONF_PERSON_ID])
            self._abort_if_unique_id_configured()
            name = self._person_data[CONF_DISPLAY_NAME]
            return self.async_create_entry(
                title=f"Calories Buddy — {name}", data=self._person_data
            )

        return self.async_show_form(
            step_id="person_diet",
            data_schema=_diet_schema(self._person_data, eff_lang),
        )


class CaloriesBuddyOptionsFlow(config_entries.OptionsFlow):
    """OptionsFlow supporting Hub settings and modular Person Profile editing."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Dispatch to Hub editor or Person section menu."""
        if self.config_entry.data.get(ENTRY_KIND) == ENTRY_KIND_HUB:
            return await self.async_step_hub_options(user_input)
        return self.async_show_menu(
            step_id="init",
            menu_options=["biometrics", "entities", "llm", "diet"],
        )

    async def async_step_hub_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit global Hub LLM, language, USDA key, and baseline notes."""
        current = merged_entry_config(self.config_entry)
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await _validate_llm_input(self.hass, user_input)
            if not errors:
                return self.async_create_entry(
                    title="", data={**self.config_entry.options, **user_input}
                )

        return self.async_show_form(
            step_id="hub_options",
            data_schema=_hub_schema(user_input or current, self.hass),
            errors=errors,
        )

    async def async_step_biometrics(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit Person biometrics, goal, and TDEE settings."""
        current = merged_entry_config(self.config_entry)
        if user_input is not None:
            updated = {**self.config_entry.options, **user_input}
            if user_input.get(CONF_TARGET_MODE) == TargetMode.MANUAL.value:
                self._pending_options = updated
                return await self.async_step_manual_targets()
            return self.async_create_entry(title="", data=updated)

        return self.async_show_form(
            step_id="biometrics", data_schema=_biometrics_schema(current)
        )

    async def async_step_manual_targets(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit Manual / Nutritionist kcal and macro targets."""
        current = merged_entry_config(self.config_entry)
        if user_input is not None:
            base = getattr(self, "_pending_options", dict(self.config_entry.options))
            return self.async_create_entry(title="", data={**base, **user_input})

        return self.async_show_form(
            step_id="manual_targets", data_schema=_manual_targets_schema(current)
        )

    async def async_step_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit mapped HA sensors, shopping list, and notification service."""
        current = merged_entry_config(self.config_entry)
        if user_input is not None:
            return self.async_create_entry(
                title="", data={**self.config_entry.options, **user_input}
            )

        return self.async_show_form(
            step_id="entities", data_schema=_entities_schema(current)
        )

    async def async_step_llm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit per-person LLM override settings."""
        current = merged_entry_config(self.config_entry)
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_LLM_OVERRIDE_ENABLED):
                errors = await _validate_llm_input(self.hass, user_input)
            if not errors:
                return self.async_create_entry(
                    title="", data={**self.config_entry.options, **user_input}
                )

        return self.async_show_form(
            step_id="llm",
            data_schema=_llm_override_schema(user_input or current, self.hass),
            errors=errors,
        )

    async def async_step_diet(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit meal slots, dietary regime, allergies, and blacklist."""
        current = merged_entry_config(self.config_entry)
        lang_ovr = current.get(CONF_LANGUAGE_OVERRIDE, "inherit")
        lang = DEFAULT_LANGUAGE if lang_ovr == "inherit" else str(lang_ovr)
        if user_input is not None:
            return self.async_create_entry(
                title="", data={**self.config_entry.options, **user_input}
            )

        return self.async_show_form(
            step_id="diet", data_schema=_diet_schema(current, lang)
        )
