"""Pure-Python tests for slot resolution, fiber targets, state-aware resolution, and Vault pagination."""

from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pytest

from custom_components.ha_calories_buddy.core.models import (
    ActivityLevel,
    FoodItem,
    FoodSource,
    GoalType,
    LabelStandard,
    Macros,
    ManualMacroTarget,
    MealLog,
    MealLogItem,
    MealSlotConfig,
    MealStatus,
    PersonProfile,
    PreparationState,
    Sex,
    TargetMode,
    TDEEEngineType,
)
from custom_components.ha_calories_buddy.core.nutrition.resolver import FoodResolver
from custom_components.ha_calories_buddy.core.physiology.bmr import calculate_bmr
from custom_components.ha_calories_buddy.core.physiology.targets import (
    calculate_daily_targets,
    default_fiber_target_g,
)
from custom_components.ha_calories_buddy.core.physiology.tdee import TDEEResult
from custom_components.ha_calories_buddy.core.planner.meal_parser import (
    MealLoggingService,
)
from custom_components.ha_calories_buddy.core.planner.slot_resolver import (
    canonical_slot_group,
    default_slot_for_time,
    default_slots_for_language,
    parse_hhmm_minutes,
    resolve_meal_slot,
    slot_approx_minutes,
)
from custom_components.ha_calories_buddy.core.storage.database import Database
from custom_components.ha_calories_buddy.core.storage.food_repo import FoodRepository
from custom_components.ha_calories_buddy.core.storage.meal_repo import MealRepository
from custom_components.ha_calories_buddy.core.storage.profile_repo import (
    ProfileRepository,
)
from custom_components.ha_calories_buddy.core.storage.seeder import (
    normalize_text,
    seed_database_if_empty,
)


@pytest.fixture
async def seeded_db(tmp_path: Path) -> Database:
    """Provide a migrated and seeded temporary SQLite database."""
    db = Database(tmp_path / "slot_fiber.db")
    await db.initialize()
    await seed_database_if_empty(db)
    yield db
    await db.close()


def _make_profile(*, lang: str = "it", slots: list[MealSlotConfig] | None = None) -> PersonProfile:
    return PersonProfile(
        person_id="p_nico",
        config_entry_id="ce_nico",
        display_name="Nicola",
        language=lang,
        sex=Sex.MALE,
        birth_year=1990,
        height_cm=178.0,
        initial_weight_kg=74.0,
        target_mode=TargetMode.PHYSIOLOGY,
        goal_type=GoalType.MAINTAIN,
        tdee_engine=TDEEEngineType.STATIC,
        activity_level=ActivityLevel.MODERATE,
        meal_slots=slots if slots is not None else default_slots_for_language(lang),
    )


def test_resolve_meal_slot_never_unspecified_and_synonyms() -> None:
    """Ensure invalid slots fall back to time-of-day and EN<->IT synonyms map cleanly."""
    it_slots = default_slots_for_language("it")
    en_slots = default_slots_for_language("en")

    # Never returns unspecified / junk
    for junk in (None, "", "unspecified", "Unknown", "altro", "n/a", "custom_invalid"):
        resolved = resolve_meal_slot(junk, it_slots, language="it", now_time=time(13, 15))
        assert resolved == "Pranzo"
        assert resolved.lower() != "unspecified"

    # EN <-> IT bidirectional synonyms
    assert resolve_meal_slot("Breakfast", it_slots, language="it") == "Colazione"
    assert resolve_meal_slot("lunch", it_slots, language="it") == "Pranzo"
    assert resolve_meal_slot("afternoon snack", it_slots, language="it") == "Spuntino"
    assert resolve_meal_slot("merenda", it_slots, language="it") == "Spuntino"
    assert resolve_meal_slot("Dinner", it_slots, language="it") == "Cena"

    assert resolve_meal_slot("Colazione", en_slots, language="en") == "Breakfast"
    assert resolve_meal_slot("pranzo", en_slots, language="en") == "Lunch"
    assert resolve_meal_slot("spuntino", en_slots, language="en") == "Snack"
    assert resolve_meal_slot("cena", en_slots, language="en") == "Dinner"

    # Time-of-day boundaries
    assert default_slot_for_time(it_slots, now_time=time(8, 0)) == "Colazione"
    assert default_slot_for_time(it_slots, now_time=time(13, 0)) == "Pranzo"
    assert default_slot_for_time(it_slots, now_time=time(16, 30)) == "Spuntino"
    assert default_slot_for_time(it_slots, now_time=time(20, 30)) == "Cena"

    # Utility helpers
    assert parse_hhmm_minutes("08:30") == 510
    assert parse_hhmm_minutes("bad") is None
    assert parse_hhmm_minutes("25:00") is None
    assert slot_approx_minutes(MealSlotConfig(slot_id="cena", name="Cena")) == 20 * 60


def test_fiber_targets_physiology_and_manual() -> None:
    """Verify 14g/1000kcal clamped [20, 45] formula and Manual fiber overrides."""
    assert default_fiber_target_g(0.0) == 20.0
    assert default_fiber_target_g(1200.0) == 20.0  # clamped low
    assert default_fiber_target_g(2500.0) == 35.0
    assert default_fiber_target_g(4000.0) == 45.0  # clamped high

    profile = _make_profile()
    bmr_kcal, bmr_formula = calculate_bmr(
        sex=profile.sex,
        weight_kg=74.0,
        height_cm=178.0,
        age_years=36,
    )
    tdee = TDEEResult(
        bmr_kcal=bmr_kcal,
        bmr_formula=bmr_formula,
        tdee_kcal=2500.0,
        engine_used=TDEEEngineType.STATIC,
        wearable_baseline_kcal=None,
        intraday_delta_kcal=0.0,
        fallback_to_static=False,
        days_used=0,
    )
    phys_target = calculate_daily_targets(profile, 74.0, None, tdee)
    assert phys_target.fiber_g == pytest.approx(35.0)

    # Manual targets: explicit rest & training fiber + fallback when omitted
    manual_profile = _make_profile().model_copy(
        update={
            "target_mode": TargetMode.MANUAL,
            "manual_targets": ManualMacroTarget(
                kcal=2100.0,
                protein_g=160.0,
                carbs_g=210.0,
                fat_g=65.0,
                fiber_g=32.0,
                training_kcal=2600.0,
                training_fiber_g=40.0,
            ),
        }
    )
    rest_res = calculate_daily_targets(manual_profile, 74.0, None, tdee, is_training_day=False)
    train_res = calculate_daily_targets(manual_profile, 74.0, None, tdee, is_training_day=True)
    assert rest_res.fiber_g == pytest.approx(32.0)
    assert train_res.fiber_g == pytest.approx(40.0)

    manual_no_fiber = _make_profile().model_copy(
        update={
            "target_mode": TargetMode.MANUAL,
            "manual_targets": ManualMacroTarget(
                kcal=2000.0, protein_g=150.0, carbs_g=200.0, fat_g=65.0
            ),
        }
    )
    fallback_res = calculate_daily_targets(manual_no_fiber, 74.0, None, tdee)
    assert fallback_res.fiber_g == pytest.approx(28.0)


async def test_state_aware_food_matching_bilingual_names_and_backfill(
    seeded_db: Database,
) -> None:
    """Verify raw vs cooked scoring, Italian name_it localization, and seed backfill."""
    food_repo = FoodRepository(seeded_db)
    resolver = FoodResolver(food_repo)

    # Clear name_it on a seed row + add a manual food -> re-seeding backfills only seed row
    await seeded_db.execute(
        "UPDATE global_foods SET name_it = NULL WHERE food_id = 'seed_lentils_cooked'"
    )
    manual_food = FoodItem(
        food_id="manual_custom_snack",
        name="My Custom Bar",
        name_it="La Mia Barretta",
        normalized_name=normalize_text("My Custom Bar"),
        category="snack",
        preparation_state=PreparationState.READY_TO_EAT,
        macros_per_100g=Macros(kcal=400, protein_g=20, carbs_g=40, fat_g=15, fiber_g=5),
        label_standard=LabelStandard.EU,
        yield_factor=1.0,
        source=FoodSource.MANUAL,
        verified=True,
    )
    await food_repo.upsert_global_food(manual_food)

    await seed_database_if_empty(seeded_db)
    restored_lentils = await food_repo.get_effective_food("seed_lentils_cooked")
    kept_manual = await food_repo.get_effective_food("manual_custom_snack")
    assert restored_lentils is not None
    assert restored_lentils.name_it == "Lenticchie (Cotte / Lessate)"
    assert restored_lentils.localized_name("it") == "Lenticchie (Cotte / Lessate)"
    assert restored_lentils.localized_name("en") == restored_lentils.name
    assert kept_manual is not None
    assert kept_manual.localized_name("it") == "La Mia Barretta"

    # State-aware resolution: raw vs cooked lentils (90g)
    raw_lentils = await resolver.resolve_item(
        person_id="p_nico",
        name="lenticchie crude",
        quantity=90.0,
        unit="g",
        state=PreparationState.RAW,
        language="it",
    )
    cooked_lentils = await resolver.resolve_item(
        person_id="p_nico",
        name="lenticchie cotte",
        quantity=90.0,
        unit="g",
        state=PreparationState.COOKED,
        language="it",
    )
    assert raw_lentils.food_id == "seed_lentils_raw"
    assert raw_lentils.macros.kcal == pytest.approx(284.4, abs=1.0)
    assert cooked_lentils.food_id == "seed_lentils_cooked"
    assert cooked_lentils.macros.kcal == pytest.approx(104.4, abs=1.0)

    # Pasta, basmati rice, and chicken breast raw vs cooked differentiation
    raw_rice = await food_repo.find_best_match(
        "riso basmati", preferred_state=PreparationState.RAW
    )
    cooked_rice = await food_repo.find_best_match(
        "riso basmati", preferred_state=PreparationState.COOKED
    )
    raw_chicken = await food_repo.find_best_match(
        "petto di pollo", preferred_state=PreparationState.RAW
    )
    cooked_chicken = await food_repo.find_best_match(
        "petto di pollo", preferred_state=PreparationState.COOKED
    )
    assert raw_rice is not None and raw_rice.food_id == "seed_basmati_rice_raw"
    assert cooked_rice is not None and cooked_rice.food_id == "seed_basmati_rice_cooked"
    assert raw_chicken is not None and raw_chicken.food_id == "seed_chicken_breast_raw"
    assert cooked_chicken is not None and cooked_chicken.food_id == "seed_chicken_breast_cooked"


async def test_vault_pagination_categories_and_meal_slot_edit(
    seeded_db: Database,
) -> None:
    """Verify full paginated Food Vault browsing, categories, inline slot switch, and fiber summary."""
    food_repo = FoodRepository(seeded_db)
    resolver = FoodResolver(food_repo)
    meal_repo = MealRepository(seeded_db)
    profile_repo = ProfileRepository(seeded_db)

    profile = _make_profile(lang="it")
    await profile_repo.upsert_person(profile)

    # Full pagination walk across all 88+ seeded foods
    page1, total1 = await resolver.search_foods_paginated(
        "", person_id="p_nico", limit=25, offset=0, language="it", include_external=False
    )
    page2, total2 = await resolver.search_foods_paginated(
        "", person_id="p_nico", limit=25, offset=25, language="it", include_external=False
    )
    page3, total3 = await resolver.search_foods_paginated(
        "", person_id="p_nico", limit=25, offset=50, language="it", include_external=False
    )
    page4, total4 = await resolver.search_foods_paginated(
        "", person_id="p_nico", limit=25, offset=75, language="it", include_external=False
    )
    assert total1 == total2 == total3 == total4 >= 88
    assert len(page1) == 25 and 25 < total1
    assert len(page2) == 25 and 50 < total2
    assert len(page3) == 25 and 75 < total3
    assert len(page4) >= 13 and 75 + len(page4) == total4
    all_ids = {f.food_id for f in [*page1, *page2, *page3, *page4]}
    assert len(all_ids) == total1

    # Category group filtering
    protein_foods, p_total = await food_repo.list_foods_paginated(
        "p_nico", category="protein", limit=50, offset=0, language="it"
    )
    assert p_total >= 5
    assert all(f.category in {"poultry", "red_meat", "fish", "egg"} for f in protein_foods)

    # Category + search query combination
    legume_hits, l_total = await food_repo.list_foods_paginated(
        "p_nico", query="lenticchie", category="legumes", limit=10, offset=0
    )
    assert l_total >= 2
    assert all(f.category == "legumes" for f in legume_hits)

    # Manual log with junk meal_slot -> normalized, fiber summary present, inline slot change
    logging_svc = MealLoggingService(meal_repo, food_repo, resolver, lambda _p: None)
    logged = await logging_svc.log_manual_item(
        profile,
        food_id="seed_lentils_cooked",
        quantity=200.0,
        unit="g",
        state=PreparationState.COOKED,
        meal_slot="unspecified",
        log_date=date.today(),
    )
    assert logged.meal_log.meal_slot != "unspecified"
    assert logged.meal_log.meal_slot in {"Colazione", "Pranzo", "Spuntino", "Cena"}
    assert "Fib" in logged.summary_message or "fibre" in logged.summary_message.lower()
    assert logged.meal_log.total_macros.fiber_g == pytest.approx(15.8, abs=0.2)

    moved = await logging_svc.change_meal_slot(profile, logged.meal_log.log_id, "Cena")
    assert moved is not None
    assert moved.meal_slot == "Cena"


async def test_database_migrations_idempotent_with_preexisting_column(
    tmp_path: Path,
) -> None:
    """Verify migration 002 safely succeeds even if ALTER TABLE columns already exist."""
    db_path = tmp_path / "partial_migration.db"
    db1 = Database(db_path)
    await db1.initialize()
    # Simulate a DB where migration 002 columns exist but schema_migrations missed version 2
    await db1.execute("DELETE FROM schema_migrations WHERE version = 2")
    await db1.close()

    db2 = Database(db_path)
    await db2.initialize()
    try:
        row = await db2.fetch_one(
            "SELECT version FROM schema_migrations WHERE version = 2"
        )
        assert row is not None
    finally:
        await db2.close()


async def test_item_replacement_and_single_item_deletion_and_evening_slots(
    seeded_db: Database,
) -> None:
    """Verify Vault food replacement (with brand clear), single-item delete, and evening snack slot."""
    assert canonical_slot_group("Spuntino serale") == "snack"
    assert canonical_slot_group("Dopocena") == "snack"
    assert canonical_slot_group("Colazione") == "breakfast"
    assert canonical_slot_group("Cena") == "dinner"
    evening = MealSlotConfig(slot_id="s_eve", name="Spuntino serale", approx_time="12:30")
    assert slot_approx_minutes(evening) == 22 * 60

    food_repo = FoodRepository(seeded_db)
    resolver = FoodResolver(food_repo)
    meal_repo = MealRepository(seeded_db)
    profile_repo = ProfileRepository(seeded_db)

    profile = _make_profile(lang="it")
    await profile_repo.upsert_person(profile)

    # Create two same-brand breads + one unbranded generic food
    await food_repo.upsert_global_food(
        FoodItem(
            food_id="f_bread_whole",
            name="Wholemeal Bread BrandX",
            name_it="Pane Integrale",
            normalized_name="pane integrale",
            brand="BrandX",
            category="bread",
            preparation_state=PreparationState.READY_TO_EAT,
            macros_per_100g=Macros(kcal=240, protein_g=9.0, carbs_g=44.0, fat_g=2.5, fiber_g=6.0),
            label_standard=LabelStandard.EU,
            yield_factor=1.0,
            source=FoodSource.MANUAL,
            verified=True,
        )
    )
    await food_repo.upsert_global_food(
        FoodItem(
            food_id="f_bread_rye",
            name="Rye Bread BrandX",
            name_it="Pane di Segale",
            normalized_name="pane di segale",
            brand="BrandX",
            category="bread",
            preparation_state=PreparationState.READY_TO_EAT,
            macros_per_100g=Macros(kcal=210, protein_g=7.0, carbs_g=40.0, fat_g=1.5, fiber_g=8.5),
            label_standard=LabelStandard.EU,
            yield_factor=1.0,
            source=FoodSource.MANUAL,
            verified=True,
        )
    )

    today = date.today()
    meal = MealLog(
        log_id="log_edit_test",
        person_id=profile.person_id,
        log_date=today,
        meal_slot="Pranzo",
        status=MealStatus.CONFIRMED,
        total_macros=Macros(kcal=343, protein_g=19.3, carbs_g=47.6, fat_g=2.7, fiber_g=6.0),
        items=[
            MealLogItem(
                item_id="item_bread",
                log_id="log_edit_test",
                food_id="f_bread_whole",
                display_name="Pane Integrale",
                brand="BrandX",
                input_quantity=100,
                input_unit="g",
                input_state=PreparationState.READY_TO_EAT,
                resolved_grams_ref=100,
                macros=Macros(kcal=240, protein_g=9.0, carbs_g=44.0, fat_g=2.5, fiber_g=6.0),
            ),
            MealLogItem(
                item_id="item_yog",
                log_id="log_edit_test",
                food_id="seed_greek_yogurt_0",
                display_name="Yogurt Greco 0% Grassi",
                input_quantity=180,
                input_unit="g",
                input_state=PreparationState.READY_TO_EAT,
                resolved_grams_ref=180,
                macros=Macros(kcal=102.6, protein_g=18.5, carbs_g=6.5, fat_g=0.4, fiber_g=0.0),
            ),
        ],
    )
    await meal_repo.create_meal_log(meal)

    logging_svc = MealLoggingService(meal_repo, food_repo, resolver, lambda _p: None)

    # 1. Replace branded Wholemeal Bread with branded Rye Bread
    swapped = await logging_svc.update_item_measurement(
        profile,
        "log_edit_test",
        "item_bread",
        quantity=100,
        unit="g",
        food_id="f_bread_rye",
    )
    assert swapped is not None
    bread_item = next(i for i in swapped.items if i.item_id == "item_bread")
    assert bread_item.food_id == "f_bread_rye"
    assert bread_item.display_name == "Pane di Segale"
    assert bread_item.brand == "BrandX"
    assert bread_item.macros.kcal == pytest.approx(210.0, abs=0.5)
    assert bread_item.macros.fiber_g == pytest.approx(8.5, abs=0.2)

    # 2. Swap to an unbranded generic seed item clears old BrandX cleanly
    swapped_unbranded = await logging_svc.update_item_measurement(
        profile,
        "log_edit_test",
        "item_bread",
        quantity=100,
        unit="g",
        food_id="seed_wholewheat_bread",
    )
    assert swapped_unbranded is not None
    bread_generic = next(i for i in swapped_unbranded.items if i.item_id == "item_bread")
    assert bread_generic.food_id == "seed_wholewheat_bread"
    assert bread_generic.brand is None

    # 3. Remove 1 of 2 items -> remaining item stays confirmed and parent totals recalculate
    after_remove_one = await logging_svc.remove_meal_item(profile, "log_edit_test", "item_yog")
    assert after_remove_one is not None
    assert after_remove_one.status == MealStatus.CONFIRMED
    assert len(after_remove_one.items) == 1
    assert after_remove_one.total_macros.kcal == pytest.approx(243.0, abs=1.0)

    # 4. Remove last item -> parent soft-deletes cleanly and disappears from daily totals
    after_remove_last = await logging_svc.remove_meal_item(profile, "log_edit_test", "item_bread")
    assert after_remove_last is not None
    assert after_remove_last.status == MealStatus.DELETED
    assert after_remove_last.items == []
    totals = await meal_repo.get_daily_totals(profile.person_id, today)
    assert totals.consumed.kcal == 0.0
    assert totals.confirmed_count == 0
