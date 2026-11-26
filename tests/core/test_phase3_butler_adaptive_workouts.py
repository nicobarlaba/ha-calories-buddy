"""Core unit & repository tests for Phase 3 (Butler Shopping, Workouts, Adaptive TDEE, Trends)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from custom_components.ha_calories_buddy.core.models import (
    ActivityLevel,
    AdaptiveState,
    BiometricSnapshot,
    FoodItem,
    FoodSource,
    GoalType,
    IngredientRole,
    LabelStandard,
    Macros,
    MealLog,
    MealLogItem,
    MealPlan,
    MealPlanDay,
    MealPlanItem,
    MealSlotConfig,
    MealStatus,
    PersonProfile,
    PlannedIngredient,
    PreferenceScope,
    PreferenceStance,
    PreparationState,
    ScheduledCalendarWorkout,
    Sex,
    ShoppingDraftItem,
    TargetMode,
    TrainingDayOverride,
    WorkoutActivityType,
    WorkoutScheduleItem,
)
from custom_components.ha_calories_buddy.core.nutrition.resolver import FoodResolver
from custom_components.ha_calories_buddy.core.physiology.targets import TargetResult
from custom_components.ha_calories_buddy.core.physiology.tdee import (
    calculate_adaptive_tdee,
    recompute_biometric_emas,
    update_ema,
)
from custom_components.ha_calories_buddy.core.physiology.trends import (
    build_trends_summary,
)
from custom_components.ha_calories_buddy.core.planner.preferences import (
    PreferenceEngine,
)
from custom_components.ha_calories_buddy.core.planner.shopping import (
    ShoppingListService,
    convert_planned_to_purchase_grams,
    filter_deduplicated_items,
    format_purchase_quantity_and_unit,
    format_todo_item_summary,
)
from custom_components.ha_calories_buddy.core.storage.database import Database
from custom_components.ha_calories_buddy.core.storage.food_repo import FoodRepository
from custom_components.ha_calories_buddy.core.storage.meal_repo import MealRepository
from custom_components.ha_calories_buddy.core.storage.plan_repo import MealPlanRepository
from custom_components.ha_calories_buddy.core.storage.preference_repo import (
    PreferenceRepository,
)
from custom_components.ha_calories_buddy.core.storage.profile_repo import ProfileRepository
from custom_components.ha_calories_buddy.core.storage.seeder import seed_database_if_empty
from custom_components.ha_calories_buddy.core.storage.shopping_repo import (
    ShoppingRepository,
)
from custom_components.ha_calories_buddy.core.storage.workout_repo import (
    WorkoutRepository,
)


@pytest.fixture
async def phase3_ctx(tmp_path: Path) -> Any:
    db = Database(tmp_path / "phase3.db")
    await db.initialize()
    await seed_database_if_empty(db)

    profile_repo = ProfileRepository(db)
    food_repo = FoodRepository(db)
    meal_repo = MealRepository(db)
    plan_repo = MealPlanRepository(db)
    pref_repo = PreferenceRepository(db)
    shopping_repo = ShoppingRepository(db)
    workout_repo = WorkoutRepository(db)

    global_notes = ["Italian Mediterranean family cooking"]
    pref_engine = PreferenceEngine(
        pref_repo,
        meal_repo,
        global_notes_getter=lambda: global_notes[0],
    )
    shopping_svc = ShoppingListService(
        plan_repo=plan_repo,
        meal_repo=meal_repo,
        food_repo=food_repo,
        shopping_repo=shopping_repo,
        preference_engine=pref_engine,
    )

    marco = PersonProfile(
        person_id="p_marco",
        config_entry_id="ce_marco",
        display_name="Marco",
        language="it",
        sex=Sex.MALE,
        birth_year=1990,
        height_cm=180.0,
        initial_weight_kg=80.0,
        goal_type=GoalType.LOSE_WEIGHT,
        weekly_rate_kg=-0.4,
        target_weight_kg=75.0,
        target_body_fat_pct=15.0,
        meal_slots=[
            MealSlotConfig(slot_id="colazione", name="Colazione"),
            MealSlotConfig(slot_id="pranzo", name="Pranzo"),
            MealSlotConfig(slot_id="cena", name="Cena"),
        ],
    )
    giulia = PersonProfile(
        person_id="p_giulia",
        config_entry_id="ce_giulia",
        display_name="Giulia",
        language="it",
        sex=Sex.FEMALE,
        birth_year=1993,
        height_cm=166.0,
        initial_weight_kg=60.0,
        goal_type=GoalType.MAINTAIN,
        meal_slots=[
            MealSlotConfig(slot_id="colazione", name="Colazione"),
            MealSlotConfig(slot_id="pranzo", name="Pranzo"),
            MealSlotConfig(slot_id="cena", name="Cena"),
        ],
    )
    await profile_repo.upsert_person(marco)
    await profile_repo.upsert_person(giulia)

    await food_repo.upsert_global_food(
        FoodItem(
            food_id="rice_raw",
            name="Basmati Rice (Raw)",
            name_it="Riso Basmati (Crudo)",
            normalized_name="riso basmati crudo",
            category="rice",
            preparation_state=PreparationState.RAW,
            macros_per_100g=Macros(kcal=350, protein_g=7.5, carbs_g=78, fat_g=0.8, fiber_g=1.4),
            label_standard=LabelStandard.EU,
            yield_factor=2.5,
            source=FoodSource.SEED,
            verified=True,
        )
    )
    await food_repo.upsert_global_food(
        FoodItem(
            food_id="chicken_raw",
            name="Chicken Breast (Raw)",
            name_it="Petto di Pollo (Crudo)",
            normalized_name="petto di pollo crudo",
            category="poultry",
            preparation_state=PreparationState.RAW,
            macros_per_100g=Macros(kcal=110, protein_g=23.0, carbs_g=0.0, fat_g=1.2, fiber_g=0.0),
            label_standard=LabelStandard.EU,
            yield_factor=0.75,
            source=FoodSource.SEED,
            verified=True,
        )
    )
    await food_repo.upsert_global_food(
        FoodItem(
            food_id="eggs_whole",
            name="Whole Eggs",
            name_it="Uova intere",
            normalized_name="uova intere",
            category="egg",
            preparation_state=PreparationState.RAW,
            macros_per_100g=Macros(kcal=143, protein_g=12.6, carbs_g=0.7, fat_g=9.5, fiber_g=0.0),
            label_standard=LabelStandard.EU,
            yield_factor=1.0,
            source=FoodSource.SEED,
            verified=True,
        )
    )

    yield {
        "db": db,
        "profile_repo": profile_repo,
        "food_repo": food_repo,
        "meal_repo": meal_repo,
        "plan_repo": plan_repo,
        "pref_repo": pref_repo,
        "shopping_repo": shopping_repo,
        "workout_repo": workout_repo,
        "pref_engine": pref_engine,
        "shopping_svc": shopping_svc,
        "marco": marco,
        "giulia": giulia,
        "global_notes": global_notes,
    }
    await db.close()


def test_shopping_conversion_units_and_dedup() -> None:
    """Verify cooked->raw purchase conversion, egg pcs rounding, formatting, and todo dedup."""
    # 250g cooked rice (yield 2.5) -> 100g raw rice; 150g cooked chicken (0.75) -> 200g raw chicken
    assert convert_planned_to_purchase_grams(
        250.0, PreparationState.COOKED, "rice", 2.5, PreparationState.RAW, "Basmati Rice"
    ) == pytest.approx(100.0)
    assert convert_planned_to_purchase_grams(
        150.0, PreparationState.COOKED, "poultry", 0.75, PreparationState.RAW, "Chicken Breast"
    ) == pytest.approx(200.0)
    # Canned legumes remain as cooked net weight
    assert convert_planned_to_purchase_grams(
        240.0, PreparationState.COOKED, "legumes", 2.4, PreparationState.COOKED, "Lenticchie in scatola"
    ) == pytest.approx(240.0)

    assert format_purchase_quantity_and_unit(180.0, "egg", "Uova intere") == (3.0, "pcs")
    assert format_purchase_quantity_and_unit(500.0, "dairy", "Latte scremato") == (500.0, "ml")
    assert format_purchase_quantity_and_unit(183.0, "poultry", "Petto di Pollo") == (190.0, "g")

    draft_items = [
        ShoppingDraftItem(
            draft_item_id="i1",
            draft_id="d1",
            label="Riso Basmati",
            brand="Scotti",
            quantity=1000,
            unit="g",
            aisle="pantry",
            checked=True,
        ),
        ShoppingDraftItem(
            draft_item_id="i2",
            draft_id="d1",
            label="Uova intere",
            quantity=6,
            unit="pcs",
            aisle="dairy_eggs",
            checked=True,
        ),
        ShoppingDraftItem(
            draft_item_id="i3",
            draft_id="d1",
            label="Spinaci freschi",
            quantity=200,
            unit="g",
            aisle="produce",
            checked=False,
        ),
    ]
    assert format_todo_item_summary(draft_items[0], "it") == "Riso Basmati (Scotti) — 1 kg"
    assert format_todo_item_summary(draft_items[1], "it") == "Uova intere — 6 pz"

    to_add, skipped = filter_deduplicated_items(
        draft_items, ["Riso Basmati 500g", "Banane"]
    )
    assert [it.label for it in to_add] == ["Uova intere"]
    assert [it.label for it in skipped] == ["Riso Basmati"]


async def test_butler_shopping_aggregation_household_merge_and_crud(
    phase3_ctx: dict[str, Any],
) -> None:
    """Verify shopping aggregation merges family plans, skips covered slots, applies brands, and supports CRUD."""
    marco: PersonProfile = phase3_ctx["marco"]
    giulia: PersonProfile = phase3_ctx["giulia"]
    plan_repo: MealPlanRepository = phase3_ctx["plan_repo"]
    meal_repo: MealRepository = phase3_ctx["meal_repo"]
    pref_repo: PreferenceRepository = phase3_ctx["pref_repo"]
    shopping_repo: ShoppingRepository = phase3_ctx["shopping_repo"]
    shopping_svc: ShoppingListService = phase3_ctx["shopping_svc"]

    await pref_repo.upsert_preference(
        scope=PreferenceScope.GLOBAL,
        person_id=None,
        target_type="brand",
        target_value="Scotti",
        stance=PreferenceStance.PREFERRED_BRAND,
        notes="riso basmati",
    )

    today = date(2026, 11, 26)
    # Marco's plan: Lunch today (Rice 250g cooked + Chicken 150g cooked), Dinner today (Eggs 120g)
    marco_plan = MealPlan(
        plan_id="plan_m",
        person_id=marco.person_id,
        start_date=today,
        end_date=today,
        days=[
            MealPlanDay(
                plan_day_id="day_m0",
                plan_id="plan_m",
                plan_date=today,
                target_macros=Macros(kcal=2000, protein_g=150, carbs_g=200, fat_g=60),
                items=[
                    MealPlanItem(
                        plan_item_id="pm_lunch",
                        plan_day_id="day_m0",
                        meal_slot="Pranzo",
                        title="Pollo e Riso",
                        ingredients_json=json.dumps(
                            [
                                PlannedIngredient(
                                    food_id="rice_raw",
                                    name="Basmati Rice (Raw)",
                                    grams=250.0,
                                    preparation_state=PreparationState.COOKED,
                                    role=IngredientRole.CARB_ANCHOR,
                                    macros=Macros(kcal=350, protein_g=7.5, carbs_g=78, fat_g=0.8),
                                ).model_dump(mode="json"),
                                PlannedIngredient(
                                    food_id="chicken_raw",
                                    name="Chicken Breast (Raw)",
                                    grams=150.0,
                                    preparation_state=PreparationState.COOKED,
                                    role=IngredientRole.PROTEIN_ANCHOR,
                                    macros=Macros(kcal=220, protein_g=46, carbs_g=0, fat_g=2.4),
                                ).model_dump(mode="json"),
                            ]
                        ),
                        macros=Macros(kcal=570, protein_g=53.5, carbs_g=78, fat_g=3.2),
                    ),
                    MealPlanItem(
                        plan_item_id="pm_dinner",
                        plan_day_id="day_m0",
                        meal_slot="Cena",
                        title="Frittata veloce",
                        ingredients_json=json.dumps(
                            [
                                PlannedIngredient(
                                    food_id="eggs_whole",
                                    name="Whole Eggs",
                                    grams=120.0,
                                    preparation_state=PreparationState.RAW,
                                    role=IngredientRole.PROTEIN_ANCHOR,
                                    macros=Macros(kcal=172, protein_g=15, carbs_g=1, fat_g=11),
                                ).model_dump(mode="json")
                            ]
                        ),
                        macros=Macros(kcal=172, protein_g=15, carbs_g=1, fat_g=11),
                    ),
                ],
            )
        ],
    )
    # Giulia's plan: Dinner today also has 150g raw rice
    giulia_plan = MealPlan(
        plan_id="plan_g",
        person_id=giulia.person_id,
        start_date=today,
        end_date=today,
        days=[
            MealPlanDay(
                plan_day_id="day_g0",
                plan_id="plan_g",
                plan_date=today,
                target_macros=Macros(kcal=1700, protein_g=120, carbs_g=180, fat_g=55),
                items=[
                    MealPlanItem(
                        plan_item_id="pg_dinner",
                        plan_day_id="day_g0",
                        meal_slot="Cena",
                        title="Riso per Giulia",
                        ingredients_json=json.dumps(
                            [
                                PlannedIngredient(
                                    food_id="rice_raw",
                                    name="Basmati Rice (Raw)",
                                    grams=150.0,
                                    preparation_state=PreparationState.RAW,
                                    role=IngredientRole.CARB_ANCHOR,
                                    macros=Macros(kcal=525, protein_g=11, carbs_g=117, fat_g=1.2),
                                ).model_dump(mode="json")
                            ]
                        ),
                        macros=Macros(kcal=525, protein_g=11, carbs_g=117, fat_g=1.2),
                    )
                ],
            )
        ],
    )
    await plan_repo.save_plan(marco_plan)
    await plan_repo.save_plan(giulia_plan)

    # Marco already logged "Cena" off-plan -> Eggs should be excluded!
    await meal_repo.create_meal_log(
        MealLog(
            log_id="log_cena_done",
            person_id=marco.person_id,
            log_date=today,
            meal_slot="Cena",
            status=MealStatus.CONFIRMED,
            total_macros=Macros(kcal=400, protein_g=30, carbs_g=30, fat_g=15),
            items=[
                MealLogItem(
                    item_id="li_1",
                    log_id="log_cena_done",
                    display_name="Pizza",
                    input_quantity=200,
                    input_unit="g",
                    input_state=PreparationState.READY_TO_EAT,
                    resolved_grams_ref=200,
                    macros=Macros(kcal=400, protein_g=30, carbs_g=30, fat_g=15),
                )
            ],
        )
    )

    draft = await shopping_svc.generate_draft(marco, [marco, giulia], today, today)
    assert set(draft.scope_person_ids) == {marco.person_id, giulia.person_id}
    by_label = {it.label: it for it in draft.items}
    assert "Uova intere" not in by_label
    # Chicken Breast: 150g cooked / 0.75 = 200g raw (aisle: meat_fish)
    assert by_label["Petto di Pollo (Crudo)"].quantity == 200.0
    assert by_label["Petto di Pollo (Crudo)"].aisle == "meat_fish"
    # Rice: Marco 250/2.5=100g + Giulia 150g = 250g raw, brand Scotti, aisle pantry
    assert by_label["Riso Basmati (Crudo)"].quantity == 250.0
    assert by_label["Riso Basmati (Crudo)"].brand == "Scotti"
    assert by_label["Riso Basmati (Crudo)"].aisle == "pantry"

    # Aisle order: meat_fish before pantry
    aisles = [it.aisle for it in draft.items]
    assert aisles.index("meat_fish") < aisles.index("pantry")

    # Editable draft item update & commit
    rice_item = by_label["Riso Basmati (Crudo)"]
    updated = await shopping_repo.update_draft_item(
        draft.draft_id, rice_item.draft_item_id, quantity=500.0, checked=False
    )
    assert updated is not None
    u_rice = next(i for i in updated.items if i.draft_item_id == rice_item.draft_item_id)
    assert u_rice.quantity == 500.0
    assert u_rice.checked is False
    committed = await shopping_repo.mark_draft_committed(draft.draft_id)
    assert committed is not None and committed.status == "committed"


def test_adaptive_tdee_bootstrap_clamping_two_compartment_and_guardrail() -> None:
    """Verify Hybrid Adaptive TDEE: bootstrap, low-adherence fallback, +-75 kcal clamp, 2-comp FM/LM, BIA guardrail."""
    today = date(2026, 11, 26)

    # 1. Bootstrapping (< 14 days) falls back to wearable_dynamic when wearable samples exist
    short_snaps = [
        BiometricSnapshot(
            snapshot_id=f"s{i}",
            person_id="p1",
            snap_date=today - timedelta(days=i),
            weight_kg=80.0,
            active_kcal=500.0,
            total_kcal=2500.0,
        )
        for i in range(1, 6)
    ]
    boot = calculate_adaptive_tdee(
        bmr_kcal=1800.0,
        bmr_formula="mifflin_st_jeor",
        activity_level=ActivityLevel.MODERATE,
        past_snapshots=short_snaps,
        daily_intake_by_date={today - timedelta(days=i): 2000.0 for i in range(1, 6)},
        today=today,
    )
    assert boot.adaptive_confidence == "bootstrapping"
    assert boot.fallback_engine == "wearable_dynamic"
    assert boot.tdee_kcal == pytest.approx(2500.0)

    # 2. 21-day clean window losing ~0.5 kg/wk on 2000 kcal intake (observed TDEE ~ 2550),
    # while Garmin overestimates at 2900 kcal -> should auto-calibrate multiplier downward & clamp step <= 75!
    snaps_21: list[BiometricSnapshot] = []
    intake_21: dict[date, float] = {}
    w_ema: float | None = None
    bf_ema: float | None = None
    for idx, days_ago in enumerate(range(21, 0, -1)):
        d = today - timedelta(days=days_ago)
        raw_w = round(80.0 - (idx * (1.5 / 20.0)), 2)
        raw_bf = round(20.0 - (idx * (1.2 / 20.0)), 2)
        w_ema = update_ema(w_ema, raw_w, alpha=0.12)
        bf_ema = update_ema(bf_ema, raw_bf, alpha=0.10)
        snaps_21.append(
            BiometricSnapshot(
                snapshot_id=f"s21_{days_ago}",
                person_id="p1",
                snap_date=d,
                weight_kg=raw_w,
                weight_ema_kg=w_ema,
                body_fat_pct=raw_bf,
                body_fat_ema_pct=bf_ema,
                active_kcal=600.0,
                total_kcal=2900.0,
            )
        )
        intake_21[d] = 2000.0

    # Low adherence (<70%) triggers fallback even over 21 days
    low_adh = calculate_adaptive_tdee(
        bmr_kcal=1800.0,
        bmr_formula="mifflin_st_jeor",
        activity_level=ActivityLevel.MODERATE,
        past_snapshots=snaps_21,
        daily_intake_by_date={d: 2000.0 for i, d in enumerate(intake_21) if i < 10},
        today=today,
    )
    assert low_adh.adaptive_confidence == "low_adherence"
    assert low_adh.fallback_engine == "wearable_dynamic"

    # Full adherence with stable prior at 2900 -> delta clamped to >= -75 kcal/week
    calibrated = calculate_adaptive_tdee(
        bmr_kcal=1800.0,
        bmr_formula="mifflin_st_jeor",
        activity_level=ActivityLevel.MODERATE,
        past_snapshots=snaps_21,
        daily_intake_by_date=intake_21,
        prev_adaptive_state=AdaptiveState(
            person_id="p1",
            last_calibrated_date=today - timedelta(days=7),
            adaptive_tdee_kcal=2800.0,
            wearable_multiplier=1.0,
            confidence="medium",
        ),
        today=today,
    )
    assert calibrated.adaptive_confidence == "high"
    assert calibrated.two_compartment_used is True
    assert calibrated.adaptive_tdee_kcal is not None
    # Anchor was 2800; cannot drop more than 75 kcal in 7 days
    assert calibrated.adaptive_tdee_kcal >= 2725.0
    assert calibrated.adaptive_tdee_kcal < 2800.0
    assert calibrated.wearable_multiplier is not None
    assert 0.65 <= calibrated.wearable_multiplier < 1.0

    # 3. BIA noise guardrail: sudden >= 4% BF spike discards two-compartment and falls back to 7700 kcal/kg
    noisy_snaps = [
        s.model_copy(
            update={"body_fat_pct": 25.0 if i == len(snaps_21) - 1 else 18.0, "body_fat_ema_pct": None}
        )
        for i, s in enumerate(snaps_21)
    ]
    # Make bf jump cleanly across EMAs by giving noisy high BF on final 6 days
    noisy_snaps = [
        s.model_copy(
            update={
                "body_fat_pct": 26.0 if i >= len(snaps_21) - 6 else 18.0,
                "body_fat_ema_pct": 23.0 if i == len(snaps_21) - 1 else (18.0 if i == 0 else None),
            }
        )
        for i, s in enumerate(snaps_21)
    ]
    # Populate body_fat_ema_pct=None everywhere so helper recomputes EMA from raw noisy BF
    noisy_snaps = [s.model_copy(update={"body_fat_ema_pct": None}) for s in noisy_snaps]
    guarded = calculate_adaptive_tdee(
        bmr_kcal=1800.0,
        bmr_formula="mifflin_st_jeor",
        activity_level=ActivityLevel.MODERATE,
        past_snapshots=noisy_snaps,
        daily_intake_by_date=intake_21,
        today=today,
    )
    assert guarded.two_compartment_used is False


async def test_workouts_repo_baseline_notes_and_trends(
    phase3_ctx: dict[str, Any],
) -> None:
    """Verify workout schedule CRUD, prompt context, PreferenceEngine global notes, and Trends ETA."""
    marco: PersonProfile = phase3_ctx["marco"]
    workout_repo: WorkoutRepository = phase3_ctx["workout_repo"]
    pref_engine: PreferenceEngine = phase3_ctx["pref_engine"]

    today = date(2026, 11, 26)
    await workout_repo.upsert_schedule_item(
        WorkoutScheduleItem(
            schedule_id="",
            person_id=marco.person_id,
            weekday=today.weekday(),
            title="Upper Body Strength",
            activity_type=WorkoutActivityType.STRENGTH,
            duration_min=65,
            estimated_kcal=420,
            time_of_day="18:30",
        )
    )
    sched = await workout_repo.list_schedule(marco.person_id)
    assert len(sched) == 1
    assert len(await workout_repo.get_scheduled_for_date(marco.person_id, today)) == 1

    # Override to Rest today clears training flag without deleting weekly template
    await workout_repo.set_override(
        TrainingDayOverride(
            person_id=marco.person_id,
            override_date=today,
            is_training_day=False,
            source="manual",
        )
    )
    prompt_ctx = await workout_repo.build_workout_prompt_context(
        marco.person_id,
        today,
        2,
        calendar_workouts_by_date={
            today + timedelta(days=1): [
                ScheduledCalendarWorkout(summary="Garmin Coach 10k Tempo", start=(today + timedelta(days=1)).isoformat())
            ]
        },
    )
    assert "REST DAY" in prompt_ctx
    assert "Garmin Coach 10k Tempo" in prompt_ctx

    pref_ctx = await pref_engine.build_context(marco, reference_date=today)
    assert pref_ctx.global_baseline_notes == "Italian Mediterranean family cooking"

    # Trends 30d summary with steady ~ -0.45 kg/wk loss -> goal ETA computed cleanly
    snaps = [
        BiometricSnapshot(
            snapshot_id=f"tr_{i}",
            person_id=marco.person_id,
            snap_date=today - timedelta(days=14 - i),
            weight_kg=round(80.0 - i * 0.065, 2),
            weight_ema_kg=round(80.0 - i * 0.065, 2),
            body_fat_pct=round(20.0 - i * 0.04, 1),
            body_fat_ema_pct=round(20.0 - i * 0.04, 1),
            computed_tdee=2400.0,
            target_kcal=1950.0,
        )
        for i in range(15)
    ]
    macros_map = {
        today - timedelta(days=i): Macros(
            kcal=1940, protein_g=155, carbs_g=185, fat_g=60, fiber_g=29
        )
        for i in range(10)
    }
    tdee_now = calculate_adaptive_tdee(
        bmr_kcal=1800.0,
        bmr_formula="mifflin_st_jeor",
        activity_level=ActivityLevel.MODERATE,
        past_snapshots=snaps[:-1],
        daily_intake_by_date={d: m.kcal for d, m in macros_map.items()},
        today=today,
    )
    summary = build_trends_summary(
        profile=marco,
        snapshots=snaps,
        daily_macros=macros_map,
        current_tdee=tdee_now,
        current_targets=TargetResult(
            target_kcal=1950.0,
            protein_g=160.0,
            carbs_g=190.0,
            fat_g=60.0,
            fiber_g=27.3,
            mode=TargetMode.PHYSIOLOGY,
        ),
        today=today,
        days=30,
    )
    assert summary.days == 30
    assert summary.logged_days == 10
    assert summary.expected_vs_actual.status == "on_track"
    assert summary.expected_vs_actual.eta_date is not None
    assert summary.points[-1].lean_mass_kg is not None

    # Default trends window is now 14 days (2 weeks), normalized cleanly
    summary_default = build_trends_summary(
        profile=marco,
        snapshots=snaps,
        daily_macros=macros_map,
        current_tdee=tdee_now,
        current_targets=TargetResult(
            target_kcal=1950.0,
            protein_g=160.0,
            carbs_g=190.0,
            fat_g=60.0,
            fiber_g=27.3,
            mode=TargetMode.PHYSIOLOGY,
        ),
        today=today,
    )
    assert summary_default.days == 14
    assert len(summary_default.points) == 14
    assert summary_default.points[-1].point_date == today
    assert summary_default.points[0].point_date == today - timedelta(days=13)


def _load_seed_items() -> list[dict[str, Any]]:
    seed_path = (
        Path(__file__).resolve().parents[2]
        / "custom_components"
        / "ha_calories_buddy"
        / "core"
        / "storage"
        / "seed_foods.json"
    )
    return list(json.loads(seed_path.read_text(encoding="utf-8")))


async def test_seed_foods_integrity_usda_search_and_non_destructive_backfill(
    phase3_ctx: dict[str, Any],
) -> None:
    """Verify expanded 280-food seed catalog Atwater integrity, USDA search fallback, and non-destructive backfill."""
    items = _load_seed_items()
    assert len(items) >= 280
    ids = [str(it["food_id"]) for it in items]
    assert len(ids) == len(set(ids))
    assert "seed_sunflower_oil" in ids
    assert "seed_flour_00" in ids

    for row in items:
        assert row.get("name") and row.get("name_it")
        p = float(row["protein_g"])
        c = float(row["carbs_g"])
        f = float(row["fat_g"])
        fib = float(row.get("fiber_g") or 0.0)
        k = float(row["kcal"])
        assert p >= 0.0 and c >= 0.0 and f >= 0.0 and fib >= 0.0 and k >= 0.0
        assert float(row.get("yield_factor", 1.0)) > 0.0
        if str(row["food_id"]) != "seed_white_wine_cooking":
            atw = 4.0 * p + 4.0 * c + 9.0 * f + 2.0 * fib
            assert abs(k - atw) <= max(18.0, 0.15 * max(atw, k)), (row["food_id"], k, atw)

    # Non-destructive biometric backfill & chronological EMA recomputation
    profile_repo: ProfileRepository = phase3_ctx["profile_repo"]
    today = date.today()
    manual_d = today - timedelta(days=3)
    await profile_repo.upsert_biometric_snapshot(
        BiometricSnapshot(
            snapshot_id="manual_snap",
            person_id="p_marco",
            snap_date=manual_d,
            weight_kg=80.0,  # Manual value must NOT be overwritten
            active_kcal=None,
        )
    )
    written = await profile_repo.backfill_biometric_snapshots(
        "p_marco",
        [
            BiometricSnapshot(
                snapshot_id="bf_1",
                person_id="p_marco",
                snap_date=today - timedelta(days=6),
                weight_kg=81.0,
                body_fat_pct=20.0,
            ),
            BiometricSnapshot(
                snapshot_id="bf_2",
                person_id="p_marco",
                snap_date=manual_d,
                weight_kg=75.0,  # Ignored because manual weight_kg=80.0 already exists!
                active_kcal=420.0,  # Filled because existing active_kcal was NULL!
            ),
        ],
    )
    assert written == 2
    loaded_snaps = await profile_repo.get_biometric_snapshots_range(
        "p_marco", today - timedelta(days=10), today
    )
    manual_after = next(s for s in loaded_snaps if s.snap_date == manual_d)
    assert manual_after.weight_kg == pytest.approx(80.0)
    assert manual_after.active_kcal == pytest.approx(420.0)

    changed = recompute_biometric_emas(loaded_snaps)
    assert len(changed) == 2
    await profile_repo.update_biometric_emas(changed)
    loaded_after_ema = await profile_repo.get_biometric_snapshots_range(
        "p_marco", today - timedelta(days=10), today
    )
    assert loaded_after_ema[-1].weight_ema_kg == pytest.approx(
        round(0.12 * 80.0 + 0.88 * 81.0, 2)
    )

    # Interactive search falls back to USDA when local + OFF leave spare slots
    class _FakeOFF:
        async def lookup_barcode(self, ean: str) -> FoodItem | None:
            return None

        async def search(self, query: str, brand: str | None = None, language: str = "en", limit: int = 5) -> list[FoodItem]:
            return []

    class _FakeUSDA:
        enabled = True

        async def lookup_barcode(self, ean: str) -> FoodItem | None:
            return None

        async def search(self, query: str, brand: str | None = None, language: str = "en", limit: int = 5) -> list[FoodItem]:
            return [
                FoodItem(
                    food_id="usda_custom_test_1",
                    name="Uncommon Test Spice Mix",
                    normalized_name="uncommon test spice mix",
                    category="other",
                    preparation_state=PreparationState.READY_TO_EAT,
                    macros_per_100g=Macros(kcal=250, protein_g=10, carbs_g=40, fat_g=5, fiber_g=8),
                    label_standard=LabelStandard.US,
                    source=FoodSource.USDA,
                    verified=True,
                )
            ]

    resolver_usda = FoodResolver(
        phase3_ctx["food_repo"],
        off_provider=_FakeOFF(),
        usda_provider=_FakeUSDA(),
    )
    hits, total = await resolver_usda.search_foods_paginated(
        "uncommon test spice", person_id="p_marco", include_external=True, limit=10
    )
    assert total == 1
    assert len(hits) == 1 and hits[0].food_id == "usda_custom_test_1"
