"""Non-destructive backfill of biometric history from Home Assistant Recorder."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.ha_calories_buddy.core.logging import get_logger, log_event
from custom_components.ha_calories_buddy.core.models import (
    BiometricSnapshot,
    PersonProfile,
    TrendsSummary,
)
from custom_components.ha_calories_buddy.core.physiology.tdee import (
    recompute_biometric_emas,
)
from custom_components.ha_calories_buddy.core.physiology.trends import (
    build_trends_summary,
)
from custom_components.ha_calories_buddy.runtime import DomainRuntime, PersonRuntimeData

_LOGGER = get_logger(__name__)

_HISTORY_LOOKBACK_DAYS = 10


def normalize_trends_days(raw_days: int) -> int:
    """Normalize requested trend span into 7, 14, 30, or 90 days (default 14)."""
    if raw_days <= 10:
        return 7
    if raw_days <= 21:
        return 14
    if raw_days <= 45:
        return 30
    return 90


def _convert_weight_value(val: float, unit: str | None = None) -> float | None:
    u = (unit or "").strip().lower()
    kg = val * 0.45359237 if u in ("lb", "lbs") else val
    return round(kg, 2) if 20.0 <= kg <= 350.0 else None


def _convert_body_fat_value(val: float, _unit: str | None = None) -> float | None:
    return round(val, 1) if 3.0 <= val <= 65.0 else None


def _convert_energy_value(val: float, unit: str | None = None) -> float | None:
    u = (unit or "").strip().lower()
    if u in ("kj", "kilojoule", "kilojoules"):
        kcal = val / 4.184
    elif u in ("mj",):
        kcal = (val * 1000.0) / 4.184
    elif u in ("wh",):
        kcal = val * 0.860421
    elif u in ("kwh",):
        kcal = val * 860.421
    else:
        kcal = val
    return round(kcal, 1) if 0.0 <= kcal <= 15000.0 else None


def _convert_steps_value(val: float, _unit: str | None = None) -> int | None:
    iv = int(round(val))
    return iv if 0 <= iv <= 200000 else None


def _unit_for_entity(hass: HomeAssistant, entity_id: str | None) -> str | None:
    if not entity_id:
        return None
    st = hass.states.get(entity_id)
    if st is None:
        return None
    raw_u = st.attributes.get("unit_of_measurement")
    return str(raw_u) if raw_u is not None else None


def _to_local_date(raw_ts: Any) -> date | None:
    """Convert numeric seconds/ms timestamp or ISO timestamp string into HA local date."""
    if raw_ts is None:
        return None
    if isinstance(raw_ts, (int, float)):
        ts = float(raw_ts)
        if ts > 1e12:  # Defensive millisecond handling
            ts /= 1000.0
        return dt_util.as_local(datetime.fromtimestamp(ts, tz=UTC)).date()
    if isinstance(raw_ts, datetime):
        return dt_util.as_local(
            raw_ts if raw_ts.tzinfo else raw_ts.replace(tzinfo=UTC)
        ).date()
    if isinstance(raw_ts, str):
        parsed = dt_util.parse_datetime(raw_ts)
        if parsed is not None:
            return dt_util.as_local(
                parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
            ).date()
    return None


def _safe_float(raw: Any) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        s = str(raw).strip().lower()
        if s in ("", "unknown", "unavailable", "none", "nan"):
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


def _extract_measurement_series(
    rows: list[Any],
    *,
    start_d: date,
    end_d: date,
    unit: str | None,
    converter: Any,
) -> dict[date, Any]:
    """Extract real weigh-in/BF events from daily statistics, skipping Garmin carry-forward plateaus."""
    sorted_rows = sorted(rows, key=lambda r: float(r.get("start") or 0.0))
    out: dict[date, Any] = {}
    prev_val: float | None = None

    for row in sorted_rows:
        row_date = _to_local_date(row.get("start"))
        if row_date is None or row_date > end_d:
            continue

        mn = _safe_float(row.get("min"))
        mx = _safe_float(row.get("max"))
        mean = _safe_float(row.get("mean"))
        st = _safe_float(row.get("state"))

        chosen: float | None = None
        if mn is not None and mx is not None and abs(mx - mn) > 0.005:
            if prev_val is not None:
                chosen = mx if abs(mx - prev_val) >= abs(mn - prev_val) else mn
            else:
                chosen = mean if mean is not None else mx
        else:
            raw_cand = mn if mn is not None else (mean if mean is not None else st)
            if raw_cand is not None:
                if prev_val is None or abs(raw_cand - prev_val) > 0.005:
                    chosen = raw_cand

        if chosen is None:
            continue
        prev_val = chosen
        if row_date < start_d:
            continue
        converted = converter(chosen, unit)
        if converted is not None:
            out[row_date] = converted
    return out


def _extract_daily_total_series(
    rows: list[Any],
    *,
    start_d: date,
    end_d: date,
    unit: str | None,
    converter: Any,
) -> dict[date, Any]:
    """Extract daily totals from statistics, preferring `change` for daily-resetting sensors."""
    out: dict[date, Any] = {}
    for row in rows:
        row_date = _to_local_date(row.get("start"))
        if row_date is None or row_date < start_d or row_date > end_d:
            continue

        chg = _safe_float(row.get("change"))
        mx = _safe_float(row.get("max"))
        st = _safe_float(row.get("state"))
        mean = _safe_float(row.get("mean"))

        cand: float | None = None
        if chg is not None and chg > 0.0:
            cand = chg
        elif mx is not None and mx >= 0.0:
            cand = mx
        elif st is not None and st >= 0.0:
            cand = st
        elif mean is not None and mean >= 0.0:
            cand = mean

        if cand is None:
            continue
        converted = converter(cand, unit)
        if converted is not None:
            out[row_date] = converted
    return out


def _extract_history_daily_values(
    state_items: list[Any],
    *,
    start_d: date,
    end_d: date,
    unit: str | None,
    converter: Any,
    take_max: bool,
) -> dict[date, Any]:
    """Extract latest state change (or daily max for totals) per HA local date from history rows."""
    by_day: dict[date, Any] = {}
    for item in state_items:
        if isinstance(item, dict):
            raw_state = item.get("s") if "s" in item else item.get("state")
            raw_ts = (
                item.get("lc")
                or item.get("last_changed")
                or item.get("lu")
                or item.get("last_updated")
            )
        else:
            raw_state = getattr(item, "state", None)
            raw_ts = getattr(item, "last_changed", None) or getattr(item, "last_updated", None)

        val = _safe_float(raw_state)
        if val is None:
            continue
        row_date = _to_local_date(raw_ts)
        if row_date is None or row_date < start_d or row_date > end_d:
            continue

        converted = converter(val, unit)
        if converted is None:
            continue
        if take_max:
            prev = by_day.get(row_date)
            if prev is None or converted > prev:
                by_day[row_date] = converted
        else:
            # Ordered ascending in HA history -> keep latest state change of the day
            by_day[row_date] = converted
    return by_day


async def async_backfill_biometrics_from_recorder(
    hass: HomeAssistant,
    runtime: DomainRuntime,
    profile: PersonProfile,
    *,
    days: int = 90,
    today: date | None = None,
) -> int:
    """Backfill past completed days from HA Recorder statistics & history, then update EMAs."""
    if "recorder" not in hass.config.components:
        return 0

    m = profile.entity_mappings
    tracked_entities: list[str] = [
        eid
        for eid in (
            m.weight_entity,
            m.body_fat_entity,
            m.active_kcal_entity,
            m.total_kcal_entity,
            m.steps_entity,
        )
        if eid
    ]
    if not tracked_entities:
        return 0

    try:
        from homeassistant.components.recorder import history, statistics
        from homeassistant.helpers.recorder import get_instance

        recorder_inst = get_instance(hass)
    except Exception:  # noqa: BLE001
        return 0

    cur_today = today or dt_util.now().date()
    end_d = cur_today - timedelta(days=1)
    start_d = cur_today - timedelta(days=max(1, days - 1))
    if end_d < start_d:
        return 0

    stats_start_utc = dt_util.as_utc(
        datetime.combine(
            start_d - timedelta(days=1), time.min, tzinfo=dt_util.DEFAULT_TIME_ZONE
        )
    )
    end_utc = dt_util.as_utc(
        datetime.combine(end_d, time.max, tzinfo=dt_util.DEFAULT_TIME_ZONE)
    )
    hist_start_d = max(start_d, cur_today - timedelta(days=_HISTORY_LOOKBACK_DAYS))
    hist_start_utc = dt_util.as_utc(
        datetime.combine(hist_start_d, time.min, tzinfo=dt_util.DEFAULT_TIME_ZONE)
    )

    entity_set = set(tracked_entities)

    def _query_recorder() -> tuple[dict[str, list[Any]], dict[str, list[Any]]]:
        stats_res = statistics.statistics_during_period(
            hass,
            stats_start_utc,
            end_utc,
            entity_set,
            "day",
            None,
            {"mean", "min", "max", "state", "change"},
        )
        hist_res = history.get_significant_states(
            hass,
            hist_start_utc,
            end_utc,
            tracked_entities,
            include_start_time_state=False,
            significant_changes_only=True,
            minimal_response=True,
            no_attributes=True,
        )
        return dict(stats_res or {}), dict(hist_res or {})

    try:
        stats_map, hist_map = await recorder_inst.async_add_executor_job(_query_recorder)
    except Exception as exc:  # noqa: BLE001
        log_event(
            _LOGGER,
            logging.WARNING,
            "biometrics_recorder_backfill_failed",
            person_id=profile.person_id,
            error_type=type(exc).__name__,
        )
        return 0

    weight_by_day: dict[date, float] = {}
    bf_by_day: dict[date, float] = {}
    active_by_day: dict[date, float] = {}
    total_by_day: dict[date, float] = {}
    steps_by_day: dict[date, int] = {}

    if m.weight_entity:
        w_unit = _unit_for_entity(hass, m.weight_entity)
        weight_by_day.update(
            _extract_measurement_series(
                stats_map.get(m.weight_entity, []),
                start_d=start_d,
                end_d=end_d,
                unit=w_unit,
                converter=_convert_weight_value,
            )
        )
        weight_by_day.update(
            _extract_history_daily_values(
                hist_map.get(m.weight_entity, []),
                start_d=hist_start_d,
                end_d=end_d,
                unit=w_unit,
                converter=_convert_weight_value,
                take_max=False,
            )
        )

    if m.body_fat_entity:
        bf_by_day.update(
            _extract_measurement_series(
                stats_map.get(m.body_fat_entity, []),
                start_d=start_d,
                end_d=end_d,
                unit=None,
                converter=_convert_body_fat_value,
            )
        )
        bf_by_day.update(
            _extract_history_daily_values(
                hist_map.get(m.body_fat_entity, []),
                start_d=hist_start_d,
                end_d=end_d,
                unit=None,
                converter=_convert_body_fat_value,
                take_max=False,
            )
        )

    if m.active_kcal_entity:
        a_unit = _unit_for_entity(hass, m.active_kcal_entity)
        active_by_day.update(
            _extract_daily_total_series(
                stats_map.get(m.active_kcal_entity, []),
                start_d=start_d,
                end_d=end_d,
                unit=a_unit,
                converter=_convert_energy_value,
            )
        )
        for d, val in _extract_history_daily_values(
            hist_map.get(m.active_kcal_entity, []),
            start_d=hist_start_d,
            end_d=end_d,
            unit=a_unit,
            converter=_convert_energy_value,
            take_max=True,
        ).items():
            active_by_day.setdefault(d, val)

    if m.total_kcal_entity:
        t_unit = _unit_for_entity(hass, m.total_kcal_entity)
        total_by_day.update(
            _extract_daily_total_series(
                stats_map.get(m.total_kcal_entity, []),
                start_d=start_d,
                end_d=end_d,
                unit=t_unit,
                converter=_convert_energy_value,
            )
        )
        for d, val in _extract_history_daily_values(
            hist_map.get(m.total_kcal_entity, []),
            start_d=hist_start_d,
            end_d=end_d,
            unit=t_unit,
            converter=_convert_energy_value,
            take_max=True,
        ).items():
            total_by_day.setdefault(d, val)

    if m.steps_entity:
        steps_by_day.update(
            _extract_daily_total_series(
                stats_map.get(m.steps_entity, []),
                start_d=start_d,
                end_d=end_d,
                unit=None,
                converter=_convert_steps_value,
            )
        )
        for d, val in _extract_history_daily_values(
            hist_map.get(m.steps_entity, []),
            start_d=hist_start_d,
            end_d=end_d,
            unit=None,
            converter=_convert_steps_value,
            take_max=True,
        ).items():
            steps_by_day.setdefault(d, val)

    all_dates = sorted(
        set(weight_by_day)
        | set(bf_by_day)
        | set(active_by_day)
        | set(total_by_day)
        | set(steps_by_day)
    )
    if not all_dates:
        return 0

    to_backfill = [
        BiometricSnapshot(
            snapshot_id=f"snap_{uuid.uuid4().hex[:12]}",
            person_id=profile.person_id,
            snap_date=d,
            weight_kg=weight_by_day.get(d),
            body_fat_pct=bf_by_day.get(d),
            active_kcal=active_by_day.get(d),
            total_kcal=total_by_day.get(d),
            steps=steps_by_day.get(d),
        )
        for d in all_dates
    ]
    written = await runtime.profile_repo.backfill_biometric_snapshots(
        profile.person_id, to_backfill
    )

    ema_span_start = cur_today - timedelta(days=max(days, 120))
    all_snaps = await runtime.profile_repo.get_biometric_snapshots_range(
        profile.person_id, ema_span_start, cur_today
    )
    changed_emas = recompute_biometric_emas(all_snaps)
    if changed_emas:
        await runtime.profile_repo.update_biometric_emas(changed_emas)

    log_event(
        _LOGGER,
        logging.INFO,
        "biometrics_recorder_backfill_complete",
        person_id=profile.person_id,
        days_written=written,
        emas_updated=len(changed_emas),
    )
    return written


async def async_ensure_biometrics_backfilled(
    hass: HomeAssistant,
    runtime: DomainRuntime,
    person_rt: PersonRuntimeData,
    *,
    days: int = 90,
) -> int:
    """Run Recorder backfill once per session (or upgrade window) safely under per-person lock."""
    target_days = max(1, days)
    if person_rt.backfilled_days >= target_days:
        return 0
    async with person_rt.backfill_lock:
        if person_rt.backfilled_days >= target_days:
            return 0
        written = await async_backfill_biometrics_from_recorder(
            hass, runtime, person_rt.profile, days=target_days
        )
        person_rt.backfilled_days = target_days
        if written > 0:
            await person_rt.coordinator.async_refresh()
        return written


def merge_live_today_snapshot(
    snaps: list[BiometricSnapshot],
    data: Any,
) -> list[BiometricSnapshot]:
    """Inject or enrich today's BiometricSnapshot using live coordinator sensor states."""
    today: date = data.today
    live_w = data.weight_kg if getattr(data, "weight_sensor_available", False) else None
    live_bf = data.body_fat_pct if getattr(data, "body_fat_sensor_available", False) else None
    has_any_live = any(
        val is not None
        for val in (
            live_w,
            live_bf,
            getattr(data, "active_kcal", None),
            getattr(data, "total_kcal", None),
            getattr(data, "steps", None),
        )
    )
    if not has_any_live:
        return snaps

    out = list(snaps)
    idx_today = next((i for i, s in enumerate(out) if s.snap_date == today), None)
    if idx_today is None:
        out.append(
            BiometricSnapshot(
                snapshot_id=f"live_{data.profile.person_id}_{today.isoformat()}",
                person_id=data.profile.person_id,
                snap_date=today,
                weight_kg=live_w,
                weight_ema_kg=getattr(data, "weight_ema_kg", None),
                body_fat_pct=live_bf,
                body_fat_ema_pct=getattr(data, "body_fat_ema_pct", None),
                active_kcal=getattr(data, "active_kcal", None),
                total_kcal=getattr(data, "total_kcal", None),
                steps=getattr(data, "steps", None),
                computed_tdee=round(data.tdee.tdee_kcal, 1),
                target_kcal=round(data.targets.target_kcal, 1),
                adaptive_tdee_kcal=data.tdee.adaptive_tdee_kcal,
                wearable_multiplier_used=data.tdee.wearable_multiplier,
            )
        )
        return out

    cur = out[idx_today]
    out[idx_today] = cur.model_copy(
        update={
            "weight_kg": cur.weight_kg if cur.weight_kg is not None else live_w,
            "weight_ema_kg": (
                cur.weight_ema_kg
                if cur.weight_ema_kg is not None
                else getattr(data, "weight_ema_kg", None)
            ),
            "body_fat_pct": cur.body_fat_pct if cur.body_fat_pct is not None else live_bf,
            "body_fat_ema_pct": (
                cur.body_fat_ema_pct
                if cur.body_fat_ema_pct is not None
                else getattr(data, "body_fat_ema_pct", None)
            ),
            "active_kcal": (
                cur.active_kcal
                if cur.active_kcal is not None
                else getattr(data, "active_kcal", None)
            ),
            "total_kcal": (
                cur.total_kcal
                if cur.total_kcal is not None
                else getattr(data, "total_kcal", None)
            ),
            "steps": cur.steps if cur.steps is not None else getattr(data, "steps", None),
        }
    )
    return out


async def async_build_trends_for_person(
    hass: HomeAssistant,
    runtime: DomainRuntime,
    person_rt: PersonRuntimeData,
    *,
    days: int = 14,
) -> TrendsSummary:
    """Shared Trends builder: ensures Recorder backfill, reads ranges, and injects today's live state."""
    norm_days = normalize_trends_days(days)
    await async_ensure_biometrics_backfilled(
        hass, runtime, person_rt, days=max(90, norm_days)
    )
    if person_rt.coordinator.data is None:
        await person_rt.coordinator.async_refresh()
    data = person_rt.coordinator.data
    if data is None:
        raise RuntimeError("Dashboard state unavailable")

    start_d = data.today - timedelta(days=norm_days - 1)
    snaps = await runtime.profile_repo.get_biometric_snapshots_range(
        person_rt.profile.person_id, start_d, data.today
    )
    snaps = merge_live_today_snapshot(snaps, data)
    daily_macros = await runtime.meal_repo.get_daily_macros_range(
        person_rt.profile.person_id, start_d, data.today
    )
    return build_trends_summary(
        profile=person_rt.profile,
        snapshots=snaps,
        daily_macros=daily_macros,
        current_tdee=data.tdee,
        current_targets=data.targets,
        today=data.today,
        days=norm_days,
    )
