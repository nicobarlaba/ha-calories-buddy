-- Migration 003: Homemade composite recipes archive

CREATE TABLE IF NOT EXISTS recipes (
    recipe_id TEXT PRIMARY KEY,
    food_id TEXT NOT NULL UNIQUE REFERENCES global_foods(food_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'other',
    servings REAL NOT NULL DEFAULT 1.0,
    serving_unit TEXT NOT NULL DEFAULT 'serving',
    raw_total_grams REAL NOT NULL,
    final_weight_g REAL,
    effective_weight_g REAL NOT NULL,
    allow_in_planner INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    created_by_person_id TEXT REFERENCES persons(person_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_recipes_norm_name ON recipes (normalized_name);
CREATE INDEX IF NOT EXISTS idx_recipes_planner ON recipes (allow_in_planner);

CREATE TABLE IF NOT EXISTS recipe_ingredients (
    ingredient_id TEXT PRIMARY KEY,
    recipe_id TEXT NOT NULL REFERENCES recipes(recipe_id) ON DELETE CASCADE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    food_id TEXT REFERENCES global_foods(food_id) ON DELETE SET NULL,
    display_name TEXT NOT NULL,
    input_quantity REAL NOT NULL,
    input_unit TEXT NOT NULL DEFAULT 'g',
    input_state TEXT NOT NULL DEFAULT 'unspecified',
    grams REAL NOT NULL,
    kcal REAL NOT NULL,
    protein REAL NOT NULL,
    carbs REAL NOT NULL,
    fat REAL NOT NULL,
    fiber REAL
);

CREATE INDEX IF NOT EXISTS idx_recipe_ingredients_recipe ON recipe_ingredients (recipe_id, sort_order);
