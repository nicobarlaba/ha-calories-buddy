"""Pure-Python tests for MealPlannerService, PlanRepository, Zero-Trust safety, and Rebalancer."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from pydantic import BaseModel

from custom_components.ha_calories_buddy.core.llm.base import LLMClient
from custom_components.ha_calories_buddy.core.llm.prompts.food_estimate import (
    EstimatedFoodSchema,
)
from custom_components.ha_calories_buddy.core.llm.prompts.meal_plan import (
    DayPlanProposalSchema,
    MealSuggestionListSchema,
    PlannedIngredientSpec,
    PlannedMealSkeleton,
    SingleMealSwapSchema,
)
from custom_components.ha_calories_buddy.core.models import (
    ActivityLevel,
    GoalType,
    IngredientRole,
    LLMConfig,
    Macros,
    MealLog,
    MealLogItem,
    MealStatus,
    PersonProfile,
    PlanGenerationOptions,
    PlanItemStatus,
    PreparationState,
    Sex,
    TargetMode,
    TDEEEngineType,
)
from custom_components.ha_calories_buddy.core.nutrition.resolver import FoodResolver
from custom_components.ha_calories_buddy.core.physiology.targets import TargetResult
from custom_components.ha_calories_buddy.core.planner.meal_planner import (
    HardConstraintViolationError,
    MealPlannerService,
)
from custom_components.ha_calories_buddy.core.planner.preferences import (
    PreferenceEngine,
)
from custom_components.ha_calories_buddy.core.planner.rebalancer import (
    MealRebalancerService,
)
from custom_components.ha_calories_buddy.core.planner.slot_resolver import (
    default_slots_for_language,
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
from custom_components.ha_calories_buddy.core.storage.seeder import (
    seed_database_if_empty,
)


class ScriptedLLMClient(LLMClient):
    """Queue-backed pure Python LLMClient mock avoiding HTTP."""

    def __init__(self, responses: list[str]) -> None:
        super().__init__(
            LLMConfig(provider="ollama", model="test", max_retries=1),
            aiohttp.ClientSession(),  # unused; closed cleanly in close()
        )
        self._responses = list(responses)
        self.prompts_seen: list[str] = []
        self.system_prompts_seen: list[str] = []

    async def _raw_structured_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
    ) -> str:
        self.system_prompts_seen.append(system_prompt)
        self.prompts_seen.append(user_prompt)
        if not self._responses:
            raise AssertionError("ScriptedLLMClient ran out of scripted responses")
        return self._responses.pop(0)

    async def health_check(self) -> bool:
        """Return True for mocked LLM client."""
        return True


@pytest.fixture
async def ctx(tmp_path: Path) -> dict[str, Any]:
    """Initialize DB, repos, and services for planner & rebalancer tests."""
    db = Database(tmp_path / "planner.db")
    await db.initialize()
    await seed_database_if_empty(db)

    profile_repo = ProfileRepository(db)
    food_repo = FoodRepository(db)
    meal_repo = MealRepository(db)
    pref_repo = PreferenceRepository(db)
    plan_repo = MealPlanRepository(db)
    resolver = FoodResolver(food_repo)
    pref_engine = PreferenceEngine(pref_repo, meal_repo)

    profile = PersonProfile(
        person_id="p_nico",
        config_entry_id="ce_nico",
        display_name="Nicola",
        language="it",
        sex=Sex.MALE,
        birth_year=1990,
        height_cm=178.0,
        initial_weight_kg=74.0,
        target_mode=TargetMode.PHYSIOLOGY,
        goal_type=GoalType.MAINTAIN,
        tdee_engine=TDEEEngineType.STATIC,
        activity_level=ActivityLevel.MODERATE,
        dietary_regime="omnivore",
        allergies=["arachidi"],
        meal_slots=default_slots_for_language("it"),
    )
    await profile_repo.upsert_person(profile)

    yield {
        "db": db,
        "profile_repo": profile_repo,
        "food_repo": food_repo,
        "meal_repo": meal_repo,
        "pref_repo": pref_repo,
        "plan_repo": plan_repo,
        "resolver": resolver,
        "pref_engine": pref_engine,
        "profile": profile,
    }
    await db.close()


def _safe_day_skeletons() -> DayPlanProposalSchema:
    return DayPlanProposalSchema(
        meals=[
            PlannedMealSkeleton(
                meal_slot="Colazione",
                title="Porridge proteico e mandorle",
                prep_minutes=10,
                steps=["Cuoci i fiocchi d'avena", "Unisci yogurt greco e mandorle"],
                ingredients=[
                    PlannedIngredientSpec(name="Fiocchi d'avena", grams=60, preparation_state=PreparationState.RAW, role=IngredientRole.CARB_ANCHOR),
                    PlannedIngredientSpec(name="Yogurt greco 0%", grams=170, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.PROTEIN_ANCHOR),
                    PlannedIngredientSpec(name="Mandorle", grams=15, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.FAT_ANCHOR),
                ],
            ),
            PlannedMealSkeleton(
                meal_slot="Pranzo",
                title="Pollo con riso basmati e zucchine",
                prep_minutes=20,
                steps=["Cuoci il riso basmati", "Griglia il pollo con olio"],
                ingredients=[
                    PlannedIngredientSpec(name="Riso basmati", grams=80, preparation_state=PreparationState.RAW, role=IngredientRole.CARB_ANCHOR),
                    PlannedIngredientSpec(name="Petto di pollo", grams=150, preparation_state=PreparationState.RAW, role=IngredientRole.PROTEIN_ANCHOR),
                    PlannedIngredientSpec(name="Olio extravergine di oliva", grams=12, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.FAT_ANCHOR),
                    PlannedIngredientSpec(name="Zucchine", grams=150, preparation_state=PreparationState.RAW, role=IngredientRole.PRODUCE_FIXED),
                ],
            ),
            PlannedMealSkeleton(
                meal_slot="Spuntino",
                title="Pane integrale e yogurt greco",
                prep_minutes=5,
                steps=["Servi yogurt greco con pane"],
                ingredients=[
                    PlannedIngredientSpec(name="Pane integrale", grams=40, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.CARB_ANCHOR),
                    PlannedIngredientSpec(name="Yogurt greco 0%", grams=150, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.PROTEIN_ANCHOR),
                ],
            ),
            PlannedMealSkeleton(
                meal_slot="Cena",
                title="Salmone al forno con patate e spinaci",
                prep_minutes=25,
                steps=["Cuoci salmone e patate in forno"],
                ingredients=[
                    PlannedIngredientSpec(name="Salmone fresco", grams=160, preparation_state=PreparationState.RAW, role=IngredientRole.PROTEIN_ANCHOR),
                    PlannedIngredientSpec(name="Patate", grams=220, preparation_state=PreparationState.RAW, role=IngredientRole.CARB_ANCHOR),
                    PlannedIngredientSpec(name="Olio extravergine di oliva", grams=8, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.FAT_ANCHOR),
                ],
            ),
        ]
    )


async def test_suggest_generate_meal_prep_swap_and_promote(ctx: dict[str, Any]) -> None:
    """Verify Chef suggestions, 3-day meal_prep_mode reuse, single swap, and 1-tap promote."""
    profile: PersonProfile = ctx["profile"]

    suggest_payload = MealSuggestionListSchema(
        suggestions=[
            PlannedMealSkeleton(
                meal_slot="Cena",
                title="Salmone e riso basmati",
                prep_minutes=20,
                steps=["Cuoci il riso", "Rosola il salmone"],
                ingredients=[
                    PlannedIngredientSpec(name="Salmone fresco", grams=150, preparation_state=PreparationState.RAW, role=IngredientRole.PROTEIN_ANCHOR),
                    PlannedIngredientSpec(name="Riso basmati", grams=80, preparation_state=PreparationState.RAW, role=IngredientRole.CARB_ANCHOR),
                    PlannedIngredientSpec(name="Olio extravergine di oliva", grams=10, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.FAT_ANCHOR),
                ],
            ),
            PlannedMealSkeleton(
                meal_slot="Cena",
                title="Pollo e lenticchie cotte",
                prep_minutes=25,
                ingredients=[
                    PlannedIngredientSpec(name="Petto di pollo", grams=140, preparation_state=PreparationState.RAW, role=IngredientRole.PROTEIN_ANCHOR),
                    PlannedIngredientSpec(name="Lenticchie cotte", grams=180, preparation_state=PreparationState.COOKED, role=IngredientRole.CARB_ANCHOR),
                ],
            ),
        ]
    ).model_dump_json()

    day1_payload = _safe_day_skeletons().model_dump_json()
    day3_payload = _safe_day_skeletons().model_dump_json()
    swap_payload = SingleMealSwapSchema(
        meal=PlannedMealSkeleton(
            meal_slot="Cena",
            title="Merluzzo con patate e olio",
            prep_minutes=20,
            ingredients=[
                PlannedIngredientSpec(name="Merluzzo", grams=200, preparation_state=PreparationState.RAW, role=IngredientRole.PROTEIN_ANCHOR),
                PlannedIngredientSpec(name="Patate", grams=250, preparation_state=PreparationState.RAW, role=IngredientRole.CARB_ANCHOR),
                PlannedIngredientSpec(name="Olio extravergine di oliva", grams=14, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.FAT_ANCHOR),
            ],
        )
    ).model_dump_json()

    # Notice num_days=3 with meal_prep_mode=True consumes ONLY 2 day LLM calls (day 1 & day 3; day 2 reuses day 1)
    client = ScriptedLLMClient([suggest_payload, day1_payload, day3_payload, swap_payload])
    try:
        planner = MealPlannerService(
            plan_repo=ctx["plan_repo"],
            meal_repo=ctx["meal_repo"],
            preference_engine=ctx["pref_engine"],
            resolver=ctx["resolver"],
            llm_client_getter=lambda _p: client,
        )

        # 1. On-demand meal suggestion
        suggestions = await planner.suggest_meal(
            profile,
            meal_slot="Cena",
            target_macros=Macros(kcal=700, protein_g=45, carbs_g=70, fat_g=24, fiber_g=10),
            user_hint="veloce",
            count=2,
        )
        assert len(suggestions) == 2
        assert all(s.meal_slot == "Cena" for s in suggestions)
        assert suggestions[0].macros.kcal > 0

        # 2. 3-day plan with meal_prep_mode=True
        today = date.today()
        daily_targets = TargetResult(
            target_kcal=2300.0,
            protein_g=155.0,
            carbs_g=250.0,
            fat_g=70.0,
            fiber_g=32.0,
            mode=TargetMode.PHYSIOLOGY,
        )
        plan = await planner.generate_meal_plan(
            profile,
            start_date=today,
            daily_targets=daily_targets,
            options=PlanGenerationOptions(num_days=3, meal_prep_mode=True),
        )
        assert len(plan.days) == 3
        assert len(client._responses) == 1  # only swap_payload remains!
        assert plan.days[1].items[1].title == plan.days[0].items[1].title  # batch reused!

        persisted = await ctx["plan_repo"].get_active_plan(profile.person_id, reference_date=today)
        assert persisted is not None
        assert persisted.plan_id == plan.plan_id

        # 3. Swap Dinner on day 0
        dinner_item = next(it for it in persisted.days[0].items if it.meal_slot == "Cena")
        swapped = await planner.swap_plan_meal(
            profile, dinner_item.plan_item_id, user_instruction="pesce bianco"
        )
        assert "Merluzzo" in swapped.title

        # 4. 1-tap promote Lunch with actual_scale=1.2
        lunch_item = next(it for it in persisted.days[0].items if it.meal_slot == "Pranzo")
        logged = await planner.promote_plan_meal(profile, lunch_item.plan_item_id, actual_scale=1.2)
        assert logged.status == MealStatus.CONFIRMED
        assert logged.source_channel == "plan_promote"
        assert logged.plan_item_id == lunch_item.plan_item_id
        assert logged.total_macros.kcal == pytest.approx(lunch_item.macros.kcal * 1.2, abs=5.0)

        after_promote = await ctx["plan_repo"].get_plan_item(profile.person_id, lunch_item.plan_item_id)
        assert after_promote is not None
        assert after_promote[1].status == PlanItemStatus.CONSUMED
    finally:
        await client._session.close()


async def test_adversarial_allergen_rejection_retry_and_block(ctx: dict[str, Any]) -> None:
    """Reject LLM proposals containing peanuts/allergens, retry cleanly, or block SQLite write."""
    profile: PersonProfile = ctx["profile"]

    poisoned = MealSuggestionListSchema(
        suggestions=[
            PlannedMealSkeleton(
                meal_slot="Spuntino",
                title="Yogurt e burro di arachidi",
                ingredients=[
                    PlannedIngredientSpec(name="Yogurt greco 0%", grams=150, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.PROTEIN_ANCHOR),
                    PlannedIngredientSpec(name="Burro di arachidi", grams=20, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.FAT_ANCHOR),
                ],
            )
        ]
    ).model_dump_json()

    repaired = MealSuggestionListSchema(
        suggestions=[
            PlannedMealSkeleton(
                meal_slot="Spuntino",
                title="Yogurt greco con mandorle",
                ingredients=[
                    PlannedIngredientSpec(name="Yogurt greco 0%", grams=150, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.PROTEIN_ANCHOR),
                    PlannedIngredientSpec(name="Mandorle", grams=20, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.FAT_ANCHOR),
                ],
            )
        ]
    ).model_dump_json()

    # Attempt 1 poisoned, Attempt 2 clean -> succeeds
    client_recover = ScriptedLLMClient([poisoned, repaired])
    try:
        planner = MealPlannerService(
            plan_repo=ctx["plan_repo"],
            meal_repo=ctx["meal_repo"],
            preference_engine=ctx["pref_engine"],
            resolver=ctx["resolver"],
            llm_client_getter=lambda _p: client_recover,
        )
        res = await planner.suggest_meal(
            profile,
            meal_slot="Spuntino",
            target_macros=Macros(kcal=250, protein_g=20, carbs_g=10, fat_g=12),
        )
        assert "mandorle" in res[0].title.lower()
        assert "REJECTED DUE TO FORBIDDEN INGREDIENTS" in client_recover.prompts_seen[1]
    finally:
        await client_recover._session.close()

    # If ALL retries still violate allergen constraints -> HardConstraintViolationError, 0 DB plans written
    bad_day = DayPlanProposalSchema(
        meals=[
            PlannedMealSkeleton(
                meal_slot="Colazione",
                title="Toast alle arachidi",
                ingredients=[
                    PlannedIngredientSpec(name="Peanuts", grams=30, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.FAT_ANCHOR),
                ],
            )
        ]
    ).model_dump_json()
    client_fail = ScriptedLLMClient([bad_day, bad_day, bad_day])
    try:
        planner_fail = MealPlannerService(
            plan_repo=ctx["plan_repo"],
            meal_repo=ctx["meal_repo"],
            preference_engine=ctx["pref_engine"],
            resolver=ctx["resolver"],
            llm_client_getter=lambda _p: client_fail,
        )
        with pytest.raises(HardConstraintViolationError) as exc_info:
            await planner_fail.generate_meal_plan(
                profile,
                start_date=date.today(),
                daily_targets=TargetResult(
                    target_kcal=2200, protein_g=140, carbs_g=250, fat_g=70, fiber_g=30, mode=TargetMode.PHYSIOLOGY
                ),
                options=PlanGenerationOptions(num_days=1),
            )
        assert exc_info.value.violations
        assert await ctx["plan_repo"].get_active_plan(profile.person_id) is None
    finally:
        await client_fail._session.close()


async def test_rebalancer_intraday_and_multiday_floors(ctx: dict[str, Any]) -> None:
    """Verify deterministic intraday rescale, LLM swap fallback, and strict multiday floors."""
    profile: PersonProfile = ctx["profile"]
    today = date.today()

    # Seed a 3-day active plan via MealPlannerService
    day_payload = _safe_day_skeletons().model_dump_json()
    swap_payload = SingleMealSwapSchema(
        meal=PlannedMealSkeleton(
            meal_slot="Cena",
            title="Merluzzo leggero",
            ingredients=[
                PlannedIngredientSpec(name="Merluzzo", grams=150, preparation_state=PreparationState.RAW, role=IngredientRole.PROTEIN_ANCHOR),
                PlannedIngredientSpec(name="Zucchine", grams=200, preparation_state=PreparationState.RAW, role=IngredientRole.PRODUCE_FIXED),
            ],
        )
    ).model_dump_json()

    client = ScriptedLLMClient([day_payload, day_payload, day_payload, swap_payload, swap_payload])
    try:
        planner = MealPlannerService(
            plan_repo=ctx["plan_repo"],
            meal_repo=ctx["meal_repo"],
            preference_engine=ctx["pref_engine"],
            resolver=ctx["resolver"],
            llm_client_getter=lambda _p: client,
        )
        rebalancer = MealRebalancerService(
            plan_repo=ctx["plan_repo"],
            meal_repo=ctx["meal_repo"],
            resolver=ctx["resolver"],
            planner_service=planner,
        )

        # No plan yet -> returns mode='no_items'
        daily_targets = TargetResult(
            target_kcal=2200.0, protein_g=150.0, carbs_g=240.0, fat_g=70.0, fiber_g=31.0, mode=TargetMode.PHYSIOLOGY
        )
        empty_res = await rebalancer.rebalance_intraday(
            profile, target_date=today, daily_targets=daily_targets
        )
        assert empty_res.mode == "no_items"

        # Generate 3-day plan
        plan = await planner.generate_meal_plan(
            profile,
            start_date=today,
            daily_targets=daily_targets,
            options=PlanGenerationOptions(num_days=3, meal_prep_mode=False),
        )

        # Consume Colazione & Pranzo, then log a moderate extra snack -> deterministic rescale handles it
        day0 = plan.days[0]
        for slot in ("Colazione", "Pranzo"):
            it = next(x for x in day0.items if x.meal_slot == slot)
            await planner.promote_plan_meal(profile, it.plan_item_id)

        # Modest extra snack so remaining dinner budget stays feasible (~500 kcal)
        await ctx["meal_repo"].create_meal_log(
            MealLog(
                log_id="extra_mod",
                person_id=profile.person_id,
                log_date=today,
                meal_slot="Spuntino",
                status=MealStatus.CONFIRMED,
                total_macros=Macros(kcal=350, protein_g=10, carbs_g=45, fat_g=14, fiber_g=3),
                items=[],
            )
        )
        # Skip original planned Spuntino so only Cena remains
        spuntino_it = next(x for x in day0.items if x.meal_slot == "Spuntino")
        await ctx["plan_repo"].update_plan_item_status(
            profile.person_id, spuntino_it.plan_item_id, PlanItemStatus.SKIPPED
        )

        intra_res = await rebalancer.rebalance_intraday(
            profile, target_date=today, daily_targets=daily_targets, allow_llm_swap=True
        )
        assert intra_res.mode in {"intraday_rescale", "intraday_swap"}
        assert len(intra_res.updated_items) == 1

        # Huge caloric excess triggers strict multiday physiological floor (max(1500, 0.85 * 1700)=1500, fat >= 0.65 * 74 = 48.1)
        multi_res = await rebalancer.rebalance_multiday(
            profile,
            start_date=today,
            excess_kcal=4500.0,
            bmr_kcal=1700.0,
            weight_kg=74.0,
            spread_days=3,
        )
        assert multi_res.mode == "multiday_smooth"
        assert len(multi_res.updated_days) == 2  # today+1 and today+2 in a 3-day plan
        assert "daily_calorie_floor_enforced" in multi_res.safety_warnings

        for future_day in multi_res.updated_days:
            pday = await ctx["plan_repo"].get_plan_day(profile.person_id, future_day)
            assert pday is not None
            assert pday.target_macros.kcal >= 1500.0
            assert pday.target_macros.fat_g >= (0.65 * 74.0) - 0.2
    finally:
        await client._session.close()


async def test_v023_tier4_fallback_diversity_anti_repetition_and_grouped_log(
    ctx: dict[str, Any],
) -> None:
    """Verify Tier 4 candidate fallback, same-day anti-repetition retry, course labels, and grouped log."""
    profile: PersonProfile = ctx["profile"]
    today = date.today()

    # Lunch already eaten today with Pasta and Lentils (should be avoided at Cena!)
    await ctx["meal_repo"].create_meal_log(
        MealLog(
            log_id="lunch_pasta_lentils",
            person_id=profile.person_id,
            log_date=today,
            meal_slot="Pranzo",
            status=MealStatus.CONFIRMED,
            total_macros=Macros(kcal=580, protein_g=24, carbs_g=92, fat_g=10, fiber_g=9),
            items=[
                MealLogItem(
                    item_id="li_pasta",
                    log_id="lunch_pasta_lentils",
                    food_id="seed_pasta_semolina_raw",
                    display_name="Pasta di Semola (Cruda)",
                    input_quantity=80,
                    input_unit="g",
                    input_state=PreparationState.RAW,
                    resolved_grams_ref=80,
                    macros=Macros(kcal=285.6, protein_g=10, carbs_g=57.6, fat_g=1.2, fiber_g=2.4),
                ),
                MealLogItem(
                    item_id="li_lentils",
                    log_id="lunch_pasta_lentils",
                    food_id="seed_lentils_cooked",
                    display_name="Lenticchie (Cotte / Lessate)",
                    input_quantity=250,
                    input_unit="g",
                    input_state=PreparationState.COOKED,
                    resolved_grams_ref=250,
                    macros=Macros(kcal=290, protein_g=22.5, carbs_g=40, fat_g=1, fiber_g=19.8),
                ),
            ],
        )
    )

    # Attempt 1 repeats lunch lentils + pasta twice -> quality retry triggers once!
    repetitive_payload = MealSuggestionListSchema(
        suggestions=[
            PlannedMealSkeleton(
                meal_slot="Cena",
                title="Pasta con lenticchie",
                prep_minutes=20,
                ingredients=[
                    PlannedIngredientSpec(name="Pasta di Semola (Cruda)", grams=80, preparation_state=PreparationState.RAW, role=IngredientRole.CARB_ANCHOR, course="Primo"),
                    PlannedIngredientSpec(name="Lenticchie (Cotte / Lessate)", grams=180, preparation_state=PreparationState.COOKED, role=IngredientRole.PROTEIN_ANCHOR, course="Secondo"),
                ],
            ),
            PlannedMealSkeleton(
                meal_slot="Cena",
                title="Pasta al pomodoro",
                prep_minutes=15,
                ingredients=[
                    PlannedIngredientSpec(name="Pasta di Semola (Cruda)", grams=90, preparation_state=PreparationState.RAW, role=IngredientRole.CARB_ANCHOR, course="Primo"),
                    PlannedIngredientSpec(name="Passata di pomodoro", grams=150, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.PRODUCE_FIXED, course="Contorno"),
                ],
            ),
        ]
    ).model_dump_json()

    # Attempt 2 includes 1 exotic unseeded item ('Tempeh') to exercise Tier 4 LLM fallback
    diversified_payload = MealSuggestionListSchema(
        suggestions=[
            PlannedMealSkeleton(
                meal_slot="Cena",
                title="Orata al forno con patate e rucola",
                prep_minutes=25,
                steps=["Cuoci l'orata con le patate", "Servi con rucola fresca"],
                ingredients=[
                    PlannedIngredientSpec(name="Orata / Branzino (Crudo)", grams=220, preparation_state=PreparationState.RAW, role=IngredientRole.PROTEIN_ANCHOR, course="Secondo"),
                    PlannedIngredientSpec(name="Patate (Crude)", grams=260, preparation_state=PreparationState.RAW, role=IngredientRole.CARB_ANCHOR, course="Pane / Carbo"),
                    PlannedIngredientSpec(name="Rucola", grams=60, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.PRODUCE_FIXED, course="Contorno"),
                    PlannedIngredientSpec(name="Olio Extravergine di Oliva", grams=15, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.FAT_ANCHOR, course="Contorno"),
                ],
            ),
            PlannedMealSkeleton(
                meal_slot="Cena",
                title="Tempeh con riso basmati e melanzane",
                prep_minutes=20,
                steps=["Griglia il tempeh", "Cuoci il riso basmati"],
                ingredients=[
                    PlannedIngredientSpec(name="Tempeh", grams=180, preparation_state=PreparationState.READY_TO_EAT, role=IngredientRole.PROTEIN_ANCHOR, course="Secondo"),
                    PlannedIngredientSpec(name="Riso Basmati (Crudo)", grams=85, preparation_state=PreparationState.RAW, role=IngredientRole.CARB_ANCHOR, course="Pane / Carbo"),
                    PlannedIngredientSpec(name="Melanzane (Crude)", grams=180, preparation_state=PreparationState.RAW, role=IngredientRole.PRODUCE_FIXED, course="Contorno"),
                ],
            ),
        ]
    ).model_dump_json()

    tier4_estimate_payload = EstimatedFoodSchema(
        name="Tempeh",
        category="other",
        preparation_state=PreparationState.READY_TO_EAT,
        kcal_per_100g=195.0,
        protein_g_per_100g=19.0,
        carbs_g_per_100g=9.0,
        fat_g_per_100g=10.0,
        fiber_g_per_100g=5.0,
        yield_factor=1.0,
    ).model_dump_json()

    client = ScriptedLLMClient([repetitive_payload, diversified_payload, tier4_estimate_payload])
    try:
        planner = MealPlannerService(
            plan_repo=ctx["plan_repo"],
            meal_repo=ctx["meal_repo"],
            preference_engine=ctx["pref_engine"],
            resolver=ctx["resolver"],
            llm_client_getter=lambda _p: client,
        )
        dinner_budget = Macros(kcal=1100.0, protein_g=65.0, carbs_g=120.0, fat_g=34.0, fiber_g=14.0)
        suggestions = await planner.suggest_meal(
            profile,
            meal_slot="Cena",
            target_macros=dinner_budget,
            count=2,
            reference_date=today,
            budget_mode="remaining_last_slot",
        )
        assert len(suggestions) == 2
        assert "ALREADY EATEN TODAY" in client.system_prompts_seen[0]
        assert "MULTI-COURSE STRUCTURE" in client.system_prompts_seen[0]
        assert "Verified Food Vault ingredients" in client.system_prompts_seen[0]
        assert "QUALITY IMPROVEMENT NEEDED" in client.prompts_seen[1]

        # Tier 4 estimated food produces real > 0 kcal instead of 0 kcal!
        tempeh_opt = next(s for s in suggestions if "Tempeh" in s.title)
        tempeh_ing = next(i for i in tempeh_opt.ingredients if "Tempeh" in i.name)
        assert tempeh_ing.macros.kcal > 0.0
        assert tempeh_ing.course == "Secondo"
        assert suggestions[0].target_macros is not None
        assert suggestions[0].target_macros.kcal == 1100.0
        assert suggestions[0].budget_mode == "remaining_last_slot"

        # 1-tap grouped logging persists 1 single MealLog containing all suggestion items
        orata_opt = next(s for s in suggestions if "Orata" in s.title)
        logged = await planner.log_suggestion_as_meal(profile, orata_opt, log_date=today)
        assert logged.status == MealStatus.CONFIRMED
        assert logged.source_channel == "suggestion_accept"
        assert logged.raw_input_text == orata_opt.title
        assert len(logged.items) == len(orata_opt.ingredients)
        assert logged.total_macros.kcal == pytest.approx(orata_opt.macros.kcal, abs=2.0)
    finally:
        await client._session.close()
