"""Tests for v0.2.6 Homemade Composite Recipes and Dynamic Plan-Log Sync."""

from __future__ import annotations

from datetime import date
from typing import Any

import aiohttp
import pytest
from pydantic import BaseModel

from custom_components.ha_calories_buddy.core.llm.base import LLMClient
from custom_components.ha_calories_buddy.core.llm.prompts.meal_plan import (
    DayPlanProposalSchema,
    PlannedIngredientSpec,
    PlannedMealSkeleton,
)
from custom_components.ha_calories_buddy.core.llm.prompts.recipe_parse import (
    ParsedRecipeIngredientSchema,
    ParsedRecipeSchema,
)
from custom_components.ha_calories_buddy.core.models import (
    ConfidenceLevel,
    FoodItem,
    FoodSource,
    IngredientRole,
    LabelStandard,
    LLMConfig,
    Macros,
    MealLog,
    MealSlotConfig,
    MealStatus,
    PersonProfile,
    PlanGenerationOptions,
    PreparationState,
    RecipeIngredientInput,
    TargetMode,
)
from custom_components.ha_calories_buddy.core.nutrition.recipes import RecipeService
from custom_components.ha_calories_buddy.core.nutrition.resolver import FoodResolver
from custom_components.ha_calories_buddy.core.physiology.targets import TargetResult
from custom_components.ha_calories_buddy.core.planner.meal_parser import (
    MealLoggingService,
)
from custom_components.ha_calories_buddy.core.planner.meal_planner import (
    MealPlannerService,
)
from custom_components.ha_calories_buddy.core.planner.preferences import (
    PreferenceEngine,
)
from custom_components.ha_calories_buddy.core.planner.rebalancer import (
    MealRebalancerService,
)
from custom_components.ha_calories_buddy.core.storage.database import Database
from custom_components.ha_calories_buddy.core.storage.food_repo import FoodRepository
from custom_components.ha_calories_buddy.core.storage.meal_repo import MealRepository
from custom_components.ha_calories_buddy.core.storage.plan_repo import (
    MealPlanRepository,
)
from custom_components.ha_calories_buddy.core.storage.preference_repo import (
    PreferenceRepository,
)
from custom_components.ha_calories_buddy.core.storage.profile_repo import (
    ProfileRepository,
)
from custom_components.ha_calories_buddy.core.storage.recipe_repo import (
    RecipeRepository,
)
from custom_components.ha_calories_buddy.core.storage.seeder import (
    normalize_text,
    seed_database_if_empty,
)


class _ScriptedLLMClient(LLMClient):
    """Queue-backed local LLMClient test double avoiding HTTP."""

    def __init__(self, responses: list[str]) -> None:
        super().__init__(
            LLMConfig(provider="ollama", model="test", max_retries=1),
            aiohttp.ClientSession(),
        )
        self._responses = list(responses)

    async def _raw_structured_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
    ) -> str:
        return self._responses.pop(0) if self._responses else "{}"

    async def health_check(self) -> bool:
        return True


@pytest.fixture
async def recipe_ctx() -> Any:
    """Provision initialized SQLite DB (including migration 003) and repositories."""
    db = Database(":memory:")
    await db.initialize()
    await seed_database_if_empty(db)

    profile_repo = ProfileRepository(db)
    food_repo = FoodRepository(db)
    meal_repo = MealRepository(db)
    pref_repo = PreferenceRepository(db)
    plan_repo = MealPlanRepository(db)
    recipe_repo = RecipeRepository(db)

    profile = PersonProfile(
        person_id="p_rec",
        config_entry_id="entry_rec",
        display_name="Nico",
        language="it",
        meal_slots=[
            MealSlotConfig(slot_id="colazione", name="Colazione"),
            MealSlotConfig(slot_id="pranzo", name="Pranzo"),
            MealSlotConfig(slot_id="spuntino", name="Spuntino"),
            MealSlotConfig(slot_id="cena", name="Cena"),
        ],
    )
    await profile_repo.upsert_person(profile)

    # Add deterministic baking ingredients into Food Vault
    for fid, name, cat, kcal, p, c, f, fib in [
        ("test_flour", "Farina 00", "grains", 360.0, 11.0, 76.0, 1.0, 2.5),
        ("test_sugar", "Zucchero semolato", "other", 400.0, 0.0, 100.0, 0.0, 0.0),
    ]:
        await food_repo.upsert_global_food(
            FoodItem(
                food_id=fid,
                name=name,
                name_it=name,
                normalized_name=normalize_text(name),
                aliases=[normalize_text(name)],
                category=cat,
                preparation_state=PreparationState.READY_TO_EAT,
                macros_per_100g=Macros(
                    kcal=kcal, protein_g=p, carbs_g=c, fat_g=f, fiber_g=fib
                ),
                label_standard=LabelStandard.EU,
                yield_factor=1.0,
                source=FoodSource.MANUAL,
                verified=True,
            )
        )

    resolver = FoodResolver(food_repo)
    pref_engine = PreferenceEngine(pref_repo, meal_repo)
    recipe_service = RecipeService(
        recipe_repo=recipe_repo,
        food_repo=food_repo,
        resolver=resolver,
    )
    logging_service = MealLoggingService(
        meal_repo=meal_repo,
        food_repo=food_repo,
        resolver=resolver,
        llm_client_getter=lambda _p: None,
    )

    yield {
        "db": db,
        "profile": profile,
        "food_repo": food_repo,
        "meal_repo": meal_repo,
        "plan_repo": plan_repo,
        "recipe_repo": recipe_repo,
        "resolver": resolver,
        "pref_engine": pref_engine,
        "recipe_service": recipe_service,
        "logging_service": logging_service,
    }
    await db.close()


async def test_migration_v3_and_homemade_recipe_lifecycle(
    recipe_ctx: dict[str, Any],
) -> None:
    """Verify homemade recipe creation, final cooked weight, Vault portion logging, update, and safe delete."""
    db: Database = recipe_ctx["db"]
    profile: PersonProfile = recipe_ctx["profile"]
    recipe_service: RecipeService = recipe_ctx["recipe_service"]
    food_repo: FoodRepository = recipe_ctx["food_repo"]
    recipe_repo: RecipeRepository = recipe_ctx["recipe_repo"]
    logging_service: MealLoggingService = recipe_ctx["logging_service"]
    meal_repo: MealRepository = recipe_ctx["meal_repo"]

    # Re-running initialize must stay idempotent and have all applied versions
    await db.initialize()
    mig_rows = await db.fetch_all("SELECT version FROM schema_migrations ORDER BY version")
    assert {1, 2, 3, 4}.issubset({int(r["version"]) for r in mig_rows})

    # Save "Ciambellone della mamma":
    # 300g Farina 00 (1080 kcal) + 200g Zucchero (800 kcal) + 100g Olio EVO (884 kcal) + 3 Uova (165g)
    recipe = await recipe_service.save_recipe(
        profile,
        name="Ciambellone della mamma",
        category="snack",
        servings=12,
        serving_unit="slice",
        final_weight_g=960.0,
        allow_in_planner=False,
        notes="Forno 180°C per 40 min",
        ingredients=[
            RecipeIngredientInput(food_id="test_flour", name="Farina 00", quantity=300, unit="g", state=PreparationState.READY_TO_EAT),
            RecipeIngredientInput(food_id="test_sugar", name="Zucchero semolato", quantity=200, unit="g", state=PreparationState.READY_TO_EAT),
            RecipeIngredientInput(food_id="seed_olive_oil", name="Olio extravergine di oliva", quantity=100, unit="g", state=PreparationState.READY_TO_EAT),
            RecipeIngredientInput(food_id="seed_eggs", name="Uova intere", quantity=3, unit="piece", state=PreparationState.RAW),
        ],
    )
    assert recipe.effective_weight_g == 960.0
    assert recipe.raw_total_grams == pytest.approx(765.0, abs=1.0)
    assert recipe.grams_per_serving == pytest.approx(80.0, abs=0.1)
    assert recipe.macros_per_serving.kcal == pytest.approx(recipe.total_macros.kcal / 12.0, abs=0.3)
    assert recipe.macros_per_100g.kcal == pytest.approx(recipe.total_macros.kcal * 100.0 / 960.0, abs=0.5)

    # Vault FoodItem synced automatically with both 'slice' and 'serving' portions
    eff_food = await food_repo.get_effective_food(recipe.food_id, person_id=profile.person_id)
    assert eff_food is not None
    assert eff_food.source == FoodSource.RECIPE
    assert eff_food.verified is True
    assert eff_food.food_portions["slice"] == pytest.approx(80.0, abs=0.1)
    assert eff_food.food_portions["serving"] == pytest.approx(80.0, abs=0.1)

    # Log "1 fetta" manually by name (simulating Assist / Quick Log portion lookup) -> ConfidenceLevel.HIGH
    res_slice = await logging_service.log_manual_item(
        profile,
        name="Ciambellone della mamma",
        quantity=1,
        unit="fetta",
        state=PreparationState.READY_TO_EAT,
        meal_slot="Spuntino",
        log_date=date.today(),
    )
    assert res_slice.meal_log.status == MealStatus.CONFIRMED
    assert res_slice.meal_log.items[0].food_id == recipe.food_id
    assert res_slice.meal_log.items[0].confidence == ConfidenceLevel.HIGH
    assert res_slice.meal_log.items[0].macros.kcal == pytest.approx(
        recipe.macros_per_serving.kcal, abs=1.5
    )

    # Log 120g by weight -> exact per-100g scaling
    res_grams = await logging_service.log_manual_item(
        profile,
        food_id=recipe.food_id,
        name="Ciambellone della mamma",
        quantity=120,
        unit="g",
        meal_slot="Colazione",
        log_date=date.today(),
        merge_existing_slot=False,
    )
    assert res_grams.meal_log.total_macros.kcal == pytest.approx(
        recipe.macros_per_100g.kcal * 1.2, abs=1.5
    )
    logged_log_id = res_slice.meal_log.log_id

    # Update recipe: rename, switch to 10 pieces with no final_weight_g (falls back to raw_total_grams)
    updated = await recipe_service.save_recipe(
        profile,
        recipe_id=recipe.recipe_id,
        name="Ciambellone light",
        category="snack",
        servings=10,
        serving_unit="piece",
        final_weight_g=None,
        allow_in_planner=True,
        ingredients=[
            RecipeIngredientInput(food_id="test_flour", name="Farina 00", quantity=300, unit="g", state=PreparationState.READY_TO_EAT),
            RecipeIngredientInput(food_id="seed_eggs", name="Uova intere", quantity=3, unit="piece", state=PreparationState.RAW),
        ],
    )
    assert updated.food_id == recipe.food_id
    assert updated.effective_weight_g == updated.raw_total_grams
    assert "Ciambellone light" == updated.name

    # Stale 'slice' portion must be purged by replace_portions=True and name_it must be updated
    eff_updated = await food_repo.get_effective_food(updated.food_id, person_id=profile.person_id)
    assert eff_updated is not None
    assert eff_updated.name_it == "Ciambellone light"
    assert "slice" not in eff_updated.food_portions
    assert eff_updated.food_portions["piece"] == pytest.approx(updated.grams_per_serving, abs=0.1)
    assert updated.food_id in await recipe_repo.list_planner_allowed_food_ids()

    by_food = await recipe_repo.get_recipe_by_food_id(updated.food_id)
    assert by_food is not None
    assert by_food.recipe_id == updated.recipe_id
    listed, total_recs = await recipe_repo.list_recipes(query="ciambellone")
    assert total_recs == 1
    assert listed[0].recipe_id == updated.recipe_id

    # Delete recipe -> removes recipe & Vault food, while preserving logged meal macros
    assert await recipe_service.delete_recipe(recipe.recipe_id) is True
    assert await recipe_repo.get_recipe(recipe.recipe_id) is None
    assert await food_repo.get_by_id(recipe.food_id) is None

    preserved_meal = await meal_repo.get_meal_log(logged_log_id)
    assert preserved_meal is not None
    assert preserved_meal.total_macros.kcal > 200.0
    assert preserved_meal.items[0].food_id is None


async def test_recipe_ai_import_and_planner_opt_in_filtering(
    recipe_ctx: dict[str, Any],
) -> None:
    """Verify LLM recipe text import pre-links Vault foods and planner respects allow_in_planner."""
    profile: PersonProfile = recipe_ctx["profile"]
    recipe_service: RecipeService = recipe_ctx["recipe_service"]

    parsed_payload = ParsedRecipeSchema(
        name="Torta proteica all'avena",
        category="snack",
        servings=8,
        serving_unit="slice",
        ingredients=[
            ParsedRecipeIngredientSchema(name="Fiocchi d'avena", quantity=160, unit="g", preparation_state=PreparationState.RAW),
            ParsedRecipeIngredientSchema(name="Banana", quantity=200, unit="g", preparation_state=PreparationState.READY_TO_EAT),
            ParsedRecipeIngredientSchema(name="Uova intere", quantity=3, unit="piece", preparation_state=PreparationState.RAW),
        ],
    ).model_dump_json()

    client = _ScriptedLLMClient([parsed_payload])
    try:
        ai_recipe_service = RecipeService(
            recipe_repo=recipe_ctx["recipe_repo"],
            food_repo=recipe_ctx["food_repo"],
            resolver=recipe_ctx["resolver"],
            llm_client_getter=lambda _p: client,
        )
        draft = await ai_recipe_service.parse_recipe_from_text(
            profile, "Torta avena 160g avena 200g banana 3 uova 8 fette"
        )
        assert draft["name"] == "Torta proteica all'avena"
        assert draft["serving_unit"] == "slice"
        assert len(draft["ingredients"]) == 3
        assert all(i["resolved"] is True for i in draft["ingredients"])
        assert all(i["raw_name"] for i in draft["ingredients"])
        egg_draft = next(i for i in draft["ingredients"] if "egg" in (i["food_id"] or ""))
        assert "piece" in egg_draft["food_portions"]
        assert egg_draft["category"] == "egg"

        # Save one opt-out recipe and one opt-in recipe
        rec_hidden = await recipe_service.save_recipe(
            profile,
            name="Dolce segreto",
            category="snack",
            servings=6,
            allow_in_planner=False,
            ingredients=[RecipeIngredientInput(food_id="test_flour", name="Farina 00", quantity=200, unit="g")],
        )
        rec_allowed = await recipe_service.save_recipe(
            profile,
            name="Torta avena fit",
            category="snack",
            servings=8,
            allow_in_planner=True,
            ingredients=[RecipeIngredientInput(food_id="seed_oats_raw", name="Fiocchi d'avena", quantity=160, unit="g")],
        )

        planner = MealPlannerService(
            plan_repo=recipe_ctx["plan_repo"],
            meal_repo=recipe_ctx["meal_repo"],
            preference_engine=recipe_ctx["pref_engine"],
            resolver=recipe_ctx["resolver"],
            llm_client_getter=lambda _p: client,
            recipe_repo=recipe_ctx["recipe_repo"],
        )
        # Ensure vault limit captures the newly saved items by keeping default limit=120
        hint = await planner._build_vault_catalog_hint(profile)
        assert rec_hidden.name not in hint
        assert rec_allowed.name in hint
    finally:
        await client._session.close()


async def test_dynamic_plan_sync_rebalance_and_partial_day0_generation(
    recipe_ctx: dict[str, Any],
) -> None:
    """Verify off-plan logged slots are skipped by rebalance_intraday and partial day 0 generation."""
    profile: PersonProfile = recipe_ctx["profile"]
    today = date.today()
    daily_targets = TargetResult(
        target_kcal=2200.0,
        protein_g=145.0,
        carbs_g=240.0,
        fat_g=68.0,
        fiber_g=30.0,
        mode=TargetMode.PHYSIOLOGY,
    )

    # 1. Partial day 0 + meal_prep_mode=True: Colazione and Pranzo already logged today
    await recipe_ctx["meal_repo"].create_meal_log(
        MealLog(
            log_id="logged_col",
            person_id=profile.person_id,
            log_date=today,
            meal_slot="Colazione",
            status=MealStatus.CONFIRMED,
            total_macros=Macros(kcal=420, protein_g=25, carbs_g=55, fat_g=10, fiber_g=6),
            items=[],
        )
    )
    await recipe_ctx["meal_repo"].create_meal_log(
        MealLog(
            log_id="logged_prz",
            person_id=profile.person_id,
            log_date=today,
            meal_slot="Pranzo",
            status=MealStatus.CONFIRMED,
            total_macros=Macros(kcal=780, protein_g=55, carbs_g=85, fat_g=22, fiber_g=9),
            items=[],
        )
    )

    partial_day0_resp = DayPlanProposalSchema(
        meals=[
            PlannedMealSkeleton(
                meal_slot="Spuntino",
                title="Yogurt greco e banana",
                prep_minutes=5,
                ingredients=[
                    PlannedIngredientSpec(name="Yogurt Greco 0% Grassi", grams=170, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.PROTEIN_ANCHOR),
                    PlannedIngredientSpec(name="Banana", grams=120, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.PRODUCE_FIXED),
                ],
            ),
            PlannedMealSkeleton(
                meal_slot="Cena",
                title="Salmone con patate",
                prep_minutes=25,
                ingredients=[
                    PlannedIngredientSpec(name="Salmone fresco", grams=160, preparation_state=PreparationState.RAW, role=IngredientRole.PROTEIN_ANCHOR),
                    PlannedIngredientSpec(name="Patate", grams=250, preparation_state=PreparationState.RAW, role=IngredientRole.CARB_ANCHOR),
                    PlannedIngredientSpec(name="Olio extravergine di oliva", grams=10, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.FAT_ANCHOR),
                ],
            ),
        ]
    ).model_dump_json()

    full_day1_resp = DayPlanProposalSchema(
        meals=[
            PlannedMealSkeleton(
                meal_slot="Colazione",
                title="Porridge proteico e mandorle",
                prep_minutes=10,
                ingredients=[
                    PlannedIngredientSpec(name="Fiocchi d'avena", grams=60, preparation_state=PreparationState.RAW, role=IngredientRole.CARB_ANCHOR),
                    PlannedIngredientSpec(name="Yogurt Greco 0% Grassi", grams=170, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.PROTEIN_ANCHOR),
                    PlannedIngredientSpec(name="Mandorle", grams=15, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.FAT_ANCHOR),
                ],
            ),
            PlannedMealSkeleton(
                meal_slot="Pranzo",
                title="Pollo e riso basmati",
                prep_minutes=20,
                ingredients=[
                    PlannedIngredientSpec(name="Petto di pollo", grams=160, preparation_state=PreparationState.RAW, role=IngredientRole.PROTEIN_ANCHOR),
                    PlannedIngredientSpec(name="Riso basmati", grams=80, preparation_state=PreparationState.RAW, role=IngredientRole.CARB_ANCHOR),
                    PlannedIngredientSpec(name="Olio extravergine di oliva", grams=10, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.FAT_ANCHOR),
                ],
            ),
            PlannedMealSkeleton(
                meal_slot="Spuntino",
                title="Ricotta e fette biscottate",
                prep_minutes=5,
                ingredients=[
                    PlannedIngredientSpec(name="Ricotta Vaccina", grams=100, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.PROTEIN_ANCHOR),
                    PlannedIngredientSpec(name="Fette Biscottate", grams=30, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.CARB_ANCHOR),
                ],
            ),
            PlannedMealSkeleton(
                meal_slot="Cena",
                title="Merluzzo e patate",
                prep_minutes=25,
                ingredients=[
                    PlannedIngredientSpec(name="Merluzzo", grams=200, preparation_state=PreparationState.RAW, role=IngredientRole.PROTEIN_ANCHOR),
                    PlannedIngredientSpec(name="Patate", grams=220, preparation_state=PreparationState.RAW, role=IngredientRole.CARB_ANCHOR),
                    PlannedIngredientSpec(name="Olio extravergine di oliva", grams=12, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.FAT_ANCHOR),
                ],
            ),
        ]
    ).model_dump_json()

    client = _ScriptedLLMClient([partial_day0_resp, full_day1_resp])
    try:
        planner = MealPlannerService(
            plan_repo=recipe_ctx["plan_repo"],
            meal_repo=recipe_ctx["meal_repo"],
            preference_engine=recipe_ctx["pref_engine"],
            resolver=recipe_ctx["resolver"],
            llm_client_getter=lambda _p: client,
            recipe_repo=recipe_ctx["recipe_repo"],
        )
        rebalancer = MealRebalancerService(
            plan_repo=recipe_ctx["plan_repo"],
            meal_repo=recipe_ctx["meal_repo"],
            resolver=recipe_ctx["resolver"],
            planner_service=planner,
        )

        # Generate 2-day plan with meal_prep_mode=True: day 0 generates ONLY Spuntino + Cena, day 1 still generates ALL 4 slots
        plan = await planner.generate_meal_plan(
            profile,
            start_date=today,
            daily_targets=daily_targets,
            options=PlanGenerationOptions(num_days=2, meal_prep_mode=True),
            reference_today=today,
        )
        assert [it.meal_slot for it in plan.days[0].items] == ["Spuntino", "Cena"]
        assert [it.meal_slot for it in plan.days[1].items] == [
            "Colazione",
            "Pranzo",
            "Spuntino",
            "Cena",
        ]
        day0_sum_kcal = sum(it.macros.kcal for it in plan.days[0].items)
        # Day 0 remaining budget was 2200 - (420 + 780) = 1000 kcal
        assert day0_sum_kcal == pytest.approx(1000.0, abs=120.0)

        # Now log an off-plan Spuntino manually -> rebalance_intraday must skip Spuntino and only adjust Cena
        await recipe_ctx["logging_service"].log_manual_item(
            profile,
            food_id="seed_banana",
            quantity=150,
            unit="g",
            meal_slot="Spuntino",
            log_date=today,
        )
        reb = await rebalancer.rebalance_intraday(
            profile,
            target_date=today,
            daily_targets=daily_targets,
            allow_llm_swap=False,
        )
        assert [it.meal_slot for it in reb.updated_items] == ["Cena"]

        # Finally log Cena too -> generating a new 2-day plan emits 0 items on today cleanly and 4 on tomorrow
        await recipe_ctx["meal_repo"].create_meal_log(
            MealLog(
                log_id="logged_cen",
                person_id=profile.person_id,
                log_date=today,
                meal_slot="Cena",
                status=MealStatus.CONFIRMED,
                total_macros=Macros(kcal=650, protein_g=45, carbs_g=60, fat_g=20, fiber_g=6),
                items=[],
            )
        )
        full_client = _ScriptedLLMClient([full_day1_resp])
        try:
            planner2 = MealPlannerService(
                plan_repo=recipe_ctx["plan_repo"],
                meal_repo=recipe_ctx["meal_repo"],
                preference_engine=recipe_ctx["pref_engine"],
                resolver=recipe_ctx["resolver"],
                llm_client_getter=lambda _p: full_client,
                recipe_repo=recipe_ctx["recipe_repo"],
            )
            plan2 = await planner2.generate_meal_plan(
                profile,
                start_date=today,
                daily_targets=daily_targets,
                options=PlanGenerationOptions(num_days=2, meal_prep_mode=True),
                reference_today=today,
            )
            assert plan2.days[0].items == []
            assert len(plan2.days[1].items) == 4
        finally:
            await full_client._session.close()
    finally:
        await client._session.close()


async def test_recipe_save_reassociated_and_freetext_ingredients(
    recipe_ctx: dict[str, Any],
) -> None:
    """Verify recipe editor allows swapping to seed oil/new items and saving unlinked free text resolved by resolver."""
    profile: PersonProfile = recipe_ctx["profile"]
    recipe_service: RecipeService = recipe_ctx["recipe_service"]

    rec = await recipe_service.save_recipe(
        profile,
        name="Torta casalinga con olio di semi",
        category="snack",
        servings=10,
        serving_unit="slice",
        ingredients=[
            # Re-associated seed ingredient using tablespoon unit
            RecipeIngredientInput(
                food_id="seed_sunflower_oil",
                name="Olio di Semi di Girasole",
                quantity=6,
                unit="tbsp",
                state=PreparationState.READY_TO_EAT,
            ),
            # Unlinked free-text ingredient (no food_id -> resolver matches Vault flour_00!)
            RecipeIngredientInput(
                food_id=None,
                name="Farina 00",
                quantity=250,
                unit="g",
                state=PreparationState.RAW,
            ),
        ],
    )
    assert len(rec.ingredients) == 2
    oil_ing = rec.ingredients[0]
    flour_ing = rec.ingredients[1]
    assert oil_ing.food_id == "seed_sunflower_oil"
    assert oil_ing.grams == pytest.approx(60.0)
    assert flour_ing.food_id == "seed_flour_00"
    assert flour_ing.grams == pytest.approx(250.0)
    assert rec.macros_per_serving.kcal > 100.0
