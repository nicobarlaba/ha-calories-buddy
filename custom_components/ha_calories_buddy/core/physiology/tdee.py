"""Total Daily Energy Expenditure (TDEE) engines without double-counting."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from pydantic import BaseModel

from custom_components.ha_calories_buddy.core.models import (
    ActivityLevel,
    AdaptiveState,
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
    adaptive_tdee_kcal: float | None = None
    wearable_multiplier: float | None = None
    adaptive_confidence: str | None = None
    adaptive_delta_kcal: float | None = None
    adaptive_window_days: int = 0
    logged_days_in_window: int = 0
    weigh_ins_in_window: int = 0
    two_compartment_used: bool = False
    fallback_engine: str | None = None


def update_ema(prev_ema: float | None, value: float, alpha: float = 0.12) -> float:
    """Compute next exponential moving average point, initializing cleanly on first value."""
    if prev_ema is None:
        return round(float(value), 2)
    return round(alpha * float(value) + (1.0 - alpha) * float(prev_ema), 2)


def recompute_biometric_emas(
    snapshots: Sequence[BiometricSnapshot],
) -> list[BiometricSnapshot]:
    """Recompute chronological weight & body-fat EMAs across ordered snapshots.

    Returns only the snapshots whose EMA fields need to be persisted/updated.
    """
    ordered = sorted(snapshots, key=lambda s: s.snap_date)
    w_ema: float | None = None
    bf_ema: float | None = None
    changed: list[BiometricSnapshot] = []

    for snap in ordered:
        new_w_ema = snap.weight_ema_kg
        if snap.weight_kg is not None:
            w_ema = update_ema(w_ema, snap.weight_kg, alpha=0.12)
            new_w_ema = w_ema

        new_bf_ema = snap.body_fat_ema_pct
        if snap.body_fat_pct is not None:
            bf_ema = update_ema(bf_ema, snap.body_fat_pct, alpha=0.10)
            new_bf_ema = bf_ema

        if new_w_ema != snap.weight_ema_kg or new_bf_ema != snap.body_fat_ema_pct:
            changed.append(
                snap.model_copy(
                    update={
                        "weight_ema_kg": new_w_ema,
                        "body_fat_ema_pct": new_bf_ema,
                    }
                )
            )
    return changed


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


def calculate_adaptive_tdee(
    *,
    bmr_kcal: float,
    bmr_formula: str,
    activity_level: ActivityLevel,
    past_snapshots: Sequence[BiometricSnapshot],
    daily_intake_by_date: dict[date, float],
    today_active_kcal: float | None = None,
    today_total_kcal: float | None = None,
    base_correction_factor: float = 1.0,
    prev_adaptive_state: AdaptiveState | None = None,
    today: date | None = None,
) -> TDEEResult:
    """Closed-loop hybrid Adaptive TDEE calibrating energy balance from intake + weight/BF EMA.

    Falls back to Wearable-Dynamic (if >= 3 wearable days exist) or Static BMR*PAL during
    bootstrap (< 14 days span or < 4 weigh-ins) or low food-logging adherence (< 70%).
    """
    ref_today = today or date.today()
    win_start = ref_today - timedelta(days=28)
    win_end = ref_today - timedelta(days=1)

    sorted_snaps = sorted(
        (s for s in past_snapshots if win_start <= s.snap_date <= win_end),
        key=lambda s: s.snap_date,
    )
    snaps_7d = [s for s in sorted_snaps if s.snap_date >= ref_today - timedelta(days=7)]
    wearable_prior = calculate_wearable_dynamic_tdee(
        bmr_kcal=bmr_kcal,
        bmr_formula=bmr_formula,
        activity_level=activity_level,
        past_snapshots=snaps_7d,
        today_active_kcal=today_active_kcal,
        today_total_kcal=today_total_kcal,
        correction_factor=(
            prev_adaptive_state.wearable_multiplier
            if prev_adaptive_state is not None
            else base_correction_factor
        ),
    )
    static_prior = calculate_static_tdee(bmr_kcal, bmr_formula, activity_level)
    has_wearable = not wearable_prior.fallback_to_static
    prior_baseline = (
        wearable_prior.wearable_baseline_kcal
        if has_wearable and wearable_prior.wearable_baseline_kcal is not None
        else static_prior.tdee_kcal
    )

    logged_entries = sorted(
        (d, kcal)
        for d, kcal in daily_intake_by_date.items()
        if win_start <= d <= win_end and kcal >= 300.0
    )
    weigh_snaps = [s for s in sorted_snaps if s.weight_kg is not None]
    logged_days = len(logged_entries)
    weigh_ins = len(weigh_snaps)

    earliest_candidates = [
        *(d for d, _ in logged_entries),
        *(s.snap_date for s in weigh_snaps),
    ]
    if earliest_candidates:
        earliest_d = min(earliest_candidates)
        window_days = min(28, max(0, (win_end - earliest_d).days + 1))
    else:
        window_days = 0

    adherence = (logged_days / window_days) if window_days > 0 else 0.0
    if window_days < 14 or weigh_ins < 4 or adherence < 0.70:
        confidence = (
            "bootstrapping"
            if (window_days < 14 or weigh_ins < 4)
            else "low_adherence"
        )
        fallback_eng = "wearable_dynamic" if has_wearable else "static"
        active_tdee = (
            wearable_prior.tdee_kcal if has_wearable else static_prior.tdee_kcal
        )
        prev_mult = (
            prev_adaptive_state.wearable_multiplier
            if prev_adaptive_state is not None
            else max(0.65, min(1.35, base_correction_factor))
        )
        return TDEEResult(
            tdee_kcal=round(active_tdee, 1),
            bmr_kcal=round(bmr_kcal, 1),
            bmr_formula=bmr_formula,
            engine_used=TDEEEngineType.ADAPTIVE,
            wearable_baseline_kcal=(
                wearable_prior.wearable_baseline_kcal if has_wearable else None
            ),
            intraday_delta_kcal=(
                wearable_prior.intraday_delta_kcal if has_wearable else 0.0
            ),
            fallback_to_static=not has_wearable,
            days_used=logged_days,
            adaptive_tdee_kcal=(
                prev_adaptive_state.adaptive_tdee_kcal
                if prev_adaptive_state is not None
                else None
            ),
            wearable_multiplier=round(prev_mult, 3) if has_wearable else None,
            adaptive_confidence=confidence,
            adaptive_delta_kcal=None,
            adaptive_window_days=window_days,
            logged_days_in_window=logged_days,
            weigh_ins_in_window=weigh_ins,
            two_compartment_used=False,
            fallback_engine=fallback_eng,
        )

    # Compute weight EMA trajectory cleanly across sorted weigh-ins
    if all(s.weight_ema_kg is not None for s in weigh_snaps):
        w_emas = [float(s.weight_ema_kg) for s in weigh_snaps if s.weight_ema_kg is not None]
    else:
        w_emas = []
        cur_w_ema: float | None = None
        for s in weigh_snaps:
            assert s.weight_kg is not None
            cur_w_ema = update_ema(cur_w_ema, s.weight_kg, alpha=0.12)
            w_emas.append(cur_w_ema)

    w_start = w_emas[0]
    w_end = w_emas[-1]
    span_days = max(7, (weigh_snaps[-1].snap_date - weigh_snaps[0].snap_date).days)
    delta_w = w_end - w_start

    # Optional two-compartment Fat Mass / Lean Mass model with BIA anti-noise guardrail
    bf_snaps = [s for s in sorted_snaps if s.body_fat_pct is not None and s.weight_kg is not None]
    two_compartment_used = False
    stored_kcal = delta_w * 7700.0

    if len(bf_snaps) >= 4 and (bf_snaps[-1].snap_date - bf_snaps[0].snap_date).days >= 7:
        if all(s.body_fat_ema_pct is not None for s in bf_snaps):
            bf_emas = [
                float(s.body_fat_ema_pct)
                for s in bf_snaps
                if s.body_fat_ema_pct is not None
            ]
        else:
            bf_emas = []
            cur_bf_ema: float | None = None
            for s in bf_snaps:
                assert s.body_fat_pct is not None
                cur_bf_ema = update_ema(cur_bf_ema, s.body_fat_pct, alpha=0.10)
                bf_emas.append(cur_bf_ema)

        bf_start = bf_emas[0]
        bf_end = bf_emas[-1]
        fm_start = w_start * (bf_start / 100.0)
        lm_start = w_start - fm_start
        fm_end = w_end * (bf_end / 100.0)
        lm_end = w_end - fm_end
        delta_fm = fm_end - fm_start
        delta_lm = lm_end - lm_start

        if abs(bf_end - bf_start) <= 2.5 and (abs(delta_fm) + abs(delta_lm)) <= max(
            2.5, abs(delta_w) * 3.0
        ):
            stored_kcal = (delta_fm * 9400.0) + (delta_lm * 1800.0)
            two_compartment_used = True

    avg_intake = sum(kcal for _, kcal in logged_entries) / len(logged_entries)
    raw_observed_tdee = avg_intake - (stored_kcal / span_days)
    min_tdee = 0.85 * bmr_kcal
    max_tdee = 3.2 * bmr_kcal
    raw_observed_tdee = max(min_tdee, min(max_tdee, raw_observed_tdee))

    anchor = (
        prev_adaptive_state.adaptive_tdee_kcal
        if prev_adaptive_state is not None
        and prev_adaptive_state.adaptive_tdee_kcal is not None
        else prior_baseline
    )
    if (
        prev_adaptive_state is not None
        and prev_adaptive_state.last_calibrated_date is not None
    ):
        elapsed_days = (ref_today - prev_adaptive_state.last_calibrated_date).days
    else:
        elapsed_days = 7

    if (
        elapsed_days <= 0
        and prev_adaptive_state is not None
        and prev_adaptive_state.adaptive_tdee_kcal is not None
    ):
        calibrated_tdee = round(prev_adaptive_state.adaptive_tdee_kcal, 1)
    else:
        max_step = max(10.7, min(150.0, 75.0 * (elapsed_days / 7.0)))
        target_delta = (raw_observed_tdee - anchor) * 0.40
        clamped_delta = max(-max_step, min(max_step, target_delta))
        calibrated_tdee = round(
            max(min_tdee, min(max_tdee, anchor + clamped_delta)), 1
        )

    # Auto-calibrate wearable multiplier [0.65, 1.35] when wearable samples exist
    win_totals = [
        s.total_kcal
        for s in sorted_snaps
        if s.total_kcal is not None and s.total_kcal > 0
    ]
    win_actives = [
        s.active_kcal
        for s in sorted_snaps
        if s.active_kcal is not None and s.active_kcal >= 0
    ]
    wearable_multiplier: float | None = None
    intraday_delta = 0.0

    if len(win_totals) >= 3 or len(win_actives) >= 3:
        prev_mult = (
            prev_adaptive_state.wearable_multiplier
            if prev_adaptive_state is not None
            else base_correction_factor
        )
        prev_mult = max(0.65, min(1.35, prev_mult))
        avg_raw_total = (
            (sum(win_totals) / len(win_totals))
            if len(win_totals) >= 3
            else (bmr_kcal + (sum(win_actives) / len(win_actives)))
        )
        avg_active = (
            (sum(win_actives) / len(win_actives))
            if win_actives
            else max(0.0, avg_raw_total - bmr_kcal)
        )
        if avg_raw_total > 0.0:
            implied_mult = calibrated_tdee / avg_raw_total
            blended_mult = (0.40 * implied_mult) + (0.60 * prev_mult)
            wearable_multiplier = round(max(0.65, min(1.35, blended_mult)), 3)
        else:
            wearable_multiplier = round(prev_mult, 3)

        if today_active_kcal is not None and today_active_kcal > avg_active:
            intraday_delta = (today_active_kcal - avg_active) * wearable_multiplier
        elif today_total_kcal is not None and (today_total_kcal * wearable_multiplier) > calibrated_tdee:
            intraday_delta = (today_total_kcal * wearable_multiplier) - calibrated_tdee

    confidence = (
        "high"
        if (window_days >= 21 and adherence >= 0.85 and weigh_ins >= 8)
        else "medium"
    )
    return TDEEResult(
        tdee_kcal=round(calibrated_tdee + intraday_delta, 1),
        bmr_kcal=round(bmr_kcal, 1),
        bmr_formula=bmr_formula,
        engine_used=TDEEEngineType.ADAPTIVE,
        wearable_baseline_kcal=calibrated_tdee,
        intraday_delta_kcal=round(intraday_delta, 1),
        fallback_to_static=False,
        days_used=logged_days,
        adaptive_tdee_kcal=calibrated_tdee,
        wearable_multiplier=wearable_multiplier,
        adaptive_confidence=confidence,
        adaptive_delta_kcal=round(calibrated_tdee - prior_baseline, 1),
        adaptive_window_days=window_days,
        logged_days_in_window=logged_days,
        weigh_ins_in_window=weigh_ins,
        two_compartment_used=two_compartment_used,
        fallback_engine=None,
    )
