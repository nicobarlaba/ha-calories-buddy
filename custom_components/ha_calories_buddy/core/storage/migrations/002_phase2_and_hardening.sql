-- Migration 002: Bilingual seed display names, fiber plan tracking, and meal feedback

ALTER TABLE global_foods ADD COLUMN name_it TEXT;

ALTER TABLE meal_plan_days ADD COLUMN target_fiber REAL;
ALTER TABLE meal_plan_items ADD COLUMN fiber REAL;
ALTER TABLE meal_plan_items ADD COLUMN prep_minutes INTEGER NOT NULL DEFAULT 20;

CREATE TABLE IF NOT EXISTS meal_feedback (
    feedback_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
    recipe_signature TEXT NOT NULL,
    title TEXT NOT NULL,
    rating TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    UNIQUE (person_id, recipe_signature)
);

CREATE INDEX IF NOT EXISTS idx_meal_feedback_person ON meal_feedback (person_id, rating);
CREATE INDEX IF NOT EXISTS idx_meal_plans_person_status ON meal_plans (person_id, status, start_date);
CREATE INDEX IF NOT EXISTS idx_meal_plan_days_plan_date ON meal_plan_days (plan_id, plan_date);
CREATE INDEX IF NOT EXISTS idx_meal_plan_items_day_status ON meal_plan_items (plan_day_id, status);
