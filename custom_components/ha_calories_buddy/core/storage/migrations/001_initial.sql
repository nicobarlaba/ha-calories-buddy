-- Initial schema for ha-calories-buddy

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS persons (
    person_id TEXT PRIMARY KEY,
    config_entry_id TEXT NOT NULL UNIQUE,
    ha_person_entity TEXT,
    ha_user_id TEXT,
    display_name TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'en',
    profile_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_persons_user_id ON persons (ha_user_id);
CREATE INDEX IF NOT EXISTS idx_persons_entity ON persons (ha_person_entity);

CREATE TABLE IF NOT EXISTS global_foods (
    food_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    aliases_text TEXT NOT NULL DEFAULT '',
    brand TEXT,
    normalized_brand TEXT,
    barcode_ean TEXT UNIQUE,
    category TEXT NOT NULL DEFAULT 'other',
    preparation_state TEXT NOT NULL DEFAULT 'ready_to_eat',
    kcal_per_100g REAL NOT NULL,
    protein_per_100g REAL NOT NULL,
    carbs_per_100g REAL NOT NULL,
    fat_per_100g REAL NOT NULL,
    fiber_per_100g REAL,
    label_standard TEXT NOT NULL DEFAULT 'eu',
    yield_factor REAL NOT NULL DEFAULT 1.0,
    source TEXT NOT NULL DEFAULT 'manual',
    verified INTEGER NOT NULL DEFAULT 0,
    atwater_warning INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_global_foods_norm_name ON global_foods (normalized_name, normalized_brand);
CREATE INDEX IF NOT EXISTS idx_global_foods_barcode ON global_foods (barcode_ean);

CREATE TABLE IF NOT EXISTS food_portions (
    portion_id TEXT PRIMARY KEY,
    food_id TEXT NOT NULL REFERENCES global_foods(food_id) ON DELETE CASCADE,
    unit_name TEXT NOT NULL,
    grams REAL NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    UNIQUE (food_id, unit_name)
);

CREATE TABLE IF NOT EXISTS person_food_overrides (
    override_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
    food_id TEXT NOT NULL REFERENCES global_foods(food_id) ON DELETE CASCADE,
    preferred_alias TEXT,
    kcal_per_100g REAL,
    protein_per_100g REAL,
    carbs_per_100g REAL,
    fat_per_100g REAL,
    fiber_per_100g REAL,
    yield_factor REAL,
    custom_portions_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    UNIQUE (person_id, food_id)
);

CREATE INDEX IF NOT EXISTS idx_person_food_overrides_person ON person_food_overrides (person_id, food_id);

CREATE TABLE IF NOT EXISTS person_preferences (
    pref_id TEXT PRIMARY KEY,
    person_id TEXT REFERENCES persons(person_id) ON DELETE CASCADE,
    scope TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_value TEXT NOT NULL,
    stance TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_person_preferences_scope ON person_preferences (scope, person_id);

CREATE TABLE IF NOT EXISTS meal_logs (
    log_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
    log_date TEXT NOT NULL,
    meal_slot TEXT NOT NULL,
    logged_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'confirmed',
    source_channel TEXT NOT NULL DEFAULT 'manual',
    raw_input_text TEXT,
    total_kcal REAL NOT NULL,
    total_protein REAL NOT NULL,
    total_carbs REAL NOT NULL,
    total_fat REAL NOT NULL,
    total_fiber REAL,
    plan_item_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_meal_logs_person_date ON meal_logs (person_id, log_date);
CREATE INDEX IF NOT EXISTS idx_meal_logs_status ON meal_logs (person_id, status);

CREATE TABLE IF NOT EXISTS meal_log_items (
    item_id TEXT PRIMARY KEY,
    log_id TEXT NOT NULL REFERENCES meal_logs(log_id) ON DELETE CASCADE,
    food_id TEXT REFERENCES global_foods(food_id) ON DELETE SET NULL,
    display_name TEXT NOT NULL,
    brand TEXT,
    input_quantity REAL NOT NULL,
    input_unit TEXT NOT NULL,
    input_state TEXT NOT NULL,
    resolved_grams_ref REAL NOT NULL,
    kcal REAL NOT NULL,
    protein REAL NOT NULL,
    carbs REAL NOT NULL,
    fat REAL NOT NULL,
    fiber REAL,
    confidence TEXT NOT NULL DEFAULT 'high',
    warning_code TEXT,
    warning_detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_meal_log_items_log ON meal_log_items (log_id);

CREATE TABLE IF NOT EXISTS biometric_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
    snap_date TEXT NOT NULL,
    weight_kg REAL,
    weight_ema_kg REAL,
    body_fat_pct REAL,
    active_kcal REAL,
    total_kcal REAL,
    steps INTEGER,
    computed_tdee REAL,
    target_kcal REAL,
    UNIQUE (person_id, snap_date)
);

CREATE INDEX IF NOT EXISTS idx_biometric_person_date ON biometric_snapshots (person_id, snap_date);

-- Phase 2 & 3 forward-compatible tables

CREATE TABLE IF NOT EXISTS meal_plans (
    plan_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meal_plan_days (
    plan_day_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES meal_plans(plan_id) ON DELETE CASCADE,
    plan_date TEXT NOT NULL,
    target_kcal REAL NOT NULL,
    target_protein REAL NOT NULL,
    target_carbs REAL NOT NULL,
    target_fat REAL NOT NULL,
    UNIQUE (plan_id, plan_date)
);

CREATE TABLE IF NOT EXISTS meal_plan_items (
    plan_item_id TEXT PRIMARY KEY,
    plan_day_id TEXT NOT NULL REFERENCES meal_plan_days(plan_day_id) ON DELETE CASCADE,
    meal_slot TEXT NOT NULL,
    title TEXT NOT NULL,
    recipe_json TEXT NOT NULL DEFAULT '{}',
    ingredients_json TEXT NOT NULL DEFAULT '[]',
    kcal REAL NOT NULL,
    protein REAL NOT NULL,
    carbs REAL NOT NULL,
    fat REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned'
);

CREATE TABLE IF NOT EXISTS shopping_drafts (
    draft_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
    plan_id TEXT REFERENCES meal_plans(plan_id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shopping_draft_items (
    draft_item_id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL REFERENCES shopping_drafts(draft_id) ON DELETE CASCADE,
    food_id TEXT REFERENCES global_foods(food_id) ON DELETE SET NULL,
    label TEXT NOT NULL,
    quantity REAL NOT NULL,
    unit TEXT NOT NULL DEFAULT 'g',
    category TEXT NOT NULL DEFAULT 'other',
    checked INTEGER NOT NULL DEFAULT 1
);
