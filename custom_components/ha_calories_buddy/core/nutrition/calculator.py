"""Deterministic macronutrient scaling and summation."""

from __future__ import annotations

from collections.abc import Sequence

from custom_components.ha_calories_buddy.core.models import Macros


def compute_macros_for_grams(macros_per_100g: Macros, ref_grams: float) -> Macros:
    """Compute exact calories and macros for a weight in reference state."""
    factor = max(0.0, ref_grams) / 100.0
    fiber = (
        round(macros_per_100g.fiber_g * factor, 1)
        if macros_per_100g.fiber_g is not None
        else None
    )
    return Macros(
        kcal=round(macros_per_100g.kcal * factor, 1),
        protein_g=round(macros_per_100g.protein_g * factor, 1),
        carbs_g=round(macros_per_100g.carbs_g * factor, 1),
        fat_g=round(macros_per_100g.fat_g * factor, 1),
        fiber_g=fiber,
    )


def sum_macros(items: Sequence[Macros]) -> Macros:
    """Deterministically sum a sequence of Macros."""
    kcal = sum(m.kcal for m in items)
    protein = sum(m.protein_g for m in items)
    carbs = sum(m.carbs_g for m in items)
    fat = sum(m.fat_g for m in items)

    fiber_values = [m.fiber_g for m in items if m.fiber_g is not None]
    fiber = round(sum(fiber_values), 1) if fiber_values else None

    return Macros(
        kcal=round(kcal, 1),
        protein_g=round(protein, 1),
        carbs_g=round(carbs, 1),
        fat_g=round(fat, 1),
        fiber_g=fiber,
    )
