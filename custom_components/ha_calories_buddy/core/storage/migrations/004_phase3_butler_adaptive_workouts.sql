-- Migration 004: Phase 3 The Butler (shopping drafts), Adaptive TDEE, and Workouts

ALTER TABLE biometric_snapshots ADD COLUMN body_fat_ema_pct REAL;
ALTER TABLE biometric_snapshots ADD COLUMN adaptive_tdee_kcal REAL;
ALTER TABLE biometric_snapshots ADD COLUMN wearable_multiplier_used REAL;

ALTER TABLE shopping_drafts ADD COLUMN start_date TEXT;
ALTER TABLE shopping_drafts ADD COLUMN end_date TEXT;
ALTER TABLE shopping_drafts ADD COLUMN scope_person_ids_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE shopping_drafts ADD COLUMN updated_at TEXT;

ALTER TABLE shopping_draft_items ADD COLUMN brand TEXT;
ALTER TABLE shopping_draft_items ADD COLUMN aisle TEXT NOT NULL DEFAULT 'other';
ALTER TABLE shopping_draft_items ADD COLUMN source_meals_json TEXT NOT NULL DEFAULT '[]';

CREATE TABLE IF NOT EXISTS workout_schedules (
    schedule_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
    weekday INTEGER NOT NULL,
    title TEXT NOT NULL,
    activity_type TEXT NOT NULL DEFAULT 'strength',
    duration_min INTEGER NOT NULL DEFAULT 60,
    estimated_kcal REAL,
    time_of_day TEXT NOT NULL DEFAULT '18:00',
    notes TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workout_schedules_person_day
    ON workout_schedules (person_id, weekday);

CREATE TABLE IF NOT EXISTS training_day_overrides (
    person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
    override_date TEXT NOT NULL,
    is_training_day INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (person_id, override_date)
);

CREATE TABLE IF NOT EXISTS adaptive_state (
    person_id TEXT PRIMARY KEY REFERENCES persons(person_id) ON DELETE CASCADE,
    last_calibrated_date TEXT,
    adaptive_tdee_kcal REAL,
    wearable_multiplier REAL NOT NULL DEFAULT 1.0,
    confidence TEXT NOT NULL DEFAULT 'bootstrapping',
    updated_at TEXT NOT NULL
);
