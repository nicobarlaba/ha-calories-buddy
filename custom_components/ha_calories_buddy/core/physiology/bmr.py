"""Basal Metabolic Rate (BMR) physiological equations."""

from __future__ import annotations

from custom_components.ha_calories_buddy.core.models import Sex


def calculate_bmr(
    weight_kg: float,
    height_cm: float,
    age_years: int,
    sex: Sex,
    body_fat_pct: float | None = None,
) -> tuple[float, str]:
    """Compute BMR (kcal/day) and formula identifier.

    Uses Katch-McArdle when valid body fat percentage is known, falling back
    to Mifflin-St Jeor otherwise.
    """
    if body_fat_pct is not None and 3.0 <= body_fat_pct <= 65.0:
        lbm_kg = weight_kg * (1.0 - body_fat_pct / 100.0)
        bmr = 370.0 + 21.6 * lbm_kg
        return round(bmr, 1), "katch_mcardle"

    clamped_age = max(10, min(age_years, 110))
    sex_offset = 5.0 if sex == Sex.MALE else -161.0
    bmr = (10.0 * weight_kg) + (6.25 * height_cm) - (5.0 * clamped_age) + sex_offset
    return round(max(bmr, 600.0), 1), "mifflin_st_jeor"
