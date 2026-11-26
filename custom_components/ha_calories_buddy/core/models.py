"""Domain models and schemas for ha-calories-buddy core."""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """Return current timezone-aware UTC timestamp."""
    return datetime.now(UTC)


class PreparationState(StrEnum):
    """Preparation state of a food item or logged quantity."""

    RAW = "raw"
    COOKED = "cooked"
    READY_TO_EAT = "ready_to_eat"
    UNSPECIFIED = "unspecified"


class LabelStandard(StrEnum):
    """Nutrition labeling convention for fiber/carbohydrate calculation."""

    EU = "eu"  # Carbohydrates exclude fiber (available carbs)
    US = "us"  # Carbohydrates include fiber (total carbs)


class FoodSource(StrEnum):
    """Origin of a food record in the Food Vault."""

    SEED = "seed"
    MANUAL = "manual"
    RECIPE = "recipe"
    OPEN_FOOD_FACTS = "open_food_facts"
    USDA = "usda"
    LLM_ESTIMATED = "llm_estimated"


class ConfidenceLevel(StrEnum):
    """Confidence level of a resolved meal item."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class WarningCode(StrEnum):
    """Warning codes triggering hybrid confirmation or UI review."""

    AMBIGUOUS_STATE = "ambiguous_state"
    ATWATER_WARNING = "atwater_warning"
    LLM_ESTIMATED = "llm_estimated"
    UNKNOWN_UNIT = "unknown_unit"


class MealStatus(StrEnum):
    """Lifecycle state of a logged meal."""

    CONFIRMED = "confirmed"
    PENDING_CONFIRMATION = "pending_confirmation"
    DELETED = "deleted"


class TargetMode(StrEnum):
    """How daily calorie and macro targets are computed."""

    PHYSIOLOGY = "physiology"
    MANUAL = "manual"


class GoalType(StrEnum):
    """Physiological goal for calorie/macro allocation."""

    LOSE_WEIGHT = "lose_weight"
    MAINTAIN = "maintain"
    RECOMPOSITION = "recomposition"
    GAIN_MUSCLE = "gain_muscle"


class TDEEEngineType(StrEnum):
    """Algorithm used to estimate daily Total Daily Energy Expenditure."""

    STATIC = "static"
    WEARABLE_DYNAMIC = "wearable_dynamic"
    ADAPTIVE = "adaptive"


class ActivityLevel(StrEnum):
    """Baseline Physical Activity Level (PAL) multiplier preset."""

    SEDENTARY = "sedentary"
    LIGHT = "light"
    MODERATE = "moderate"
    VERY_ACTIVE = "very_active"
    ATHLETE = "athlete"


class PreferenceScope(StrEnum):
    """Whether a preference applies household-wide or to one person."""

    GLOBAL = "global"
    PERSON = "person"


class PreferenceStance(StrEnum):
    """Hard constraint or soft preference stance."""

    ALLERGY = "allergy"
    INTOLERANCE = "intolerance"
    BLACKLIST = "blacklist"
    DISLIKE = "dislike"
    NEUTRAL = "neutral"
    LIKE = "like"
    LOVE = "love"
    PREFERRED_BRAND = "preferred_brand"


class Sex(StrEnum):
    """Biological sex for BMR equations and conservative safety floors."""

    MALE = "male"
    FEMALE = "female"


class PlanItemStatus(StrEnum):
    """Lifecycle status of a planned meal slot."""

    PLANNED = "planned"
    CONSUMED = "consumed"
    SKIPPED = "skipped"
    REPLACED = "replaced"


class Macros(BaseModel):
    """Macronutrient and energy profile."""

    kcal: float = Field(ge=0.0)
    protein_g: float = Field(ge=0.0)
    carbs_g: float = Field(ge=0.0)
    fat_g: float = Field(ge=0.0)
    fiber_g: float | None = Field(default=None, ge=0.0)


class FoodPortion(BaseModel):
    """Named portion/serving size for a food item."""

    portion_id: str | None = None
    food_id: str
    unit_name: str
    grams: float = Field(gt=0.0)
    description: str = ""


class FoodItem(BaseModel):
    """Global Food Vault item (values per 100g in reference preparation_state)."""

    food_id: str
    name: str
    name_it: str | None = None
    normalized_name: str
    aliases: list[str] = Field(default_factory=list)
    brand: str | None = None
    barcode_ean: str | None = None
    category: str = "other"
    preparation_state: PreparationState = PreparationState.READY_TO_EAT
    macros_per_100g: Macros
    label_standard: LabelStandard = LabelStandard.EU
    yield_factor: float = Field(default=1.0, gt=0.0)
    source: FoodSource = FoodSource.MANUAL
    verified: bool = False
    atwater_warning: bool = False
    portions: list[FoodPortion] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)


class PersonFoodOverride(BaseModel):
    """Per-person customizations applied on top of a global FoodItem."""

    override_id: str
    person_id: str
    food_id: str
    preferred_alias: str | None = None
    kcal_per_100g: float | None = Field(default=None, ge=0.0)
    protein_g_per_100g: float | None = Field(default=None, ge=0.0)
    carbs_g_per_100g: float | None = Field(default=None, ge=0.0)
    fat_g_per_100g: float | None = Field(default=None, ge=0.0)
    fiber_g_per_100g: float | None = Field(default=None, ge=0.0)
    yield_factor: float | None = Field(default=None, gt=0.0)
    custom_portions: dict[str, float] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=utc_now)


class EffectiveFood(BaseModel):
    """Resolved view of a Global Food merged with any Person Food Override."""

    food_id: str
    name: str
    name_it: str | None = None
    normalized_name: str
    aliases: list[str] = Field(default_factory=list)
    preferred_alias: str | None = None
    brand: str | None = None
    barcode_ean: str | None = None
    category: str
    preparation_state: PreparationState
    macros_per_100g: Macros
    label_standard: LabelStandard
    yield_factor: float
    source: FoodSource
    verified: bool
    atwater_warning: bool
    has_override: bool = False
    food_portions: dict[str, float] = Field(default_factory=dict)
    person_portions: dict[str, float] = Field(default_factory=dict)

    def localized_name(self, language: str = "en") -> str:
        """Return person alias first, then Italian seed name when IT is active, else base name."""
        if self.preferred_alias:
            return self.preferred_alias
        if language == "it" and self.name_it:
            return self.name_it
        return self.name


class MealSlotConfig(BaseModel):
    """Customizable meal slot definition for a person."""

    slot_id: str
    name: str
    approx_time: str = "12:30"
    max_cook_minutes: int = Field(default=30, ge=0)
    kcal_share_pct: float | None = Field(default=None, ge=0.0, le=100.0)


class EntityMappings(BaseModel):
    """Home Assistant entity mappings for biophysical sensors, workouts, todo, and notify."""

    weight_entity: str | None = None
    body_fat_entity: str | None = None
    active_kcal_entity: str | None = None
    total_kcal_entity: str | None = None
    steps_entity: str | None = None
    todo_entity: str | None = None
    notify_service: str | None = None
    workout_calendar_entity: str | None = None
    activities_entity: str | None = None


class ManualMacroTarget(BaseModel):
    """Explicit daily calorie and macro targets for Manual / Nutritionist mode."""

    kcal: float = Field(gt=0.0)
    protein_g: float = Field(ge=0.0)
    carbs_g: float = Field(ge=0.0)
    fat_g: float = Field(ge=0.0)
    fiber_g: float | None = Field(default=None, ge=0.0)
    training_kcal: float | None = Field(default=None, gt=0.0)
    training_protein_g: float | None = Field(default=None, ge=0.0)
    training_carbs_g: float | None = Field(default=None, ge=0.0)
    training_fat_g: float | None = Field(default=None, ge=0.0)
    training_fiber_g: float | None = Field(default=None, ge=0.0)


class LLMConfig(BaseModel):
    """In-memory runtime LLM configuration (never serialized with secrets to SQLite)."""

    provider: str = "none"
    api_key: str | None = None
    base_url: str | None = None
    model: str = ""
    ai_task_entity: str | None = None
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    timeout_s: float = Field(default=60.0, gt=0.0, le=600.0)
    max_retries: int = Field(default=2, ge=0, le=5)


class PersonProfile(BaseModel):
    """Persisted configuration and profile for one monitored person."""

    person_id: str
    config_entry_id: str
    ha_person_entity: str | None = None
    ha_user_id: str | None = None
    display_name: str
    language: str = "en"
    sex: Sex = Sex.MALE
    birth_year: int = Field(default=1990, ge=1900, le=2026)
    height_cm: float = Field(default=175.0, gt=50.0, lt=260.0)
    initial_weight_kg: float = Field(default=75.0, gt=20.0, lt=400.0)
    body_fat_pct: float | None = Field(default=None, ge=3.0, le=65.0)
    target_mode: TargetMode = TargetMode.PHYSIOLOGY
    goal_type: GoalType = GoalType.MAINTAIN
    target_weight_kg: float | None = Field(default=None, gt=20.0, lt=400.0)
    target_body_fat_pct: float | None = Field(default=None, ge=3.0, le=65.0)
    weekly_rate_kg: float = Field(default=0.0, ge=-1.5, le=1.5)
    tdee_engine: TDEEEngineType = TDEEEngineType.STATIC
    activity_level: ActivityLevel = ActivityLevel.MODERATE
    wearable_correction_factor: float = Field(default=1.0, gt=0.3, le=1.5)
    manual_targets: ManualMacroTarget | None = None
    entity_mappings: EntityMappings = Field(default_factory=EntityMappings)
    dietary_regime: str = "omnivore"
    allergies: list[str] = Field(default_factory=list)
    intolerances: list[str] = Field(default_factory=list)
    blacklist: list[str] = Field(default_factory=list)
    meal_slots: list[MealSlotConfig] = Field(default_factory=list)
    llm_override_enabled: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class PreferenceEntry(BaseModel):
    """Two-tier food/brand preference or hard dietary constraint."""

    pref_id: str
    person_id: str | None = None
    scope: PreferenceScope
    target_type: str
    target_value: str
    stance: PreferenceStance
    notes: str = ""
    updated_at: datetime = Field(default_factory=utc_now)


class MealLogItem(BaseModel):
    """Single resolved food item inside a MealLog."""

    item_id: str
    log_id: str
    food_id: str | None = None
    display_name: str
    brand: str | None = None
    input_quantity: float = Field(gt=0.0)
    input_unit: str
    input_state: PreparationState
    resolved_grams_ref: float = Field(ge=0.0)
    macros: Macros
    confidence: ConfidenceLevel = ConfidenceLevel.HIGH
    warning_code: WarningCode | None = None
    warning_detail: str | None = None


class MealLog(BaseModel):
    """Logged meal entry for a person on a given date."""

    log_id: str
    person_id: str
    log_date: date
    meal_slot: str
    logged_at: datetime = Field(default_factory=utc_now)
    status: MealStatus = MealStatus.CONFIRMED
    source_channel: str = "manual"
    raw_input_text: str | None = None
    total_macros: Macros
    plan_item_id: str | None = None
    items: list[MealLogItem] = Field(default_factory=list)


class DailyTotals(BaseModel):
    """Aggregated nutrition totals for a single date."""

    log_date: date
    consumed: Macros
    confirmed_count: int = 0
    pending_count: int = 0
    by_slot: dict[str, Macros] = Field(default_factory=dict)


class BiometricSnapshot(BaseModel):
    """Daily biophysical snapshot captured from mapped HA sensors or manual input."""

    snapshot_id: str
    person_id: str
    snap_date: date
    weight_kg: float | None = Field(default=None, gt=20.0, lt=400.0)
    weight_ema_kg: float | None = Field(default=None, gt=20.0, lt=400.0)
    body_fat_pct: float | None = Field(default=None, ge=3.0, le=65.0)
    body_fat_ema_pct: float | None = Field(default=None, ge=3.0, le=65.0)
    active_kcal: float | None = Field(default=None, ge=0.0)
    total_kcal: float | None = Field(default=None, ge=0.0)
    steps: int | None = Field(default=None, ge=0)
    computed_tdee: float | None = Field(default=None, ge=0.0)
    target_kcal: float | None = Field(default=None, ge=0.0)
    adaptive_tdee_kcal: float | None = Field(default=None, ge=0.0)
    wearable_multiplier_used: float | None = Field(default=None, gt=0.3, le=1.5)


# Phase 2 / Phase 3 forward-compatible domain models


class IngredientRole(StrEnum):
    """Functional role of an ingredient for bounded deterministic macro scaling."""

    PROTEIN_ANCHOR = "protein_anchor"
    CARB_ANCHOR = "carb_anchor"
    FAT_ANCHOR = "fat_anchor"
    PRODUCE_FIXED = "produce_fixed"
    CONDIMENT_FIXED = "condiment_fixed"


class PlannedIngredient(BaseModel):
    """Resolved and macro-scaled ingredient belonging to a planned meal or recipe."""

    food_id: str | None = None
    name: str
    brand: str | None = None
    grams: float = Field(gt=0.0)
    preparation_state: PreparationState = PreparationState.READY_TO_EAT
    role: IngredientRole = IngredientRole.PRODUCE_FIXED
    course: str | None = None
    macros: Macros


class RecipeDetail(BaseModel):
    """Structured recipe metadata serialized into meal_plan_items.recipe_json."""

    prep_minutes: int = Field(default=20, ge=0)
    steps: list[str] = Field(default_factory=list)
    tips: str = ""
    macro_fit_warning: bool = False


class MealFeedbackRating(StrEnum):
    """User rating for a suggested or planned meal/recipe."""

    LIKE = "like"
    DISLIKE = "dislike"
    NEVER_SUGGEST = "never_suggest"


class MealFeedback(BaseModel):
    """Persisted per-person rating on a dish/recipe signature."""

    feedback_id: str
    person_id: str
    recipe_signature: str
    title: str
    rating: MealFeedbackRating
    notes: str = ""
    updated_at: datetime = Field(default_factory=utc_now)


class MealPlanItem(BaseModel):
    """Single planned meal in a MealPlanDay."""

    plan_item_id: str
    plan_day_id: str
    meal_slot: str
    title: str
    recipe_json: str = "{}"
    ingredients_json: str = "[]"
    macros: Macros
    prep_minutes: int = 20
    macro_fit_warning: bool = False
    status: PlanItemStatus = PlanItemStatus.PLANNED


class MealSuggestion(BaseModel):
    """On-demand AI-proposed & deterministically scaled meal option."""

    title: str
    meal_slot: str
    prep_minutes: int = 20
    steps: list[str] = Field(default_factory=list)
    tips: str = ""
    ingredients: list[PlannedIngredient] = Field(default_factory=list)
    macros: Macros
    target_macros: Macros | None = None
    budget_mode: str = "auto"
    macro_fit_warning: bool = False


class PlanGenerationOptions(BaseModel):
    """Options controlling multi-day meal plan generation."""

    num_days: int = Field(default=7, ge=1, le=7)
    meal_prep_mode: bool = False
    notes: str = ""
    max_cook_overrides: dict[str, int] = Field(default_factory=dict)


class RebalanceResult(BaseModel):
    """Result of an intra-day or multi-day meal plan rebalance."""

    mode: str
    updated_items: list[MealPlanItem] = Field(default_factory=list)
    updated_days: list[date] = Field(default_factory=list)
    remaining_before: Macros
    remaining_after: Macros
    safety_warnings: list[str] = Field(default_factory=list)
    summary_message: str


class MealPlanDay(BaseModel):
    """One day inside a MealPlan."""

    plan_day_id: str
    plan_id: str
    plan_date: date
    target_macros: Macros
    items: list[MealPlanItem] = Field(default_factory=list)


class MealPlan(BaseModel):
    """Multi-day meal plan for a person."""

    plan_id: str
    person_id: str
    start_date: date
    end_date: date
    status: str = "active"
    created_at: datetime = Field(default_factory=utc_now)
    days: list[MealPlanDay] = Field(default_factory=list)


class ShoppingDraftItem(BaseModel):
    """Editable grocery item inside a ShoppingDraft."""

    draft_item_id: str
    draft_id: str
    food_id: str | None = None
    label: str
    brand: str | None = None
    quantity: float = Field(gt=0.0)
    unit: str = "g"
    category: str = "other"
    aisle: str = "other"
    checked: bool = True
    source_meals: list[str] = Field(default_factory=list)


class ShoppingDraft(BaseModel):
    """Aggregated editable grocery draft linked to one or more MealPlans."""

    draft_id: str
    person_id: str
    plan_id: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    scope_person_ids: list[str] = Field(default_factory=list)
    status: str = "draft"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    items: list[ShoppingDraftItem] = Field(default_factory=list)


class WorkoutActivityType(StrEnum):
    """Supported recurring workout discipline classifications."""

    STRENGTH = "strength"
    CARDIO = "cardio"
    RUNNING = "running"
    CYCLING = "cycling"
    HIIT = "hiit"
    MOBILITY = "mobility"
    SPORT = "sport"
    OTHER = "other"


class WorkoutScheduleItem(BaseModel):
    """Recurring weekly workout entry (weekday 0=Monday .. 6=Sunday)."""

    schedule_id: str
    person_id: str
    weekday: int = Field(ge=0, le=6)
    title: str
    activity_type: WorkoutActivityType = WorkoutActivityType.STRENGTH
    duration_min: int = Field(default=60, ge=5, le=480)
    estimated_kcal: float | None = Field(default=None, ge=0.0)
    time_of_day: str = "18:00"
    notes: str = ""
    enabled: bool = True
    updated_at: datetime = Field(default_factory=utc_now)


class TrainingDayOverride(BaseModel):
    """Manual or explicit per-date override for training-day status."""

    person_id: str
    override_date: date
    is_training_day: bool
    source: str = "manual"
    updated_at: datetime = Field(default_factory=utc_now)


class CompletedActivity(BaseModel):
    """Completed workout activity read from mapped Garmin/wearable sensor."""

    activity_id: str = ""
    name: str
    activity_type: str = "other"
    start_time: str | None = None
    duration_min: float | None = None
    calories: float | None = None
    distance_km: float | None = None


class ScheduledCalendarWorkout(BaseModel):
    """Scheduled future workout read from mapped Home Assistant calendar.* entity."""

    summary: str
    start: str
    end: str | None = None
    description: str = ""


class AdaptiveState(BaseModel):
    """Persisted closed-loop Adaptive TDEE state per person."""

    person_id: str
    last_calibrated_date: date | None = None
    adaptive_tdee_kcal: float | None = None
    wearable_multiplier: float = Field(default=1.0, gt=0.3, le=1.5)
    confidence: str = "bootstrapping"
    updated_at: datetime = Field(default_factory=utc_now)


class TrendPoint(BaseModel):
    """Single day telemetry point for Trends charts and analytics."""

    point_date: date
    kcal_consumed: float = 0.0
    kcal_target: float | None = None
    protein_g: float = 0.0
    carbs_g: float = 0.0
    fat_g: float = 0.0
    fiber_g: float = 0.0
    weight_kg: float | None = None
    weight_ema_kg: float | None = None
    body_fat_pct: float | None = None
    body_fat_ema_pct: float | None = None
    lean_mass_kg: float | None = None
    tdee_kcal: float | None = None
    steps: int | None = None
    adherence_pct: float | None = None
    is_logged: bool = False


class ExpectedVsActualSummary(BaseModel):
    """Goal pace comparison and ETA projection based on weight EMA."""

    expected_weekly_rate_kg: float
    actual_weekly_rate_kg: float | None = None
    status: str = "insufficient_data"
    current_weight_ema_kg: float | None = None
    target_weight_kg: float | None = None
    current_body_fat_ema_pct: float | None = None
    target_body_fat_pct: float | None = None
    eta_date: date | None = None
    eta_weeks: float | None = None


class WeeklyAdherenceBucket(BaseModel):
    """7-day adherence rollup bucket."""

    week_start: date
    week_end: date
    logged_days: int
    total_days: int
    avg_kcal: float
    avg_target_kcal: float
    adherence_pct: float


class TrendsSummary(BaseModel):
    """Complete 7/30/90d nutrition, body composition, and TDEE trends response."""

    person_id: str
    days: int
    start_date: date
    end_date: date
    points: list[TrendPoint]
    expected_vs_actual: ExpectedVsActualSummary
    logged_days: int
    total_days: int
    logging_adherence_pct: float
    avg_kcal: float
    avg_target_kcal: float
    kcal_adherence_pct: float
    avg_protein_g: float
    avg_carbs_g: float
    avg_fat_g: float
    avg_fiber_g: float
    target_protein_g: float
    target_carbs_g: float
    target_fat_g: float
    target_fiber_g: float
    engine_used: str
    adaptive_confidence: str | None = None
    wearable_multiplier: float | None = None
    adaptive_tdee_kcal: float | None = None
    two_compartment_used: bool = False
    fallback_engine: str | None = None
    weekly_adherence: list[WeeklyAdherenceBucket] = Field(default_factory=list)


class RecipeIngredientInput(BaseModel):
    """Caller-supplied ingredient specification for creating/updating a recipe."""

    food_id: str | None = None
    name: str
    quantity: float = Field(gt=0.0)
    unit: str = "g"
    state: PreparationState = PreparationState.UNSPECIFIED


class RecipeIngredient(BaseModel):
    """Persisted, Vault-resolved ingredient inside a HomemadeRecipe."""

    ingredient_id: str
    recipe_id: str
    sort_order: int = 0
    food_id: str | None = None
    display_name: str
    input_quantity: float = Field(gt=0.0)
    input_unit: str = "g"
    input_state: PreparationState = PreparationState.UNSPECIFIED
    grams: float = Field(gt=0.0)
    macros: Macros


class HomemadeRecipe(BaseModel):
    """Reusable homemade multi-ingredient recipe synced to a Global Food Vault item."""

    recipe_id: str
    food_id: str
    name: str
    normalized_name: str
    category: str = "other"
    servings: float = Field(default=1.0, gt=0.0)
    serving_unit: str = "serving"
    raw_total_grams: float = Field(gt=0.0)
    final_weight_g: float | None = Field(default=None, gt=0.0)
    effective_weight_g: float = Field(gt=0.0)
    grams_per_serving: float = Field(gt=0.0)
    total_macros: Macros
    macros_per_100g: Macros
    macros_per_serving: Macros
    allow_in_planner: bool = False
    notes: str = ""
    created_by_person_id: str | None = None
    ingredients: list[RecipeIngredient] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
