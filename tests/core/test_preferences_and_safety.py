"""Pure-Python tests for two-tier Preferences, Feedback, and Zero-Trust Hard Constraints."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from custom_components.ha_calories_buddy.core.models import (
    EffectiveFood,
    FoodSource,
    LabelStandard,
    Macros,
    MealFeedbackRating,
    MealLog,
    MealLogItem,
    MealStatus,
    PersonProfile,
    PreferenceScope,
    PreferenceStance,
    PreparationState,
)
from custom_components.ha_calories_buddy.core.planner.preferences import (
    PreferenceEngine,
)
from custom_components.ha_calories_buddy.core.storage.database import Database
from custom_components.ha_calories_buddy.core.storage.meal_repo import MealRepository
from custom_components.ha_calories_buddy.core.storage.preference_repo import (
    PreferenceRepository,
)
from custom_components.ha_calories_buddy.core.storage.profile_repo import (
    ProfileRepository,
)


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    """Provide an initialized temporary SQLite database."""
    database = Database(tmp_path / "prefs.db")
    await database.initialize()
    yield database
    await database.close()


def _person(person_id: str, name: str, **kwargs: object) -> PersonProfile:
    return PersonProfile(
        person_id=person_id,
        config_entry_id=f"ce_{person_id}",
        display_name=name,
        **kwargs,  # type: ignore[arg-type]
    )


async def test_preference_repo_two_tier_crud_isolation_and_feedback(db: Database) -> None:
    """Verify Global vs Person CRUD, ownership isolation, and recipe feedback."""
    profile_repo = ProfileRepository(db)
    pref_repo = PreferenceRepository(db)
    meal_repo = MealRepository(db)

    alice = _person("p_alice", "Alice", dietary_regime="omnivore")
    bob = _person("p_bob", "Bob", dietary_regime="omnivore")
    await profile_repo.upsert_person(alice)
    await profile_repo.upsert_person(bob)

    # Global household dislike vs Alice person override LOVE
    g_pref = await pref_repo.upsert_preference(
        scope=PreferenceScope.GLOBAL,
        target_type="ingredient",
        target_value="Coriander",
        stance=PreferenceStance.DISLIKE,
        notes="Household default",
    )
    a_override = await pref_repo.upsert_preference(
        scope=PreferenceScope.PERSON,
        person_id="p_alice",
        target_type="ingredient",
        target_value="Coriander",
        stance=PreferenceStance.LOVE,
        notes="Alice loves it",
    )
    # Re-upsert deduplicates on target instead of creating duplicates
    await pref_repo.upsert_preference(
        scope=PreferenceScope.GLOBAL,
        target_type="ingredient",
        target_value="Peanuts",
        stance=PreferenceStance.ALLERGY,
    )
    await pref_repo.upsert_preference(
        scope=PreferenceScope.GLOBAL,
        target_type="ingredient",
        target_value="peanuts",
        stance=PreferenceStance.ALLERGY,
        notes="Strict household peanut ban",
    )
    global_only = await pref_repo.list_preferences(include_global=True)
    assert len([p for p in global_only if p.target_value.lower() == "peanuts"]) == 1

    # Bob cannot delete Alice's personal preference; Alice can
    assert await pref_repo.delete_preference(a_override.pref_id, person_id="p_bob") is False
    assert await pref_repo.delete_preference(a_override.pref_id, person_id="p_alice") is True
    await pref_repo.upsert_preference(
        scope=PreferenceScope.PERSON,
        person_id="p_alice",
        target_type="ingredient",
        target_value="Coriander",
        stance=PreferenceStance.LOVE,
    )

    # Feedback upsert with signature collision + ownership check
    fb1 = await pref_repo.upsert_feedback(
        person_id="p_alice",
        title="Pollo e Riso Basmati!",
        rating=MealFeedbackRating.LIKE,
    )
    fb2 = await pref_repo.upsert_feedback(
        person_id="p_alice",
        title="pollo e riso basmati",
        rating=MealFeedbackRating.NEVER_SUGGEST,
        notes="Tired of it",
    )
    assert fb1.feedback_id == fb2.feedback_id
    assert fb2.rating == MealFeedbackRating.NEVER_SUGGEST

    # Frequent foods + recent logs feed PreferenceEngine
    await meal_repo.create_meal_log(
        MealLog(
            log_id="log_a1",
            person_id="p_alice",
            log_date=date.today(),
            meal_slot="Lunch",
            logged_at=datetime.now(UTC),
            status=MealStatus.CONFIRMED,
            raw_input_text="Oats with berries",
            total_macros=Macros(kcal=350, protein_g=14, carbs_g=55, fat_g=7),
            items=[
                MealLogItem(
                    item_id="i1",
                    log_id="log_a1",
                    food_id=None,
                    display_name="Rolled Oats",
                    brand="Quaker",
                    input_quantity=80,
                    input_unit="g",
                    input_state=PreparationState.RAW,
                    resolved_grams_ref=80,
                    macros=Macros(kcal=300, protein_g=11, carbs_g=47, fat_g=6),
                )
            ],
        )
    )

    engine = PreferenceEngine(pref_repo, meal_repo)
    alice_ctx = await engine.build_context(alice)
    bob_ctx = await engine.build_context(bob)

    assert "Coriander" in alice_ctx.liked_items
    assert "Coriander" not in alice_ctx.disliked_items
    assert "Coriander" in bob_ctx.disliked_items
    assert "peanuts" in {t.lower() for t in bob_ctx.hard_constraints}
    # Cold-start guard: single-day food appears in eaten_today_foods, NOT top_frequent_foods
    assert "Rolled Oats" in alice_ctx.eaten_today_foods
    assert not alice_ctx.top_frequent_foods
    assert not bob_ctx.top_frequent_foods

    # Logging the same food across 2 earlier distinct days (3 distinct days total) promotes it
    for day_offset, lid in ((1, "log_a2"), (2, "log_a3")):
        await meal_repo.create_meal_log(
            MealLog(
                log_id=lid,
                person_id="p_alice",
                log_date=date.today() - timedelta(days=day_offset),
                meal_slot="Breakfast",
                logged_at=datetime.now(UTC) - timedelta(days=day_offset),
                status=MealStatus.CONFIRMED,
                raw_input_text="Oats",
                total_macros=Macros(kcal=300, protein_g=11, carbs_g=47, fat_g=6),
                items=[
                    MealLogItem(
                        item_id=f"item_{lid}",
                        log_id=lid,
                        food_id=None,
                        display_name="Rolled Oats",
                        brand="Quaker",
                        input_quantity=80,
                        input_unit="g",
                        input_state=PreparationState.RAW,
                        resolved_grams_ref=80,
                        macros=Macros(kcal=300, protein_g=11, carbs_g=47, fat_g=6),
                    )
                ],
            )
        )
    alice_ctx_multi = await engine.build_context(alice)
    assert any("Rolled Oats" in f for f in alice_ctx_multi.top_frequent_foods)

    eaten_names, _ = await meal_repo.get_eaten_today_foods("p_alice", date.today())
    assert "Rolled Oats" in eaten_names

    assert "pollo e riso basmati" in {t.lower() for t in alice_ctx.never_suggest_titles}
    assert await pref_repo.delete_feedback("p_bob", fb2.feedback_id) is False
    assert await pref_repo.delete_feedback("p_alice", fb2.feedback_id) is True
    assert await pref_repo.delete_preference(g_pref.pref_id) is True


async def test_zero_trust_hard_constraints_allergens_and_regimes(db: Database) -> None:
    """Adversarially verify allergens, bilingual synonyms, word boundaries, and regimes."""
    profile_repo = ProfileRepository(db)
    pref_repo = PreferenceRepository(db)
    meal_repo = MealRepository(db)
    engine = PreferenceEngine(pref_repo, meal_repo)

    # 1. Gluten + Lactose + Peanuts allergic profile
    allergic = _person(
        "p_allergic",
        "Sensitive",
        allergies=["arachidi", "crostacei"],
        intolerances=["glutine", "lattosio"],
        blacklist=["coriandolo"],
    )
    await profile_repo.upsert_person(allergic)
    ctx = await engine.build_context(allergic)

    # Bilingual allergen / intolerance detection
    assert engine.check_hard_constraints(
        title="Toast con burro di arachidi",
        ingredient_names=["Pane integrale", "Burro di arachidi"],
        context=ctx,
    )
    assert engine.check_hard_constraints(
        title="Spaghetti aglio e olio",
        ingredient_names=["Spaghetti di semola", "Olio EVO"],
        context=ctx,
    )
    assert engine.check_hard_constraints(
        title="Risotto con gamberi e mozzarella",
        ingredient_names=["Riso", "Gamberi", "Mozzarella"],
        context=ctx,
    )
    # Clean dish passes with 0 violations (and false-positive 'nocera' must not match 'noci')
    clean = engine.check_hard_constraints(
        title="Pollo arrosto stile Nocera con patate",
        ingredient_names=["Petto di pollo", "Patate", "Olio extravergine"],
        context=ctx,
    )
    assert clean == []

    # 2. Vegan & Vegetarian & Pescatarian regime checks (including resolved food category)
    vegan = _person("p_vegan", "Vegan", dietary_regime="vegan")
    await profile_repo.upsert_person(vegan)
    v_ctx = await engine.build_context(vegan)

    poultry_food = EffectiveFood(
        food_id="seed_chicken",
        name="Chicken Breast",
        name_it="Petto di Pollo",
        normalized_name="chicken breast",
        category="poultry",
        preparation_state=PreparationState.RAW,
        macros_per_100g=Macros(kcal=110, protein_g=23, carbs_g=0, fat_g=2),
        label_standard=LabelStandard.EU,
        yield_factor=0.75,
        source=FoodSource.SEED,
        verified=True,
        atwater_warning=False,
    )
    dairy_food = EffectiveFood(
        food_id="seed_milk",
        name="Whole Milk",
        normalized_name="whole milk",
        category="dairy",
        preparation_state=PreparationState.READY_TO_EAT,
        macros_per_100g=Macros(kcal=64, protein_g=3.3, carbs_g=4.9, fat_g=3.6),
        label_standard=LabelStandard.EU,
        yield_factor=1.0,
        source=FoodSource.SEED,
        verified=True,
        atwater_warning=False,
    )
    lentil_food = EffectiveFood(
        food_id="seed_lentils",
        name="Lentils",
        normalized_name="lentils",
        category="legumes",
        preparation_state=PreparationState.COOKED,
        macros_per_100g=Macros(kcal=116, protein_g=9, carbs_g=16, fat_g=0.4, fiber_g=7.9),
        label_standard=LabelStandard.EU,
        yield_factor=2.6,
        source=FoodSource.SEED,
        verified=True,
        atwater_warning=False,
    )

    assert engine.check_hard_constraints(
        title="Bowl proteica",
        ingredient_names=["Pollo", "Riso"],
        resolved_foods=[poultry_food, None],
        context=v_ctx,
    )
    assert engine.check_hard_constraints(
        title="Porridge",
        ingredient_names=["Avena", "Milk"],
        resolved_foods=[None, dairy_food],
        context=v_ctx,
    )
    assert (
        engine.check_hard_constraints(
            title="Curry di lenticchie e riso basmati",
            ingredient_names=["Lenticchie cotte", "Riso basmati"],
            resolved_foods=[lentil_food, None],
            context=v_ctx,
        )
        == []
    )

    pescatarian = _person("p_pesca", "Pesca", dietary_regime="pescatarian")
    await profile_repo.upsert_person(pescatarian)
    p_ctx = await engine.build_context(pescatarian)
    fish_food = EffectiveFood(
        food_id="seed_salmon",
        name="Fresh Salmon",
        normalized_name="fresh salmon",
        category="fish",
        preparation_state=PreparationState.RAW,
        macros_per_100g=Macros(kcal=182, protein_g=20, carbs_g=0, fat_g=11.3),
        label_standard=LabelStandard.EU,
        yield_factor=0.82,
        source=FoodSource.SEED,
        verified=True,
        atwater_warning=False,
    )
    assert (
        engine.check_hard_constraints(
            title="Salmone al forno con verdure",
            ingredient_names=["Salmone fresco", "Zucchine"],
            resolved_foods=[fish_food, None],
            context=p_ctx,
        )
        == []
    )
    assert engine.check_hard_constraints(
        title="Tagliata di manzo",
        ingredient_names=["Manzo"],
        context=p_ctx,
    )
