"""Pure-Python tests for bounded algebraic macro scaling and slot allocation."""

from __future__ import annotations

import pytest

from custom_components.ha_calories_buddy.core.models import (
    EffectiveFood,
    FoodSource,
    IngredientRole,
    LabelStandard,
    Macros,
    MealSlotConfig,
    PreparationState,
    TargetMode,
)
from custom_components.ha_calories_buddy.core.physiology.targets import TargetResult
from custom_components.ha_calories_buddy.core.planner.macro_scaler import (
    ScalableIngredientInput,
    allocate_slot_targets,
    classify_ingredient_role,
    compute_smart_slot_budget,
    scale_day_meals,
    scale_meal_ingredients,
)
from custom_components.ha_calories_buddy.core.planner.slot_resolver import (
    default_slots_for_language,
    slot_approx_minutes,
)


def _food(
    food_id: str,
    name: str,
    category: str,
    *,
    state: PreparationState = PreparationState.RAW,
    kcal: float,
    p: float,
    c: float,
    f: float,
    fib: float = 0.0,
    yf: float = 1.0,
) -> EffectiveFood:
    return EffectiveFood(
        food_id=food_id,
        name=name,
        normalized_name=name.lower(),
        category=category,
        preparation_state=state,
        macros_per_100g=Macros(kcal=kcal, protein_g=p, carbs_g=c, fat_g=f, fiber_g=fib),
        label_standard=LabelStandard.EU,
        yield_factor=yf,
        source=FoodSource.SEED,
        verified=True,
        atwater_warning=False,
    )


def test_allocate_slot_targets_and_role_classification() -> None:
    """Verify slot budget split sums back to daily targets and classifies roles accurately."""
    daily = TargetResult(
        target_kcal=2400.0,
        protein_g=160.0,
        carbs_g=270.0,
        fat_g=75.0,
        fiber_g=34.0,
        mode=TargetMode.PHYSIOLOGY,
    )
    slots4 = default_slots_for_language("it")
    alloc4 = allocate_slot_targets(daily, slots4)
    assert set(alloc4.keys()) == {"Colazione", "Pranzo", "Spuntino", "Cena"}
    assert sum(m.kcal for m in alloc4.values()) == pytest.approx(2400.0, abs=0.5)
    assert sum(m.protein_g for m in alloc4.values()) == pytest.approx(160.0, abs=0.5)
    assert sum((m.fiber_g or 0.0) for m in alloc4.values()) == pytest.approx(34.0, abs=0.5)

    # Custom explicit kcal_share_pct weights
    custom = [
        MealSlotConfig(slot_id="b", name="Breakfast", kcal_share_pct=30.0),
        MealSlotConfig(slot_id="l", name="Lunch", kcal_share_pct=70.0),
    ]
    alloc_custom = allocate_slot_targets(daily, custom)
    assert alloc_custom["Breakfast"].kcal == pytest.approx(720.0, abs=0.5)
    assert alloc_custom["Lunch"].kcal == pytest.approx(1680.0, abs=0.5)
    assert allocate_slot_targets(daily, []) == {}

    chicken = _food("f_chicken", "Chicken", "poultry", kcal=110, p=23.1, c=0, f=1.9)
    rice = _food("f_rice", "Rice", "rice", kcal=352, p=8.5, c=77.7, f=0.5)
    oil = _food("f_oil", "Olive Oil", "oil", state=PreparationState.READY_TO_EAT, kcal=884, p=0, c=0, f=98.2)
    zucchini = _food("f_zuc", "Zucchini", "vegetables", kcal=17, p=1.2, c=3.1, f=0.3, fib=1.0)
    espresso = _food("f_cof", "Espresso", "beverage", state=PreparationState.READY_TO_EAT, kcal=2, p=0.1, c=0, f=0)

    assert classify_ingredient_role(chicken) == IngredientRole.PROTEIN_ANCHOR
    assert classify_ingredient_role(rice) == IngredientRole.CARB_ANCHOR
    assert classify_ingredient_role(oil) == IngredientRole.FAT_ANCHOR
    assert classify_ingredient_role(zucchini) == IngredientRole.PRODUCE_FIXED
    assert classify_ingredient_role(espresso) == IngredientRole.CONDIMENT_FIXED
    assert classify_ingredient_role(None, IngredientRole.PROTEIN_ANCHOR) == IngredientRole.PROTEIN_ANCHOR


def test_scale_meal_and_day_converge_within_five_percent() -> None:
    """Verify algebraic coordinate descent hits <= +-5% daily accuracy within culinary bounds."""
    chicken = _food("f_chicken", "Petto di pollo", "poultry", kcal=110, p=23.1, c=0.0, f=1.9)
    rice = _food("f_rice", "Riso Basmati", "rice", kcal=352, p=8.5, c=77.7, f=0.5, fib=1.4)
    oil = _food("f_oil", "Olio EVO", "oil", state=PreparationState.READY_TO_EAT, kcal=884, p=0, c=0, f=98.2)
    zucchini = _food("f_zuc", "Zucchine", "vegetables", kcal=17, p=1.2, c=3.1, f=0.3, fib=1.0)
    oats = _food("f_oats", "Fiocchi d'avena", "oats", kcal=374, p=13.5, c=58.7, f=7.0, fib=10.1)
    yogurt = _food("f_yog", "Yogurt Greco 0%", "dairy", state=PreparationState.READY_TO_EAT, kcal=57, p=10.3, c=3.6, f=0.2)
    almonds = _food("f_alm", "Mandorle", "nuts", state=PreparationState.READY_TO_EAT, kcal=579, p=21.2, c=9.5, f=49.9, fib=12.5)
    salmon = _food("f_sal", "Salmone fresco", "fish", kcal=182, p=20.0, c=0.0, f=11.3)
    bread = _food("f_bread", "Pane integrale", "bread", state=PreparationState.READY_TO_EAT, kcal=247, p=8.5, c=44.0, f=2.5, fib=6.5)

    # Single lunch meal scaling test
    lunch_target = Macros(kcal=750.0, protein_g=50.0, carbs_g=85.0, fat_g=22.0, fiber_g=10.0)
    lunch_ings = [
        ScalableIngredientInput("Riso Basmati", rice, 70.0, PreparationState.RAW, IngredientRole.CARB_ANCHOR),
        ScalableIngredientInput("Petto di pollo", chicken, 120.0, PreparationState.RAW, IngredientRole.PROTEIN_ANCHOR),
        ScalableIngredientInput("Olio EVO", oil, 10.0, PreparationState.READY_TO_EAT, IngredientRole.FAT_ANCHOR),
        ScalableIngredientInput("Zucchine", zucchini, 150.0, PreparationState.RAW, IngredientRole.PRODUCE_FIXED),
    ]
    scaled_lunch, lunch_tot, lunch_warn = scale_meal_ingredients(lunch_ings, lunch_target)
    assert lunch_warn is False
    assert abs(lunch_tot.kcal - lunch_target.kcal) / lunch_target.kcal <= 0.05
    assert abs(lunch_tot.protein_g - lunch_target.protein_g) / lunch_target.protein_g <= 0.10
    # Grams obey sensible bounds
    rice_g = next(i.grams for i in scaled_lunch if i.name == "Riso Basmati")
    oil_g = next(i.grams for i in scaled_lunch if i.name == "Olio EVO")
    assert 40.0 <= rice_g <= 180.0
    assert 3.0 <= oil_g <= 25.0

    # Full day multi-slot convergence test
    daily_target = TargetResult(
        target_kcal=2200.0,
        protein_g=150.0,
        carbs_g=240.0,
        fat_g=70.0,
        fiber_g=31.0,
        mode=TargetMode.PHYSIOLOGY,
    )
    slot_alloc = allocate_slot_targets(daily_target, default_slots_for_language("it"))
    day_skeletons = [
        (
            "Colazione",
            [
                ScalableIngredientInput("Avena", oats, 50.0, PreparationState.RAW, IngredientRole.CARB_ANCHOR),
                ScalableIngredientInput("Yogurt", yogurt, 150.0, PreparationState.READY_TO_EAT, IngredientRole.PROTEIN_ANCHOR),
                ScalableIngredientInput("Mandorle", almonds, 10.0, PreparationState.READY_TO_EAT, IngredientRole.FAT_ANCHOR),
            ],
            slot_alloc["Colazione"],
        ),
        ("Pranzo", lunch_ings, slot_alloc["Pranzo"]),
        (
            "Spuntino",
            [
                ScalableIngredientInput("Yogurt", yogurt, 150.0, PreparationState.READY_TO_EAT, IngredientRole.PROTEIN_ANCHOR),
                ScalableIngredientInput("Mandorle", almonds, 15.0, PreparationState.READY_TO_EAT, IngredientRole.FAT_ANCHOR),
                ScalableIngredientInput("Pane", bread, 40.0, PreparationState.READY_TO_EAT, IngredientRole.CARB_ANCHOR),
            ],
            slot_alloc["Spuntino"],
        ),
        (
            "Cena",
            [
                ScalableIngredientInput("Salmone", salmon, 150.0, PreparationState.RAW, IngredientRole.PROTEIN_ANCHOR),
                ScalableIngredientInput("Pane", bread, 70.0, PreparationState.READY_TO_EAT, IngredientRole.CARB_ANCHOR),
                ScalableIngredientInput("Olio EVO", oil, 10.0, PreparationState.READY_TO_EAT, IngredientRole.FAT_ANCHOR),
                ScalableIngredientInput("Zucchine", zucchini, 180.0, PreparationState.RAW, IngredientRole.PRODUCE_FIXED),
            ],
            slot_alloc["Cena"],
        ),
    ]
    day_target_macros = Macros(
        kcal=daily_target.target_kcal,
        protein_g=daily_target.protein_g,
        carbs_g=daily_target.carbs_g,
        fat_g=daily_target.fat_g,
        fiber_g=daily_target.fiber_g,
    )
    _, day_tot, day_warn = scale_day_meals(day_skeletons, day_target_macros)
    assert day_warn is False
    assert abs(day_tot.kcal - day_target_macros.kcal) / day_target_macros.kcal <= 0.05
    assert abs(day_tot.protein_g - day_target_macros.protein_g) / day_target_macros.protein_g <= 0.08

    # Impossible bounds produce macro_fit_warning=True rather than fabricating impossible portions
    _, _, impossible_warn = scale_meal_ingredients(
        [ScalableIngredientInput("Zucchine", zucchini, 100.0, PreparationState.RAW, IngredientRole.PRODUCE_FIXED)],
        Macros(kcal=2500.0, protein_g=180.0, carbs_g=250.0, fat_g=80.0),
    )
    assert impossible_warn is True
    assert scale_meal_ingredients([], lunch_target)[2] is True
    assert scale_day_meals([], day_target_macros)[2] is True


def test_compute_smart_slot_budget_and_main_meal_bounds() -> None:
    """Verify remaining-aware budgets, raw carb lower-bound guard, and course propagation."""
    daily = TargetResult(
        target_kcal=2200.0,
        protein_g=160.0,
        carbs_g=240.0,
        fat_g=67.0,
        fiber_g=31.0,
        mode=TargetMode.PHYSIOLOGY,
    )
    slots = default_slots_for_language("it")
    zero = Macros(kcal=0.0, protein_g=0.0, carbs_g=0.0, fat_g=0.0, fiber_g=0.0)

    # 1. Auto on empty day falls back to standard slot target
    b_empty, mode_empty = compute_smart_slot_budget(
        daily, slots, "Cena", zero, set(), budget_mode="auto"
    )
    assert mode_empty == "slot_standard"
    assert b_empty.kcal == pytest.approx(660.0, abs=1.0)

    # 2. Auto on last remaining slot (Cena after Colazione/Pranzo/Spuntino eaten) uses 100% remaining
    eaten_day = Macros(kcal=1002.0, protein_g=52.0, carbs_g=120.0, fat_g=19.0, fiber_g=15.0)
    b_cena, mode_cena = compute_smart_slot_budget(
        daily,
        slots,
        "Cena",
        eaten_day,
        {"Colazione", "Pranzo", "Spuntino"},
        budget_mode="auto",
    )
    assert mode_cena == "remaining_last_slot"
    assert b_cena.kcal == pytest.approx(1198.0, abs=1.0)
    assert b_cena.protein_g == pytest.approx(108.0, abs=1.0)

    # 3. Auto at Pranzo shares remaining across Pranzo + Spuntino + Cena proportionally
    consumed_bk = Macros(kcal=440.0, protein_g=25.0, carbs_g=55.0, fat_g=12.0, fiber_g=6.0)
    b_pranzo, mode_pranzo = compute_smart_slot_budget(
        daily, slots, "Pranzo", consumed_bk, {"Colazione"}, budget_mode="auto"
    )
    assert mode_pranzo == "remaining_shared"
    assert 600.0 <= b_pranzo.kcal <= 1050.0

    # 4. Over-budget day falls back to bounded light budget
    over = Macros(kcal=2300.0, protein_g=170.0, carbs_g=250.0, fat_g=75.0, fiber_g=32.0)
    b_over, mode_over = compute_smart_slot_budget(
        daily, slots, "Cena", over, {"Colazione", "Pranzo"}, budget_mode="auto"
    )
    assert mode_over == "over_budget_light"
    assert b_over.kcal == 180.0

    # 5. Explicit 'remaining' and 'slot' modes
    b_rem_full, mode_rem = compute_smart_slot_budget(
        daily, slots, "Pranzo", consumed_bk, {"Colazione"}, budget_mode="remaining"
    )
    assert mode_rem == "remaining_full"
    assert b_rem_full.kcal == pytest.approx(1760.0, abs=1.0)

    b_slot, mode_slot = compute_smart_slot_budget(
        daily, slots, "Cena", eaten_day, {"Pranzo"}, budget_mode="slot"
    )
    assert mode_slot == "slot_standard"
    assert b_slot.kcal == pytest.approx(660.0, abs=1.0)

    # 6. Main-meal raw pasta guard: cannot collapse below 45g even if target favors lower carbs,
    #    and course labels are preserved verbatim on PlannedIngredient
    pasta = _food("f_pasta", "Pasta di Semola", "pasta", kcal=357, p=12.5, c=72.0, f=1.5, yf=2.2)
    chicken = _food("f_chicken", "Petto di pollo", "poultry", kcal=110, p=23.1, c=0.0, f=1.9)
    oil = _food("f_oil", "Olio EVO", "oil", state=PreparationState.READY_TO_EAT, kcal=884, p=0, c=0, f=98.2)
    low_carb_dinner = Macros(kcal=520.0, protein_g=60.0, carbs_g=20.0, fat_g=20.0, fiber_g=4.0)
    scaled_ings, _, _ = scale_meal_ingredients(
        [
            ScalableIngredientInput("Pasta di Semola", pasta, 70.0, PreparationState.RAW, IngredientRole.CARB_ANCHOR, course="Primo"),
            ScalableIngredientInput("Petto di pollo", chicken, 150.0, PreparationState.RAW, IngredientRole.PROTEIN_ANCHOR, course="Secondo"),
            ScalableIngredientInput("Olio EVO", oil, 10.0, PreparationState.READY_TO_EAT, IngredientRole.FAT_ANCHOR, course="Contorno"),
        ],
        low_carb_dinner,
    )
    pasta_out = next(i for i in scaled_ings if i.name == "Pasta di Semola")
    chicken_out = next(i for i in scaled_ings if i.name == "Petto di pollo")
    assert pasta_out.grams >= 45.0
    assert chicken_out.grams >= 90.0
    assert pasta_out.course == "Primo"
    assert chicken_out.course == "Secondo"


def test_semantic_six_slot_allocation_and_slot_times() -> None:
    """Verify 6-slot Italian profiles allocate larger main-meal budgets and distinct times."""
    slots6 = [
        MealSlotConfig(slot_id="col", name="Colazione"),
        MealSlotConfig(slot_id="sp_m", name="Spuntino"),
        MealSlotConfig(slot_id="pr", name="Pranzo"),
        MealSlotConfig(slot_id="mer", name="Merenda"),
        MealSlotConfig(slot_id="cen", name="Cena"),
        MealSlotConfig(slot_id="sp_s", name="Spuntino Serale"),
    ]
    mins = [slot_approx_minutes(s, all_slots=slots6) for s in slots6]
    assert mins == [8 * 60, 10 * 60 + 30, 13 * 60, 16 * 60 + 30, 20 * 60, 22 * 60]

    daily = TargetResult(
        target_kcal=2160.0,
        protein_g=155.0,
        carbs_g=240.0,
        fat_g=64.0,
        fiber_g=30.0,
        mode=TargetMode.PHYSIOLOGY,
    )
    alloc = allocate_slot_targets(daily, slots6)
    assert alloc["Pranzo"].kcal >= 600.0
    assert alloc["Cena"].kcal >= 550.0
    for snack_name in ("Spuntino", "Merenda", "Spuntino Serale"):
        assert alloc[snack_name].kcal < 250.0
        assert alloc[snack_name].protein_g < 16.0

    assert sum(m.kcal for m in alloc.values()) == pytest.approx(2160.0, abs=0.5)
    assert sum(m.protein_g for m in alloc.values()) == pytest.approx(155.0, abs=0.5)
    assert sum(m.carbs_g for m in alloc.values()) == pytest.approx(240.0, abs=0.5)
    assert sum(m.fat_g for m in alloc.values()) == pytest.approx(64.0, abs=0.5)
    assert sum((m.fiber_g or 0.0) for m in alloc.values()) == pytest.approx(30.0, abs=0.5)
