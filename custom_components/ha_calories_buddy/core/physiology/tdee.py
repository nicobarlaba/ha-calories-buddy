"""Total Daily Energy Expenditure (TDEE) engines without double-counting."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from custom_components.ha_calories_buddy.core.models import (
    ActivityLevel,
    BiometricSnapshot,
    TDEEEngineType,
)

PAL_FACTORS: dict[ActivityLevel, float] = {
    ActivityLevel.SEDENTARY: 1.2,
    ActivityLevel.LIGHT: 1.375,
    ActivityLevel.MODERATE: 1.55,
    ActivityLevel.VERY_ACTIVE: 1.725,
    ActivityLevel.ATHLETE: 1.9,
}


class TDEEResult(BaseModel):
    """Computed TDEE breakdown with explainability metadata."""

    tdee_kcal: float
    bmr_kcal: float
    bmr_formula: str
    engine_used: TDEEEngineType
    wearable_baseline_kcal: float | None = None
    intraday_delta_kcal: float = 0.0
    fallback_to_static: bool = False
    days_used: int = 0


def calculate_static_tdee(
    bmr_kcal: float,
    bmr_formula: str,
    activity_level: ActivityLevel,
) -> TDEEResult:
    """Compute classic static profile TDEE = BMR * PAL."""
    pal = PAL_FACTORS.get(activity_level, 1.55)
    tdee = round(bmr_kcal * pal, 1)
    return TDEEResult(
        tdee_kcal=tdee,
        bmr_kcal=round(bmr_kcal, 1),
        bmr_formula=bmr_formula,
        engine_used=TDEEEngineType.STATIC,
    )


def calculate_wearable_dynamic_tdee(
    bmr_kcal: float,
    bmr_formula: str,
    activity_level: ActivityLevel,
    past_snapshots: Sequence[BiometricSnapshot],
    today_active_kcal: float | None = None,
    today_total_kcal: float | None = None,
    correction_factor: float = 1.0,
) -> TDEEResult:
    """Compute Wearable-Dynamic TDEE from a 7-day completed-day rolling baseline.

    Avoids two classic pitfalls:
    1. Never multiplies wearable expenditure by a PAL factor (anti-double-counting).
    2. Uses the 7-day completed baseline for morning budgets so cumulative sensors
       starting near 0 at 00:00 do not starve the user; only positive surplus above
       the rolling active baseline adjusts today's TDEE upward.
    """
    factor = max(0.3, min(correction_factor, 1.5))

    total_samples = [
        s.total_kcal for s in past_snapshots if s.total_kcal is not None and s.total_kcal > 0
    ]
    active_samples = [
        s.active_kcal for s in past_snapshots if s.active_kcal is not None and s.active_kcal >= 0
    ]

    if len(total_samples) >= 3:
        avg_total = sum(total_samples) / len(total_samples)
        baseline_tdee = avg_total * factor
        avg_active = (
            (sum(active_samples) / len(active_samples))
            if active_samples
            else max(0.0, avg_total - bmr_kcal)
        )
        delta = 0.0
        if today_active_kcal is not None and today_active_kcal > avg_active:
            delta = (today_active_kcal - avg_active) * factor
        elif today_total_kcal is not None and (today_total_kcal * factor) > baseline_tdee:
            delta = (today_total_kcal * factor) - baseline_tdee

        return TDEEResult(
            tdee_kcal=round(baseline_tdee + delta, 1),
            bmr_kcal=round(bmr_kcal, 1),
            bmr_formula=bmr_formula,
            engine_used=TDEEEngineType.WEARABLE_DYNAMIC,
            wearable_baseline_kcal=round(baseline_tdee, 1),
            intraday_delta_kcal=round(delta, 1),
            fallback_to_static=False,
            days_used=len(total_samples),
        )

    if len(active_samples) >= 3:
        avg_active = sum(active_samples) / len(active_samples)
        baseline_tdee = bmr_kcal + (avg_active * factor)
        delta = 0.0
        if today_active_kcal is not None and today_active_kcal > avg_active:
            delta = (today_active_kcal - avg_active) * factor

        return TDEEResult(
            tdee_kcal=round(baseline_tdee + delta, 1),
            bmr_kcal=round(bmr_kcal, 1),
            bmr_formula=bmr_formula,
            engine_used=TDEEEngineType.WEARABLE_DYNAMIC,
            wearable_baseline_kcal=round(baseline_tdee, 1),
            intraday_delta_kcal=round(delta, 1),
            fallback_to_static=False,
            days_used=len(active_samples),
        )

    # Fewer than 3 completed past days -> fall back safely to static prior
    static_prior = calculate_static_tdee(bmr_kcal, bmr_formula, activity_level)
    implied_active = max(0.0, (static_prior.tdee_kcal - bmr_kcal) / factor)
    delta = 0.0
    if today_active_kcal is not None and today_active_kcal > implied_active:
        delta = (today_active_kcal - implied_active) * factor

    return TDEEResult(
        tdee_kcal=round(static_prior.tdee_kcal + delta, 1),
        bmr_kcal=round(bmr_kcal, 1),
        bmr_formula=bmr_formula,
        engine_used=TDEEEngineType.WEARABLE_DYNAMIC,
        wearable_baseline_kcal=static_prior.tdee_kcal,
        intraday_delta_kcal=round(delta, 1),
        fallback_to_static=True,
        days_used=max(len(total_samples), len(active_samples)),
    )
