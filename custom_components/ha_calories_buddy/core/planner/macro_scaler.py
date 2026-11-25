"""Deterministic bounded algebraic macro scaler for meal plans and suggestions."""

from __future__ import annotations

from dataclasses import dataclass

from custom_components.ha_calories_buddy.core.models import (
    EffectiveFood,
    IngredientRole,
    Macros,
    MealSlotConfig,
    PlannedIngredient,
    PreparationState,
)
from custom_components.ha_calories_buddy.core.nutrition.calculator import (
    compute_macros_for_grams,
    sum_macros,
)
from custom_components.ha_calories_buddy.core.nutrition.yield_factors import (
    convert_to_reference_grams,
)
from custom_components.ha_calories_buddy.core.physiology.targets import TargetResult
from custom_components.ha_calories_buddy.core.planner.slot_resolver import (
    slot_approx_minutes,
)
from custom_components.ha_calories_buddy.core.storage.seeder import normalize_text

DEFAULT_SHARES_BY_COUNT: dict[int, tuple[float, ...]] = {
    1: (100.0,),
    2: (45.0, 55.0),
    3: (25.0, 40.0, 35.0),
    4: (22.0, 35.0, 13.0, 30.0),
    5: (20.0, 10.0, 32.0, 10.0, 28.0),
}


@dataclass
class ScalableIngredientInput:
    """Resolved ingredient skeleton ready for algebraic quantity optimization."""

    name: str
    food: EffectiveFood | None
    proposed_grams: float
    preparation_state: PreparationState
    role: IngredientRole
    brand: str | None = None
    course: str | None = None


def allocate_slot_targets(
    daily_targets: TargetResult,
    slots: list[MealSlotConfig],
) -> dict[str, Macros]:
    """Split daily kcal/P/C/F/fiber targets cleanly across a person's meal slots."""
    if not slots:
        return {}

    count = len(slots)
    explicit = [s.kcal_share_pct for s in slots]
    if all(v is not None and v > 0 for v in explicit):
        raw_weights = [float(v) for v in explicit if v is not None]
    else:
        preset = DEFAULT_SHARES_BY_COUNT.get(count)
        if preset is not None:
            raw_weights = list(preset)
        else:
            raw_weights = [100.0 / count] * count

    total_w = sum(raw_weights) or 100.0
    result: dict[str, Macros] = {}
    for slot, weight in zip(slots, raw_weights, strict=True):
        frac = weight / total_w
        result[slot.name] = Macros(
            kcal=round(daily_targets.target_kcal * frac, 1),
            protein_g=round(daily_targets.protein_g * frac, 1),
            carbs_g=round(daily_targets.carbs_g * frac, 1),
            fat_g=round(daily_targets.fat_g * frac, 1),
            fiber_g=round(daily_targets.fiber_g * frac, 1),
        )
    return result


def compute_smart_slot_budget(
    daily_targets: TargetResult,
    slots: list[MealSlotConfig],
    target_slot_name: str,
    consumed_today: Macros,
    eaten_slots: set[str],
    *,
    budget_mode: str = "auto",
) -> tuple[Macros, str]:
    """Compute remaining-aware target macros for an on-demand meal suggestion."""
    slot_targets = allocate_slot_targets(daily_targets, slots)
    fallback_slot = slot_targets.get(
        target_slot_name,
        Macros(
            kcal=round(daily_targets.target_kcal * 0.35, 1),
            protein_g=round(daily_targets.protein_g * 0.35, 1),
            carbs_g=round(daily_targets.carbs_g * 0.35, 1),
            fat_g=round(daily_targets.fat_g * 0.35, 1),
            fiber_g=round(daily_targets.fiber_g * 0.35, 1),
        ),
    )

    mode = (budget_mode or "auto").strip().lower()
    if mode == "slot":
        return fallback_slot, "slot_standard"

    rem_k = daily_targets.target_kcal - consumed_today.kcal
    rem_p = daily_targets.protein_g - consumed_today.protein_g
    rem_c = daily_targets.carbs_g - consumed_today.carbs_g
    rem_f = daily_targets.fat_g - consumed_today.fat_g
    rem_fib = daily_targets.fiber_g - (consumed_today.fiber_g or 0.0)

    def _floored(scale: float = 1.0) -> Macros:
        return Macros(
            kcal=round(max(180.0, rem_k * scale), 1),
            protein_g=round(max(10.0, rem_p * scale), 1),
            carbs_g=round(max(0.0, rem_c * scale), 1),
            fat_g=round(max(3.0, rem_f * scale), 1),
            fiber_g=round(max(0.0, rem_fib * scale), 1),
        )

    if mode == "remaining":
        return _floored(1.0), "remaining_full"

    # Auto mode: respect consumed meals & share across remaining un-eaten slots
    if consumed_today.kcal <= 0.0 and not eaten_slots:
        return fallback_slot, "slot_standard"

    if rem_k < 180.0:
        light_budget = Macros(
            kcal=180.0,
            protein_g=round(max(10.0, min(25.0, max(rem_p, 15.0))), 1),
            carbs_g=round(max(0.0, min(20.0, rem_c)), 1),
            fat_g=round(max(3.0, min(6.0, rem_f)), 1),
            fiber_g=round(max(0.0, min(6.0, rem_fib)), 1),
        )
        return light_budget, "over_budget_light"

    if not slots:
        return _floored(1.0), "remaining_last_slot"

    ordered = sorted(
        enumerate(slots),
        key=lambda pair: (slot_approx_minutes(pair[1]), pair[0]),
    )
    ordered_slots = [s for _, s in ordered]
    target_norm = normalize_text(target_slot_name)
    target_idx = next(
        (i for i, s in enumerate(ordered_slots) if normalize_text(s.name) == target_norm),
        len(ordered_slots) - 1,
    )
    eaten_norm = {normalize_text(s) for s in eaten_slots}
    future_slots = [
        s
        for s in ordered_slots[target_idx:]
        if normalize_text(s.name) == target_norm
        or normalize_text(s.name) not in eaten_norm
    ]

    if len(future_slots) <= 1:
        return _floored(1.0), "remaining_last_slot"

    weights = [max(slot_targets.get(s.name, fallback_slot).kcal, 50.0) for s in future_slots]
    total_weight = sum(weights) or 100.0
    target_weight = max(fallback_slot.kcal, 50.0)
    share = target_weight / total_weight
    return _floored(share), "remaining_shared"


def classify_ingredient_role(
    food: EffectiveFood | None,
    hint: IngredientRole | None = None,
) -> IngredientRole:
    """Verify or infer an ingredient's scaling role from its macro density."""
    if food is None:
        return hint or IngredientRole.PRODUCE_FIXED

    cat = food.category.lower()
    m = food.macros_per_100g
    total_energy = max(m.kcal, (4.0 * m.protein_g) + (4.0 * m.carbs_g) + (9.0 * m.fat_g), 1.0)
    p_share = (4.0 * m.protein_g) / total_energy
    c_share = (4.0 * m.carbs_g) / total_energy
    f_share = (9.0 * m.fat_g) / total_energy

    if cat in {"oil", "nuts"} or f_share >= 0.60:
        return IngredientRole.FAT_ANCHOR
    if cat in {"vegetables", "fruit"}:
        return IngredientRole.PRODUCE_FIXED
    if cat in {"beverage"} or m.kcal < 25.0:
        return IngredientRole.CONDIMENT_FIXED
    if p_share >= 0.35 or cat in {"poultry", "red_meat", "fish", "egg"}:
        return IngredientRole.PROTEIN_ANCHOR
    if c_share >= 0.45 or cat in {"rice", "pasta", "grains", "oats", "bread", "potatoes", "legumes"}:
        return IngredientRole.CARB_ANCHOR
    return hint or IngredientRole.PRODUCE_FIXED


def _gram_bounds(
    item: ScalableIngredientInput,
    target: Macros | None = None,
) -> tuple[float, float]:
    base = max(1.0, item.proposed_grams)
    role = item.role
    cat = item.food.category.lower() if item.food is not None else "other"
    is_main_meal = target is not None and target.kcal >= 450.0

    if role == IngredientRole.CONDIMENT_FIXED:
        return max(1.0, base * 0.8), max(2.0, base * 1.2)
    if role == IngredientRole.PRODUCE_FIXED:
        return max(15.0, base * 0.7), min(400.0, max(base * 1.4, base + 30.0))
    if role == IngredientRole.FAT_ANCHOR:
        if cat == "oil":
            return max(2.0, min(base, 5.0)), min(25.0, max(base * 2.0, 20.0))
        return max(5.0, base * 0.4), min(60.0, max(base * 2.0, 40.0))
    if role == IngredientRole.CARB_ANCHOR:
        if item.preparation_state == PreparationState.RAW and cat in {
            "rice",
            "pasta",
            "grains",
            "oats",
            "legumes",
        }:
            min_raw = (
                max(45.0, base * 0.55)
                if is_main_meal and cat in {"rice", "pasta", "grains", "oats"}
                else max(20.0, base * 0.45)
            )
            return min_raw, min(180.0, max(base * 2.0, 140.0))
        return max(30.0, base * 0.45), min(400.0, max(base * 2.0, 280.0))
    # PROTEIN_ANCHOR
    if cat in {"poultry", "red_meat", "fish"}:
        if is_main_meal and target is not None and target.protein_g >= 25.0:
            return max(90.0, base * 0.55), min(360.0, max(base * 2.2, 280.0))
        return max(50.0, base * 0.5), min(320.0, max(base * 2.0, 260.0))
    return max(30.0, base * 0.5), min(350.0, max(base * 2.0, 280.0))


def _per_gram_macros(item: ScalableIngredientInput) -> Macros:
    """Compute unrounded per-gram nutritional derivatives for optimization."""
    if item.food is None:
        return Macros(kcal=0.0, protein_g=0.0, carbs_g=0.0, fat_g=0.0, fiber_g=0.0)
    eff_state = item.preparation_state
    if eff_state == PreparationState.UNSPECIFIED:
        eff_state = item.food.preparation_state
    ref_grams = convert_to_reference_grams(
        input_grams=1.0,
        effective_state=eff_state,
        ref_state=item.food.preparation_state,
        yield_factor=item.food.yield_factor,
    )
    factor = max(0.0, ref_grams) / 100.0
    m = item.food.macros_per_100g
    return Macros(
        kcal=m.kcal * factor,
        protein_g=m.protein_g * factor,
        carbs_g=m.carbs_g * factor,
        fat_g=m.fat_g * factor,
        fiber_g=(m.fiber_g * factor) if m.fiber_g is not None else None,
    )


def _compute_item_macros(item: ScalableIngredientInput, grams: float) -> Macros:
    if item.food is None:
        return Macros(kcal=0.0, protein_g=0.0, carbs_g=0.0, fat_g=0.0, fiber_g=0.0)
    eff_state = item.preparation_state
    if eff_state == PreparationState.UNSPECIFIED:
        eff_state = item.food.preparation_state
    ref_grams = convert_to_reference_grams(
        input_grams=grams,
        effective_state=eff_state,
        ref_state=item.food.preparation_state,
        yield_factor=item.food.yield_factor,
    )
    return compute_macros_for_grams(item.food.macros_per_100g, ref_grams)


def _round_kitchen_grams(grams: float, role: IngredientRole) -> float:
    if grams <= 25.0 or role == IngredientRole.FAT_ANCHOR:
        return float(max(1, round(grams)))
    return float(max(5, round(grams / 5.0) * 5))


def _macros_loss(actual: Macros, target: Macros) -> float:
    tk = max(target.kcal, 100.0)
    tp = max(target.protein_g, 10.0)
    tc = max(target.carbs_g, 10.0)
    tf = max(target.fat_g, 5.0)
    return (
        3.0 * ((actual.kcal - target.kcal) / tk) ** 2
        + 2.5 * ((actual.protein_g - target.protein_g) / tp) ** 2
        + 1.5 * ((actual.carbs_g - target.carbs_g) / tc) ** 2
        + 1.5 * ((actual.fat_g - target.fat_g) / tf) ** 2
    )


def scale_meal_ingredients(
    ingredients: list[ScalableIngredientInput],
    target: Macros,
) -> tuple[list[PlannedIngredient], Macros, bool]:
    """Algebraically scale ingredients within culinary bounds to match target macros.

    Returns (planned_ingredients, total_macros, macro_fit_warning).
    """
    if not ingredients:
        return [], Macros(kcal=0.0, protein_g=0.0, carbs_g=0.0, fat_g=0.0, fiber_g=0.0), True

    quantities = [max(1.0, it.proposed_grams) for it in ingredients]
    bounds = [_gram_bounds(it, target=target) for it in ingredients]
    for idx, (lo, hi) in enumerate(bounds):
        quantities[idx] = max(lo, min(hi, quantities[idx]))

    per_gram: list[Macros] = [
        _per_gram_macros(it) for it in ingredients
    ]

    def current_total() -> Macros:
        kcal = sum(per_gram[i].kcal * quantities[i] for i in range(len(ingredients)))
        p = sum(per_gram[i].protein_g * quantities[i] for i in range(len(ingredients)))
        c = sum(per_gram[i].carbs_g * quantities[i] for i in range(len(ingredients)))
        f = sum(per_gram[i].fat_g * quantities[i] for i in range(len(ingredients)))
        return Macros(kcal=kcal, protein_g=p, carbs_g=c, fat_g=f, fiber_g=None)

    # Analytical weighted coordinate-descent updates
    tk = max(target.kcal, 100.0)
    tp = max(target.protein_g, 10.0)
    tc = max(target.carbs_g, 10.0)
    tf = max(target.fat_g, 5.0)
    wk, wp, wc, wf = 3.0 / (tk**2), 2.5 / (tp**2), 1.5 / (tc**2), 1.5 / (tf**2)

    adjustable = [
        i
        for i, it in enumerate(ingredients)
        if it.food is not None
        and it.role
        in (
            IngredientRole.PROTEIN_ANCHOR,
            IngredientRole.CARB_ANCHOR,
            IngredientRole.FAT_ANCHOR,
        )
    ]
    if not adjustable:
        adjustable = [i for i, it in enumerate(ingredients) if it.food is not None]

    for _ in range(30):
        changed = False
        for i in adjustable:
            pg = per_gram[i]
            denom = (
                wk * (pg.kcal**2)
                + wp * (pg.protein_g**2)
                + wc * (pg.carbs_g**2)
                + wf * (pg.fat_g**2)
            )
            if denom <= 1e-12:
                continue
            tot = current_total()
            rest_k = tot.kcal - pg.kcal * quantities[i]
            rest_p = tot.protein_g - pg.protein_g * quantities[i]
            rest_c = tot.carbs_g - pg.carbs_g * quantities[i]
            rest_f = tot.fat_g - pg.fat_g * quantities[i]

            num = (
                wk * pg.kcal * (target.kcal - rest_k)
                + wp * pg.protein_g * (target.protein_g - rest_p)
                + wc * pg.carbs_g * (target.carbs_g - rest_c)
                + wf * pg.fat_g * (target.fat_g - rest_f)
            )
            lo, hi = bounds[i]
            new_q = max(lo, min(hi, num / denom))
            if abs(new_q - quantities[i]) > 0.05:
                quantities[i] = new_q
                changed = True
        if not changed:
            break

    planned: list[PlannedIngredient] = []
    item_macros: list[Macros] = []
    for idx, it in enumerate(ingredients):
        lo, hi = bounds[idx]
        rounded_g = max(lo, min(hi, _round_kitchen_grams(quantities[idx], it.role)))
        macros = _compute_item_macros(it, rounded_g)
        planned.append(
            PlannedIngredient(
                food_id=it.food.food_id if it.food is not None else None,
                name=it.name,
                brand=it.brand or (it.food.brand if it.food else None),
                grams=round(rounded_g, 1),
                preparation_state=it.preparation_state,
                role=it.role,
                course=it.course,
                macros=macros,
            )
        )
        item_macros.append(macros)

    total = sum_macros(item_macros)
    warn = _has_fit_warning(total, target)
    return planned, total, warn


def scale_day_meals(
    slot_skeletons: list[tuple[str, list[ScalableIngredientInput], Macros]],
    daily_target: Macros,
) -> tuple[list[tuple[str, list[PlannedIngredient], Macros, bool]], Macros, bool]:
    """Scale each meal in a day against its slot share, then run a day-level trim pass."""
    if not slot_skeletons:
        return [], Macros(kcal=0.0, protein_g=0.0, carbs_g=0.0, fat_g=0.0, fiber_g=0.0), True

    # Pass 1: independent per-slot optimization
    pass1: list[tuple[str, list[PlannedIngredient], Macros, bool]] = []
    for slot_name, ingredients, slot_target in slot_skeletons:
        scaled_ings, slot_total, slot_warn = scale_meal_ingredients(
            ingredients, slot_target
        )
        pass1.append((slot_name, scaled_ings, slot_total, slot_warn))

    day_total = sum_macros([m for _, _, m, _ in pass1])
    if not _has_fit_warning(day_total, daily_target, tolerance=0.04):
        return pass1, day_total, False

    # Pass 2: distribute remaining day delta across remaining slots proportionally
    delta_kcal = daily_target.kcal - day_total.kcal
    delta_p = daily_target.protein_g - day_total.protein_g
    delta_c = daily_target.carbs_g - day_total.carbs_g
    delta_f = daily_target.fat_g - day_total.fat_g

    weight_sum = sum(max(t.kcal, 100.0) for _, _, t in slot_skeletons)
    pass2: list[tuple[str, list[PlannedIngredient], Macros, bool]] = []
    for idx, (slot_name, ingredients, slot_target) in enumerate(slot_skeletons):
        share = max(slot_target.kcal, 100.0) / weight_sum
        prev_actual = pass1[idx][2]
        adjusted_target = Macros(
            kcal=max(80.0, prev_actual.kcal + delta_kcal * share),
            protein_g=max(2.0, prev_actual.protein_g + delta_p * share),
            carbs_g=max(0.0, prev_actual.carbs_g + delta_c * share),
            fat_g=max(1.0, prev_actual.fat_g + delta_f * share),
            fiber_g=slot_target.fiber_g,
        )
        scaled_ings, slot_total, _ = scale_meal_ingredients(
            ingredients, adjusted_target
        )
        slot_warn = _has_fit_warning(slot_total, adjusted_target)
        pass2.append((slot_name, scaled_ings, slot_total, slot_warn))

    final_total = sum_macros([m for _, _, m, _ in pass2])
    return pass2, final_total, _has_fit_warning(final_total, daily_target)


def _has_fit_warning(
    actual: Macros,
    target: Macros,
    tolerance: float = 0.05,
) -> bool:
    if target.kcal > 0 and abs(actual.kcal - target.kcal) / target.kcal > tolerance:
        return True
    if (
        target.protein_g >= 10.0
        and abs(actual.protein_g - target.protein_g) / target.protein_g > max(tolerance, 0.10)
    ):
        return True
    return False
