"""Daily calorie and macronutrient target calculation with safety guardrails."""

from __future__ import annotations

from pydantic import BaseModel, Field

from custom_components.ha_calories_buddy.core.models import (
    GoalType,
    PersonProfile,
    Sex,
    TargetMode,
)
from custom_components.ha_calories_buddy.core.physiology.tdee import TDEEResult


class TargetResult(BaseModel):
    """Computed daily targets and advisory physiological safety warnings."""

    target_kcal: float
    protein_g: float
    carbs_g: float
    fat_g: float
    mode: TargetMode
    safety_warnings: list[str] = Field(default_factory=list)


def calculate_daily_targets(
    profile: PersonProfile,
    weight_kg: float,
    body_fat_pct: float | None,
    tdee_result: TDEEResult,
    is_training_day: bool = False,
) -> TargetResult:
    """Compute daily kcal and P/C/F targets for Physiology or Manual mode."""
    if profile.target_mode == TargetMode.MANUAL:
        return _manual_targets(profile, tdee_result, is_training_day)

    return _physiology_targets(profile, weight_kg, body_fat_pct, tdee_result)


def _manual_targets(
    profile: PersonProfile,
    tdee_result: TDEEResult,
    is_training_day: bool,
) -> TargetResult:
    manual = profile.manual_targets
    if manual is None:
        # Defensive fallback to balanced maintenance if manual targets omitted
        kcal = round(tdee_result.tdee_kcal, 1)
        protein = round((0.25 * kcal) / 4.0, 1)
        fat = round((0.25 * kcal) / 9.0, 1)
        carbs = round((0.50 * kcal) / 4.0, 1)
        return TargetResult(
            target_kcal=kcal,
            protein_g=protein,
            carbs_g=carbs,
            fat_g=fat,
            mode=TargetMode.MANUAL,
        )

    if is_training_day and manual.training_kcal is not None:
        kcal = manual.training_kcal
        protein = (
            manual.training_protein_g
            if manual.training_protein_g is not None
            else manual.protein_g
        )
        carbs = (
            manual.training_carbs_g
            if manual.training_carbs_g is not None
            else manual.carbs_g
        )
        fat = (
            manual.training_fat_g
            if manual.training_fat_g is not None
            else manual.fat_g
        )
    else:
        kcal = manual.kcal
        protein = manual.protein_g
        carbs = manual.carbs_g
        fat = manual.fat_g

    return TargetResult(
        target_kcal=round(kcal, 1),
        protein_g=round(protein, 1),
        carbs_g=round(carbs, 1),
        fat_g=round(fat, 1),
        mode=TargetMode.MANUAL,
    )


def _physiology_targets(
    profile: PersonProfile,
    weight_kg: float,
    body_fat_pct: float | None,
    tdee_result: TDEEResult,
) -> TargetResult:
    warnings: list[str] = []

    if profile.goal_type == GoalType.MAINTAIN:
        daily_delta = 0.0
    elif profile.goal_type == GoalType.RECOMPOSITION:
        daily_delta = -100.0
    elif profile.goal_type == GoalType.LOSE_WEIGHT:
        rate = abs(profile.weekly_rate_kg) if profile.weekly_rate_kg != 0.0 else 0.4
        daily_delta = -(rate * 7700.0) / 7.0
    else:  # GAIN_MUSCLE
        rate = abs(profile.weekly_rate_kg) if profile.weekly_rate_kg != 0.0 else 0.25
        daily_delta = (rate * 7700.0) / 7.0

    raw_target = max(0.0, tdee_result.tdee_kcal + daily_delta)

    if daily_delta < -0.25 * tdee_result.tdee_kcal:
        warnings.append("excessive_deficit")

    conservative_floor = 1200.0 if profile.sex == Sex.FEMALE else 1500.0
    if raw_target < conservative_floor or raw_target < (0.85 * tdee_result.bmr_kcal):
        warnings.append("low_calorie_floor")

    is_deficit_or_recomp = profile.goal_type in (
        GoalType.LOSE_WEIGHT,
        GoalType.RECOMPOSITION,
    )
    high_bf_threshold = 25.0 if profile.sex == Sex.MALE else 32.0

    if body_fat_pct is not None and 3.0 <= body_fat_pct <= 65.0 and body_fat_pct > high_bf_threshold:
        lbm_kg = weight_kg * (1.0 - body_fat_pct / 100.0)
        protein_g = lbm_kg * (2.2 if is_deficit_or_recomp else 2.0)
    else:
        protein_g = weight_kg * (2.0 if is_deficit_or_recomp else 1.8)

    # Cap protein calories only if target calories are extremely low
    max_protein_g = (0.50 * raw_target) / 4.0 if raw_target > 0 else 0.0
    if protein_g > max_protein_g and max_protein_g > 0:
        protein_g = max_protein_g

    desired_fat_g = max(0.65 * weight_kg, (0.25 * raw_target) / 9.0)
    remaining_after_protein = max(0.0, raw_target - (4.0 * protein_g))
    max_fat_g = remaining_after_protein / 9.0
    if desired_fat_g > max_fat_g:
        fat_g = max_fat_g
        warnings.append("fat_floor_strained")
    else:
        fat_g = desired_fat_g

    carbs_kcal = max(0.0, raw_target - (4.0 * protein_g) - (9.0 * fat_g))
    carbs_g = carbs_kcal / 4.0

    return TargetResult(
        target_kcal=round(raw_target, 1),
        protein_g=round(protein_g, 1),
        carbs_g=round(carbs_g, 1),
        fat_g=round(fat_g, 1),
        mode=TargetMode.PHYSIOLOGY,
        safety_warnings=warnings,
    )
