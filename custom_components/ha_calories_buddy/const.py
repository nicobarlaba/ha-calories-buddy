"""Constants for the HA Calories Buddy integration."""

from __future__ import annotations

DOMAIN = "ha_calories_buddy"
VERSION = "0.2.1"

STORAGE_DB_FILENAME = "ha_calories_buddy.db"

ENTRY_KIND = "entry_kind"
ENTRY_KIND_HUB = "hub"
ENTRY_KIND_PERSON = "person"

SUPPORTED_LANGUAGES = ["en", "it"]
DEFAULT_LANGUAGE = "en"

# Frontend / Lovelace auto-registration constants
FRONTEND_URL_BASE = f"/{DOMAIN}/frontend"
FRONTEND_CARD_FILENAME = "ha-calories-buddy-card.js"
FRONTEND_CARD_URL = f"{FRONTEND_URL_BASE}/{FRONTEND_CARD_FILENAME}?v={VERSION}"

# Config keys — Hub
CONF_LLM_PROVIDER = "llm_provider"
CONF_LLM_API_KEY = "llm_api_key"
CONF_LLM_BASE_URL = "llm_base_url"
CONF_LLM_MODEL = "llm_model"
CONF_AI_TASK_ENTITY = "ai_task_entity"
CONF_LLM_TEMPERATURE = "llm_temperature"
CONF_LANGUAGE = "language"
CONF_USDA_API_KEY = "usda_api_key"
CONF_GLOBAL_BASELINE_NOTES = "global_baseline_notes"

# Config keys — Person Identity
CONF_PERSON_ID = "person_id"
CONF_HA_PERSON_ENTITY = "ha_person_entity"
CONF_HA_USER_ID = "ha_user_id"
CONF_DISPLAY_NAME = "display_name"
CONF_LANGUAGE_OVERRIDE = "language_override"

# Config keys — Biometrics & Targets
CONF_SEX = "sex"
CONF_BIRTH_YEAR = "birth_year"
CONF_HEIGHT_CM = "height_cm"
CONF_INITIAL_WEIGHT_KG = "initial_weight_kg"
CONF_BODY_FAT_PCT = "body_fat_pct"
CONF_TARGET_MODE = "target_mode"
CONF_GOAL_TYPE = "goal_type"
CONF_TARGET_WEIGHT_KG = "target_weight_kg"
CONF_TARGET_BODY_FAT_PCT = "target_body_fat_pct"
CONF_WEEKLY_RATE_KG = "weekly_rate_kg"
CONF_TDEE_ENGINE = "tdee_engine"
CONF_ACTIVITY_LEVEL = "activity_level"
CONF_WEARABLE_CORRECTION_FACTOR = "wearable_correction_factor"

# Manual targets
CONF_MANUAL_KCAL = "manual_kcal"
CONF_MANUAL_PROTEIN_G = "manual_protein_g"
CONF_MANUAL_CARBS_G = "manual_carbs_g"
CONF_MANUAL_FAT_G = "manual_fat_g"
CONF_MANUAL_FIBER_G = "manual_fiber_g"
CONF_MANUAL_TRAINING_KCAL = "manual_training_kcal"
CONF_MANUAL_TRAINING_PROTEIN_G = "manual_training_protein_g"
CONF_MANUAL_TRAINING_CARBS_G = "manual_training_carbs_g"
CONF_MANUAL_TRAINING_FAT_G = "manual_training_fat_g"
CONF_MANUAL_TRAINING_FIBER_G = "manual_training_fiber_g"

# Config keys — Entity Mappings
CONF_WEIGHT_ENTITY = "weight_entity"
CONF_BODY_FAT_ENTITY = "body_fat_entity"
CONF_ACTIVE_KCAL_ENTITY = "active_kcal_entity"
CONF_TOTAL_KCAL_ENTITY = "total_kcal_entity"
CONF_STEPS_ENTITY = "steps_entity"
CONF_TODO_ENTITY = "todo_entity"
CONF_NOTIFY_SERVICE = "notify_service"

# Config keys — LLM Override
CONF_LLM_OVERRIDE_ENABLED = "llm_override_enabled"

# Config keys — Diet & Meal Slots
CONF_DIETARY_REGIME = "dietary_regime"
CONF_ALLERGIES = "allergies"
CONF_INTOLERANCES = "intolerances"
CONF_BLACKLIST = "blacklist"
CONF_MEAL_SLOTS = "meal_slots"

# Defaults
DEFAULT_MEAL_SLOTS_EN = "Breakfast, Lunch, Snack, Dinner"
DEFAULT_MEAL_SLOTS_IT = "Colazione, Pranzo, Spuntino, Cena"
DEFAULT_LLM_TEMPERATURE = 0.2
DEFAULT_LLM_TIMEOUT_S = 30.0
DEFAULT_WEARABLE_CORRECTION_FACTOR = 1.0
ATWATER_TOLERANCE_PCT = 0.15
ATWATER_TOLERANCE_KCAL = 25.0

# LLM Provider identifiers
LLM_PROVIDER_NONE = "none"
LLM_PROVIDER_HA_AI_TASK = "ha_ai_task"
LLM_PROVIDER_OPENAI = "openai"
LLM_PROVIDER_ANTHROPIC = "anthropic"
LLM_PROVIDER_GEMINI = "gemini"
LLM_PROVIDER_OLLAMA = "ollama"
LLM_PROVIDER_OPENAI_COMPAT = "openai_compatible"

LLM_PROVIDERS = [
    LLM_PROVIDER_NONE,
    LLM_PROVIDER_HA_AI_TASK,
    LLM_PROVIDER_OPENAI,
    LLM_PROVIDER_ANTHROPIC,
    LLM_PROVIDER_GEMINI,
    LLM_PROVIDER_OLLAMA,
    LLM_PROVIDER_OPENAI_COMPAT,
]

# Event / Service / WS constants
ATTR_PERSON = "person"
ATTR_PERSON_ID = "person_id"
ATTR_TEXT = "text"
ATTR_DATE = "date"
ATTR_MEAL_SLOT = "meal_slot"
ATTR_FOOD_ID = "food_id"
ATTR_QUANTITY = "quantity"
ATTR_UNIT = "unit"
ATTR_STATE = "state"
ATTR_LOG_ID = "log_id"
ATTR_ITEM_ID = "item_id"
ATTR_BARCODE = "barcode"
ATTR_WEIGHT_KG = "weight_kg"
