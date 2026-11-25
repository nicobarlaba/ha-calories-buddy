"""EU/US Atwater energy consistency validator."""

from __future__ import annotations

from dataclasses import dataclass

from custom_components.ha_calories_buddy.core.models import LabelStandard, Macros

DEFAULT_TOLERANCE_PCT = 0.15
DEFAULT_TOLERANCE_KCAL = 25.0


@dataclass(frozen=True, slots=True)
class AtwaterResult:
    """Result of validating declared calories against macronutrient Atwater factors."""

    expected_kcal: float
    declared_kcal: float
    delta_kcal: float
    delta_pct: float
    warning: bool


def check_atwater(
    macros: Macros,
    label_standard: LabelStandard = LabelStandard.EU,
    tolerance_pct: float = DEFAULT_TOLERANCE_PCT,
    tolerance_kcal: float = DEFAULT_TOLERANCE_KCAL,
) -> AtwaterResult:
    """Validate declared kcal against macro-derived expected energy.

    - EU standard: carbohydrates represent available (net) carbs; fiber adds ~2 kcal/g.
    - US standard: carbohydrates represent total carbs including fiber; net carbs = C - Fiber.
    """
    fiber = macros.fiber_g or 0.0
    if label_standard == LabelStandard.US and macros.fiber_g is not None:
        available_carbs = max(0.0, macros.carbs_g - fiber)
    else:
        available_carbs = macros.carbs_g

    fiber_kcal = 2.0 * fiber if macros.fiber_g is not None else 0.0
    expected = (
        4.0 * macros.protein_g
        + 4.0 * available_carbs
        + 9.0 * macros.fat_g
        + fiber_kcal
    )

    declared = macros.kcal
    delta = abs(declared - expected)
    delta_pct = (delta / expected) if expected > 1.0 else (1.0 if delta > 5.0 else 0.0)

    # Skip zero-calorie beverages/condiments where tiny rounding causes high relative %
    if declared < 5.0 and expected < 5.0:
        warning = False
    else:
        threshold = max(tolerance_kcal, tolerance_pct * expected)
        warning = delta > threshold

    return AtwaterResult(
        expected_kcal=round(expected, 1),
        declared_kcal=round(declared, 1),
        delta_kcal=round(delta, 1),
        delta_pct=round(delta_pct, 4),
        warning=warning,
    )
