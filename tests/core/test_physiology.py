"""Unit tests for BMR, static/wearable TDEE, macro targets, and safety guardrails."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from custom_components.ha_calories_buddy.core.models import (
    ActivityLevel,
    BiometricSnapshot,
    GoalType,
    ManualMacroTarget,
    PersonProfile,
    Sex,
    TargetMode,
    TDEEEngineType,
)
from custom_components.ha_calories_buddy.core.physiology.bmr import calculate_bmr
from custom_components.ha_calories_buddy.core.physiology.targets import (
    calculate_daily_targets,
)
from custom_components.ha_calories_buddy.core.physiology.tdee import (
    calculate_static_tdee,
    calculate_wearable_dynamic_tdee,
)


def test_bmr_katch_mcardle_vs_mifflin() -> None:
    """Verify Katch-McArdle when BF% known and Mifflin-St Jeor fallback otherwise."""
    bmr_km, formula_km = calculate_bmr(
        weight_kg=80.0, height_cm=180.0, age_years=32, sex=Sex.MALE, body_fat_pct=15.0
    )
    # LBM = 68 kg -> 370 + 21.6 * 68 = 1838.8
    assert formula_km == "katch_mcardle"
    assert bmr_km == pytest.approx(1838.8)

    bmr_msj, formula_msj = calculate_bmr(
        weight_kg=80.0, height_cm=180.0, age_years=32, sex=Sex.MALE, body_fat_pct=None
    )
    # 10*80 + 6.25*180 - 5*32 + 5 = 800 + 1125 - 160 + 5 = 1770
    assert formula_msj == "mifflin_st_jeor"
    assert bmr_msj == pytest.approx(1770.0)


def test_wearable_dynamic_tdee_avoids_morning_bug_and_double_counting() -> None:
    """Verify 7-day baseline protects 09:00 morning budget and adds only excess workout burn."""
    today = date(2026, 11, 25)
    snapshots = [
        BiometricSnapshot(
            snapshot_id=f"s_{i}",
            person_id="p1",
            snap_date=today - timedelta(days=i),
            active_kcal=500.0,
            total_kcal=2300.0,
        )
        for i in range(1, 6)
    ]

    # At 09:00 AM Garmin reports only 150 active kcal -> budget uses 7-day baseline (2300), NOT 1950!
    morning = calculate_wearable_dynamic_tdee(
        bmr_kcal=1800.0,
        bmr_formula="mifflin_st_jeor",
        activity_level=ActivityLevel.MODERATE,
        past_snapshots=snapshots,
        today_active_kcal=150.0,
        today_total_kcal=900.0,
        correction_factor=1.0,
    )
    assert morning.fallback_to_static is False
    assert morning.wearable_baseline_kcal == pytest.approx(2300.0)
    assert morning.intraday_delta_kcal == 0.0
    assert morning.tdee_kcal == pytest.approx(2300.0)

    # Heavy workout day: 900 active kcal (+400 above 500 baseline), correction 0.9 -> +360
    workout = calculate_wearable_dynamic_tdee(
        bmr_kcal=1800.0,
        bmr_formula="mifflin_st_jeor",
        activity_level=ActivityLevel.MODERATE,
        past_snapshots=snapshots,
        today_active_kcal=900.0,
        correction_factor=0.9,
    )
    assert workout.wearable_baseline_kcal == pytest.approx(2070.0)
    assert workout.intraday_delta_kcal == pytest.approx(360.0)
    assert workout.tdee_kcal == pytest.approx(2430.0)

    # Fewer than 3 completed past snapshots -> falls back gracefully to static prior
    sparse = calculate_wearable_dynamic_tdee(
        bmr_kcal=1800.0,
        bmr_formula="mifflin_st_jeor",
        activity_level=ActivityLevel.MODERATE,
        past_snapshots=snapshots[:2],
        today_active_kcal=100.0,
    )
    assert sparse.fallback_to_static is True
    assert sparse.tdee_kcal == pytest.approx(1800.0 * 1.55)


def test_physiology_targets_lbm_and_safety_warnings() -> None:
    """Verify high-BF LBM protein anchoring, fat floor, and extreme deficit warnings."""
    tdee = calculate_static_tdee(1750.0, "mifflin_st_jeor", ActivityLevel.LIGHT)
    # 1750 * 1.375 = 2406.3
    profile = PersonProfile(
        person_id="p1",
        config_entry_id="e1",
        display_name="Alex",
        sex=Sex.MALE,
        initial_weight_kg=105.0,
        body_fat_pct=34.0,
        target_mode=TargetMode.PHYSIOLOGY,
        goal_type=GoalType.LOSE_WEIGHT,
        weekly_rate_kg=-0.8,  # -880 kcal/day -> >25% deficit warning
        tdee_engine=TDEEEngineType.STATIC,
    )
    res = calculate_daily_targets(profile, weight_kg=105.0, body_fat_pct=34.0, tdee_result=tdee)
    assert "excessive_deficit" in res.safety_warnings
    # LBM = 105 * 0.66 = 69.3 kg -> 2.2 * 69.3 = 152.5g protein (not 210g!)
    assert res.protein_g == pytest.approx(152.5, abs=0.5)
    assert res.fat_g >= 0.65 * 105.0 - 0.1
    assert res.carbs_g >= 0.0


def test_manual_targets_with_training_override() -> None:
    """Verify Manual / Nutritionist mode supports rest vs training day targets."""
    tdee = calculate_static_tdee(1700.0, "mifflin_st_jeor", ActivityLevel.MODERATE)
    profile = PersonProfile(
        person_id="p2",
        config_entry_id="e2",
        display_name="Giulia",
        sex=Sex.FEMALE,
        target_mode=TargetMode.MANUAL,
        manual_targets=ManualMacroTarget(
            kcal=1800.0,
            protein_g=130.0,
            carbs_g=180.0,
            fat_g=60.0,
            training_kcal=2100.0,
            training_carbs_g=250.0,
        ),
    )
    rest = calculate_daily_targets(profile, 60.0, None, tdee, is_training_day=False)
    train = calculate_daily_targets(profile, 60.0, None, tdee, is_training_day=True)

    assert rest.target_kcal == 1800.0
    assert rest.carbs_g == 180.0
    assert train.target_kcal == 2100.0
    assert train.carbs_g == 250.0
    assert train.protein_g == 130.0
