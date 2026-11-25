"""Tests for async SQLite storage, seeding, and two-tier repositories."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from custom_components.ha_calories_buddy.core.models import (
    BiometricSnapshot,
    ConfidenceLevel,
    FoodItem,
    FoodPortion,
    FoodSource,
    LabelStandard,
    Macros,
    MealLog,
    MealLogItem,
    MealStatus,
    PersonFoodOverride,
    PersonProfile,
    PreferenceEntry,
    PreferenceScope,
    PreferenceStance,
    PreparationState,
    WarningCode,
)
from custom_components.ha_calories_buddy.core.storage.database import Database
from custom_components.ha_calories_buddy.core.storage.food_repo import FoodRepository
from custom_components.ha_calories_buddy.core.storage.meal_repo import MealRepository
from custom_components.ha_calories_buddy.core.storage.profile_repo import ProfileRepository
from custom_components.ha_calories_buddy.core.storage.seeder import (
    normalize_text,
    seed_database_if_empty,
)


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    """Provide an initialized temporary SQLite database."""
    database = Database(tmp_path / "test.db")
    await database.initialize()
    yield database
    await database.close()


async def test_migrations_and_seeder_idempotent(db: Database) -> None:
    """Verify seeding loads baseline foods once and remains idempotent."""
    first_count = await seed_database_if_empty(db)
    second_count = await seed_database_if_empty(db)
    await db.initialize()  # idempotent re-init

    assert first_count >= 40
    assert second_count == 0

    food_repo = FoodRepository(db)
    results = await food_repo.search("riso basmati")
    assert results
    assert any("basmati" in r.normalized_name for r in results)


async def test_two_tier_food_vault_isolation(db: Database) -> None:
    """Verify global foods are shared while overrides stay isolated per person."""
    profile_repo = ProfileRepository(db)
    food_repo = FoodRepository(db)

    marco = PersonProfile(
        person_id="p_marco",
        config_entry_id="entry_marco",
        ha_person_entity="person.marco",
        ha_user_id="user_marco",
        display_name="Marco",
    )
    giulia = PersonProfile(
        person_id="p_giulia",
        config_entry_id="entry_giulia",
        ha_person_entity="person.giulia",
        ha_user_id="user_giulia",
        display_name="Giulia",
    )
    await profile_repo.upsert_person(marco)
    await profile_repo.upsert_person(giulia)

    food = FoodItem(
        food_id="food_scotti_basmati",
        name="Riso Basmati",
        normalized_name=normalize_text("Riso Basmati"),
        brand="Scotti",
        barcode_ean="8001234567890",
        category="rice",
        preparation_state=PreparationState.RAW,
        macros_per_100g=Macros(kcal=352, protein_g=8.5, carbs_g=77.7, fat_g=0.5, fiber_g=1.4),
        label_standard=LabelStandard.EU,
        yield_factor=2.5,
        source=FoodSource.OPEN_FOOD_FACTS,
        verified=True,
        portions=[FoodPortion(food_id="food_scotti_basmati", unit_name="cup", grams=180.0)],
    )
    await food_repo.upsert_global_food(food)

    # Giulia overrides yield factor and macros + adds custom portion
    await food_repo.upsert_person_override(
        PersonFoodOverride(
            override_id="ovr_1",
            person_id="p_giulia",
            food_id="food_scotti_basmati",
            preferred_alias="Il mio basmati al dente",
            kcal_per_100g=360.0,
            yield_factor=2.3,
            custom_portions={"scodella": 90.0},
        )
    )

    marco_view = await food_repo.get_by_barcode("8001234567890", person_id="p_marco")
    giulia_view = await food_repo.get_by_barcode("8001234567890", person_id="p_giulia")

    assert marco_view is not None
    assert giulia_view is not None
    assert marco_view.has_override is False
    assert marco_view.macros_per_100g.kcal == 352.0
    assert marco_view.yield_factor == 2.5
    assert "scodella" not in marco_view.person_portions

    assert giulia_view.has_override is True
    assert giulia_view.macros_per_100g.kcal == 360.0
    assert giulia_view.macros_per_100g.protein_g == 8.5  # inherited non-overridden fields
    assert giulia_view.yield_factor == 2.3
    assert giulia_view.person_portions["scodella"] == 90.0
    assert giulia_view.preferred_alias == "Il mio basmati al dente"


async def test_meal_logs_confirm_update_and_soft_delete(db: Database) -> None:
    """Verify meal lifecycle, daily totals filtering, atomic updates, and undo."""
    profile_repo = ProfileRepository(db)
    meal_repo = MealRepository(db)
    await seed_database_if_empty(db)

    await profile_repo.upsert_person(
        PersonProfile(
            person_id="p_marco",
            config_entry_id="entry_marco",
            display_name="Marco",
        )
    )

    today = date(2026, 11, 25)
    confirmed_meal = MealLog(
        log_id="log_1",
        person_id="p_marco",
        log_date=today,
        meal_slot="Lunch",
        status=MealStatus.CONFIRMED,
        source_channel="voice",
        total_macros=Macros(kcal=370.0, protein_g=10.0, carbs_g=60.0, fat_g=10.0),
        items=[
            MealLogItem(
                item_id="item_1",
                log_id="log_1",
                food_id="seed_basmati_rice_raw",
                display_name="Basmati Rice (Raw)",
                input_quantity=80.0,
                input_unit="g",
                input_state=PreparationState.RAW,
                resolved_grams_ref=80.0,
                macros=Macros(kcal=281.6, protein_g=6.8, carbs_g=62.2, fat_g=0.4),
            ),
            MealLogItem(
                item_id="item_2",
                log_id="log_1",
                food_id="seed_olive_oil",
                display_name="Extra Virgin Olive Oil",
                input_quantity=1.0,
                input_unit="tbsp",
                input_state=PreparationState.READY_TO_EAT,
                resolved_grams_ref=10.0,
                macros=Macros(kcal=88.4, protein_g=0.0, carbs_g=0.0, fat_g=9.8),
            ),
        ],
    )
    pending_meal = MealLog(
        log_id="log_2",
        person_id="p_marco",
        log_date=today,
        meal_slot="Snack",
        status=MealStatus.PENDING_CONFIRMATION,
        source_channel="voice",
        total_macros=Macros(kcal=200.0, protein_g=5.0, carbs_g=20.0, fat_g=10.0),
        items=[
            MealLogItem(
                item_id="item_3",
                log_id="log_2",
                food_id=None,
                display_name="Mystery Biscuit",
                input_quantity=40.0,
                input_unit="g",
                input_state=PreparationState.READY_TO_EAT,
                resolved_grams_ref=40.0,
                macros=Macros(kcal=200.0, protein_g=5.0, carbs_g=20.0, fat_g=10.0),
                confidence=ConfidenceLevel.LOW,
                warning_code=WarningCode.LLM_ESTIMATED,
            )
        ],
    )
    await meal_repo.create_meal_log(confirmed_meal)
    await meal_repo.create_meal_log(pending_meal)

    totals = await meal_repo.get_daily_totals("p_marco", today)
    assert totals.confirmed_count == 1
    assert totals.pending_count == 1
    assert totals.consumed.kcal == 370.0

    # Confirm pending meal -> totals increase
    await meal_repo.confirm_meal("p_marco", "log_2")
    totals_after_confirm = await meal_repo.get_daily_totals("p_marco", today)
    assert totals_after_confirm.confirmed_count == 2
    assert totals_after_confirm.pending_count == 0
    assert totals_after_confirm.consumed.kcal == 570.0

    # Update item_1 -> meal total updates atomically
    updated = await meal_repo.update_meal_item(
        "p_marco",
        "log_1",
        "item_1",
        input_quantity=100.0,
        input_unit="g",
        input_state=PreparationState.RAW,
        resolved_grams_ref=100.0,
        macros=Macros(kcal=352.0, protein_g=8.5, carbs_g=77.7, fat_g=0.5),
    )
    assert updated is not None
    assert updated.total_macros.kcal == pytest.approx(440.4, abs=0.2)

    # Soft delete and restore
    assert await meal_repo.soft_delete_meal("p_marco", "log_2") is True
    totals_deleted = await meal_repo.get_daily_totals("p_marco", today)
    assert totals_deleted.confirmed_count == 1

    assert await meal_repo.restore_meal("p_marco", "log_2") is True
    totals_restored = await meal_repo.get_daily_totals("p_marco", today)
    assert totals_restored.confirmed_count == 2

    freq = await meal_repo.get_top_frequent_foods("p_marco")
    assert len(freq) >= 2


async def test_preferences_override_and_biometric_history(db: Database) -> None:
    """Verify two-tier global-vs-person preferences and biometric snapshots."""
    profile_repo = ProfileRepository(db)
    await profile_repo.upsert_person(
        PersonProfile(
            person_id="p_marco",
            config_entry_id="entry_m",
            ha_person_entity="person.marco",
            ha_user_id="uid_m",
            display_name="Marco",
        )
    )
    await profile_repo.upsert_person(
        PersonProfile(
            person_id="p_giulia",
            config_entry_id="entry_g",
            ha_person_entity="person.giulia",
            ha_user_id="uid_g",
            display_name="Giulia",
        )
    )

    assert (await profile_repo.get_person_by_user_id("uid_m")) is not None
    assert (await profile_repo.get_person_by_entity("person.giulia")) is not None

    # Global household likes salmon, dislikes broccoli
    await profile_repo.upsert_preference(
        PreferenceEntry(
            pref_id="pref_g1",
            scope=PreferenceScope.GLOBAL,
            target_type="food",
            target_value="Salmon",
            stance=PreferenceStance.LIKE,
        )
    )
    await profile_repo.upsert_preference(
        PreferenceEntry(
            pref_id="pref_g2",
            scope=PreferenceScope.GLOBAL,
            target_type="food",
            target_value="Broccoli",
            stance=PreferenceStance.DISLIKE,
        )
    )
    # Giulia loves broccoli (overrides global dislike) and is allergic to walnuts
    await profile_repo.upsert_preference(
        PreferenceEntry(
            pref_id="pref_p1",
            person_id="p_giulia",
            scope=PreferenceScope.PERSON,
            target_type="food",
            target_value="Broccoli",
            stance=PreferenceStance.LOVE,
        )
    )
    await profile_repo.upsert_preference(
        PreferenceEntry(
            pref_id="pref_p2",
            person_id="p_giulia",
            scope=PreferenceScope.PERSON,
            target_type="food",
            target_value="Walnuts",
            stance=PreferenceStance.ALLERGY,
        )
    )

    marco_prefs = {(p.target_value, p.stance) for p in await profile_repo.get_effective_preferences("p_marco")}
    giulia_prefs = {(p.target_value, p.stance) for p in await profile_repo.get_effective_preferences("p_giulia")}

    assert ("Salmon", PreferenceStance.LIKE) in marco_prefs
    assert ("Broccoli", PreferenceStance.DISLIKE) in marco_prefs
    assert ("Walnuts", PreferenceStance.ALLERGY) not in marco_prefs

    assert ("Salmon", PreferenceStance.LIKE) in giulia_prefs
    assert ("Broccoli", PreferenceStance.LOVE) in giulia_prefs
    assert ("Broccoli", PreferenceStance.DISLIKE) not in giulia_prefs
    assert ("Walnuts", PreferenceStance.ALLERGY) in giulia_prefs

    # Biometric snapshots query
    today = date(2026, 11, 25)
    for offset in range(1, 5):
        await profile_repo.upsert_biometric_snapshot(
            BiometricSnapshot(
                snapshot_id=f"snap_{offset}",
                person_id="p_marco",
                snap_date=today - timedelta(days=offset),
                weight_kg=75.0 - offset * 0.1,
                active_kcal=500.0,
                total_kcal=2300.0,
            )
        )
    recent = await profile_repo.get_recent_biometric_snapshots("p_marco", before_date=today, days=7)
    assert len(recent) == 4
