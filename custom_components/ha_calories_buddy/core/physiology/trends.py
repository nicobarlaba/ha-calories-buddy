"""Trends analytics engine building 7/30/90d series, Expected-vs-Actual, and goal ETA."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from custom_components.ha_calories_buddy.core.models import (
    BiometricSnapshot,
    ExpectedVsActualSummary,
    GoalType,
    Macros,
    PersonProfile,
    TrendPoint,
    TrendsSummary,
    WeeklyAdherenceBucket,
)
from custom_components.ha_calories_buddy.core.physiology.targets import TargetResult
from custom_components.ha_calories_buddy.core.physiology.tdee import TDEEResult, update_ema


def _expected_weekly_rate_kg(profile: PersonProfile) -> float:
    if profile.goal_type == GoalType.LOSE_WEIGHT:
        rate = abs(profile.weekly_rate_kg) if profile.weekly_rate_kg != 0.0 else 0.4
        return round(-rate, 2)
    if profile.goal_type == GoalType.GAIN_MUSCLE:
        rate = abs(profile.weekly_rate_kg) if profile.weekly_rate_kg != 0.0 else 0.25
        return round(rate, 2)
    return 0.0


def build_trends_summary(
    *,
    profile: PersonProfile,
    snapshots: Sequence[BiometricSnapshot],
    daily_macros: dict[date, Macros],
    current_tdee: TDEEResult,
    current_targets: TargetResult,
    today: date,
    days: int = 14,
) -> TrendsSummary:
    """Build contiguous 7/14/30/90d trend points, expected-vs-actual weight rate, and ETA."""
    if days <= 10:
        norm_days = 7
    elif days <= 21:
        norm_days = 14
    elif days <= 45:
        norm_days = 30
    else:
        norm_days = 90

    start_date = today - timedelta(days=norm_days - 1)
    snap_by_date: dict[date, BiometricSnapshot] = {
        s.snap_date: s for s in snapshots if start_date <= s.snap_date <= today
    }

    points: list[TrendPoint] = []
    running_w_ema: float | None = None
    running_bf_ema: float | None = None

    for offset in range(norm_days):
        cur_date = start_date + timedelta(days=offset)
        snap = snap_by_date.get(cur_date)
        macros = daily_macros.get(cur_date)

        w_raw = snap.weight_kg if snap is not None else None
        if snap is not None and snap.weight_ema_kg is not None:
            w_ema: float | None = round(snap.weight_ema_kg, 2)
            running_w_ema = w_ema
        elif w_raw is not None:
            w_ema = update_ema(running_w_ema, w_raw, alpha=0.12)
            running_w_ema = w_ema
        else:
            w_ema = None

        bf_raw = snap.body_fat_pct if snap is not None else None
        if snap is not None and snap.body_fat_ema_pct is not None:
            bf_ema: float | None = round(snap.body_fat_ema_pct, 2)
            running_bf_ema = bf_ema
        elif bf_raw is not None:
            bf_ema = update_ema(running_bf_ema, bf_raw, alpha=0.10)
            running_bf_ema = bf_ema
        else:
            bf_ema = None

        weight_for_lean = w_ema if w_ema is not None else w_raw
        bf_for_lean = bf_ema if bf_ema is not None else bf_raw
        lean_mass: float | None = (
            round(weight_for_lean * (1.0 - bf_for_lean / 100.0), 2)
            if weight_for_lean is not None and bf_for_lean is not None
            else None
        )

        kcal_consumed = round(macros.kcal, 1) if macros is not None else 0.0
        is_logged = bool(macros is not None and macros.kcal > 0.0)
        if snap is not None and snap.target_kcal is not None:
            kcal_target: float | None = round(snap.target_kcal, 1)
        elif cur_date == today:
            kcal_target = round(current_targets.target_kcal, 1)
        else:
            kcal_target = round(current_targets.target_kcal, 1)

        if snap is not None and snap.adaptive_tdee_kcal is not None:
            point_tdee: float | None = round(snap.adaptive_tdee_kcal, 1)
        elif snap is not None and snap.computed_tdee is not None:
            point_tdee = round(snap.computed_tdee, 1)
        elif cur_date == today:
            point_tdee = round(current_tdee.tdee_kcal, 1)
        else:
            point_tdee = None

        adherence_pct = (
            round((kcal_consumed / kcal_target) * 100.0, 1)
            if is_logged and kcal_target and kcal_target > 0
            else None
        )

        points.append(
            TrendPoint(
                point_date=cur_date,
                kcal_consumed=kcal_consumed,
                kcal_target=kcal_target,
                protein_g=round(macros.protein_g, 1) if macros else 0.0,
                carbs_g=round(macros.carbs_g, 1) if macros else 0.0,
                fat_g=round(macros.fat_g, 1) if macros else 0.0,
                fiber_g=round(macros.fiber_g or 0.0, 1) if macros else 0.0,
                weight_kg=round(w_raw, 2) if w_raw is not None else None,
                weight_ema_kg=w_ema,
                body_fat_pct=round(bf_raw, 1) if bf_raw is not None else None,
                body_fat_ema_pct=bf_ema,
                lean_mass_kg=lean_mass,
                tdee_kcal=point_tdee,
                steps=snap.steps if snap is not None else None,
                adherence_pct=adherence_pct,
                is_logged=is_logged,
            )
        )

    logged_points = [p for p in points if p.is_logged]
    logged_days = len(logged_points)
    avg_kcal = (
        round(sum(p.kcal_consumed for p in logged_points) / logged_days, 1)
        if logged_days
        else 0.0
    )
    target_samples = [
        p.kcal_target for p in logged_points if p.kcal_target and p.kcal_target > 0
    ]
    avg_target_kcal = (
        round(sum(target_samples) / len(target_samples), 1)
        if target_samples
        else round(current_targets.target_kcal, 1)
    )
    kcal_adherence_pct = (
        round((avg_kcal / avg_target_kcal) * 100.0, 1)
        if logged_days and avg_target_kcal > 0
        else 0.0
    )

    avg_protein = (
        round(sum(p.protein_g for p in logged_points) / logged_days, 1)
        if logged_days
        else 0.0
    )
    avg_carbs = (
        round(sum(p.carbs_g for p in logged_points) / logged_days, 1)
        if logged_days
        else 0.0
    )
    avg_fat = (
        round(sum(p.fat_g for p in logged_points) / logged_days, 1)
        if logged_days
        else 0.0
    )
    avg_fiber = (
        round(sum(p.fiber_g for p in logged_points) / logged_days, 1)
        if logged_days
        else 0.0
    )

    ema_points = [p for p in points if p.weight_ema_kg is not None]
    bf_ema_points = [p for p in points if p.body_fat_ema_pct is not None]
    current_w_ema = ema_points[-1].weight_ema_kg if ema_points else None
    current_bf_ema = bf_ema_points[-1].body_fat_ema_pct if bf_ema_points else None

    expected_rate = _expected_weekly_rate_kg(profile)
    actual_rate: float | None = None
    status = "insufficient_data"
    if len(ema_points) >= 2:
        span_days = (ema_points[-1].point_date - ema_points[0].point_date).days
        if span_days >= 6:
            assert ema_points[-1].weight_ema_kg is not None
            assert ema_points[0].weight_ema_kg is not None
            actual_rate = round(
                (ema_points[-1].weight_ema_kg - ema_points[0].weight_ema_kg)
                / (span_days / 7.0),
                2,
            )
            diff = actual_rate - expected_rate
            if abs(diff) <= 0.15:
                status = "on_track"
            elif profile.goal_type == GoalType.LOSE_WEIGHT:
                status = (
                    "faster_than_expected" if diff < -0.15 else "slower_than_expected"
                )
            elif profile.goal_type == GoalType.GAIN_MUSCLE:
                status = (
                    "faster_than_expected" if diff > 0.15 else "slower_than_expected"
                )
            else:
                status = "drifting"

    eta_weeks: float | None = None
    eta_date: date | None = None
    if (
        profile.target_weight_kg is not None
        and current_w_ema is not None
        and actual_rate is not None
    ):
        remaining_kg = profile.target_weight_kg - current_w_ema
        if abs(remaining_kg) <= 0.15:
            eta_weeks = 0.0
            eta_date = today
        elif (
            (remaining_kg < 0 and actual_rate < -0.02)
            or (remaining_kg > 0 and actual_rate > 0.02)
        ):
            raw_weeks = abs(remaining_kg / actual_rate)
            if raw_weeks <= 104.0:
                eta_weeks = round(raw_weeks, 1)
                eta_date = today + timedelta(days=int(round(raw_weeks * 7.0)))

    weekly_buckets: list[WeeklyAdherenceBucket] = []
    for idx in range(0, len(points), 7):
        chunk = points[idx : idx + 7]
        c_logged = [p for p in chunk if p.is_logged]
        c_avg_kcal = (
            round(sum(p.kcal_consumed for p in c_logged) / len(c_logged), 1)
            if c_logged
            else 0.0
        )
        c_targets = [p.kcal_target for p in c_logged if p.kcal_target and p.kcal_target > 0]
        c_avg_tgt = (
            round(sum(c_targets) / len(c_targets), 1)
            if c_targets
            else round(current_targets.target_kcal, 1)
        )
        c_adh = (
            round((c_avg_kcal / c_avg_tgt) * 100.0, 1)
            if c_logged and c_avg_tgt > 0
            else 0.0
        )
        weekly_buckets.append(
            WeeklyAdherenceBucket(
                week_start=chunk[0].point_date,
                week_end=chunk[-1].point_date,
                logged_days=len(c_logged),
                total_days=len(chunk),
                avg_kcal=c_avg_kcal,
                avg_target_kcal=c_avg_tgt,
                adherence_pct=c_adh,
            )
        )

    return TrendsSummary(
        person_id=profile.person_id,
        days=norm_days,
        start_date=start_date,
        end_date=today,
        points=points,
        expected_vs_actual=ExpectedVsActualSummary(
            expected_weekly_rate_kg=expected_rate,
            actual_weekly_rate_kg=actual_rate,
            status=status,
            current_weight_ema_kg=current_w_ema,
            target_weight_kg=profile.target_weight_kg,
            current_body_fat_ema_pct=current_bf_ema,
            target_body_fat_pct=profile.target_body_fat_pct,
            eta_date=eta_date,
            eta_weeks=eta_weeks,
        ),
        logged_days=logged_days,
        total_days=norm_days,
        logging_adherence_pct=round((logged_days / norm_days) * 100.0, 1),
        avg_kcal=avg_kcal,
        avg_target_kcal=avg_target_kcal,
        kcal_adherence_pct=kcal_adherence_pct,
        avg_protein_g=avg_protein,
        avg_carbs_g=avg_carbs,
        avg_fat_g=avg_fat,
        avg_fiber_g=avg_fiber,
        target_protein_g=round(current_targets.protein_g, 1),
        target_carbs_g=round(current_targets.carbs_g, 1),
        target_fat_g=round(current_targets.fat_g, 1),
        target_fiber_g=round(current_targets.fiber_g, 1),
        engine_used=current_tdee.engine_used.value,
        adaptive_confidence=current_tdee.adaptive_confidence,
        wearable_multiplier=current_tdee.wearable_multiplier,
        adaptive_tdee_kcal=current_tdee.adaptive_tdee_kcal,
        two_compartment_used=current_tdee.two_compartment_used,
        fallback_engine=current_tdee.fallback_engine,
        weekly_adherence=weekly_buckets,
    )
