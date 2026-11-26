"""Per-person DataUpdateCoordinator syncing mapped HA sensors with domain core."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from custom_components.ha_calories_buddy.const import DOMAIN
from custom_components.ha_calories_buddy.core.logging import get_logger, log_event
from custom_components.ha_calories_buddy.core.models import (
    AdaptiveState,
    BiometricSnapshot,
    CompletedActivity,
    DailyTotals,
    MealLog,
    MealPlanItem,
    PersonProfile,
    ScheduledCalendarWorkout,
    TDEEEngineType,
    WorkoutScheduleItem,
)
from custom_components.ha_calories_buddy.core.physiology.bmr import calculate_bmr
from custom_components.ha_calories_buddy.core.physiology.targets import (
    TargetResult,
    calculate_daily_targets,
)
from custom_components.ha_calories_buddy.core.physiology.tdee import (
    TDEEResult,
    calculate_adaptive_tdee,
    calculate_static_tdee,
    calculate_wearable_dynamic_tdee,
    update_ema,
)
from custom_components.ha_calories_buddy.runtime import DomainRuntime

_LOGGER = get_logger(__name__)
INVALID_STATES = frozenset({"unknown", "unavailable", "none", ""})


@dataclass
class PersonDashboardState:
    """Computed daily state exposed by sensors and WebSocket dashboard."""

    profile: PersonProfile
    today: date
    weight_kg: float
    body_fat_pct: float | None
    active_kcal: float | None
    total_kcal: float | None
    steps: int | None
    tdee: TDEEResult
    targets: TargetResult
    totals: DailyTotals
    meals: list[MealLog]
    planned_today: list[MealPlanItem]
    latest_weight_kg: float | None = None
    weight_source: str = "initial"
    weight_entity_id: str | None = None
    weight_entity_name: str | None = None
    weight_sensor_available: bool = False
    body_fat_source: str = "none"
    body_fat_entity_id: str | None = None
    body_fat_entity_name: str | None = None
    body_fat_sensor_available: bool = False
    weight_ema_kg: float | None = None
    body_fat_ema_pct: float | None = None
    is_training_day: bool = False
    training_day_source: str = "rest"
    today_workouts: list[WorkoutScheduleItem] = field(default_factory=list)
    calendar_workouts: list[ScheduledCalendarWorkout] = field(default_factory=list)
    calendar_workouts_by_date: dict[date, list[ScheduledCalendarWorkout]] = field(
        default_factory=dict
    )
    recent_activities: list[CompletedActivity] = field(default_factory=list)


def _entity_friendly_name(hass: HomeAssistant, entity_id: str | None) -> str | None:
    if not entity_id:
        return None
    st = hass.states.get(entity_id)
    if st is not None:
        fname = st.attributes.get("friendly_name")
        if isinstance(fname, str) and fname.strip():
            return fname.strip()
    return entity_id


def _parse_numeric_state(state: State | None) -> tuple[float | None, str]:
    if state is None:
        return None, ""
    raw = str(state.state).strip()
    if raw.lower() in INVALID_STATES:
        return None, ""
    try:
        val = float(raw)
    except ValueError:
        return None, ""
    unit = str(state.attributes.get("unit_of_measurement") or "").strip()
    return val, unit


def _read_weight_kg(hass: HomeAssistant, entity_id: str | None) -> float | None:
    if not entity_id:
        return None
    val, unit = _parse_numeric_state(hass.states.get(entity_id))
    if val is None or val <= 0:
        return None
    u = unit.lower()
    if u in ("lb", "lbs", "pound", "pounds"):
        return round(val * 0.45359237, 2)
    if u == "g":
        return round(val / 1000.0, 2)
    return round(val, 2)


def _read_energy_kcal(hass: HomeAssistant, entity_id: str | None) -> float | None:
    if not entity_id:
        return None
    val, unit = _parse_numeric_state(hass.states.get(entity_id))
    if val is None or val < 0:
        return None
    u = unit.lower()
    if u == "kj":
        return round(val / 4.184, 1)
    if u == "mcal":
        return round(val * 1000.0, 1)
    return round(val, 1)


def _read_percentage(hass: HomeAssistant, entity_id: str | None) -> float | None:
    if not entity_id:
        return None
    val, _ = _parse_numeric_state(hass.states.get(entity_id))
    if val is None or not (3.0 <= val <= 65.0):
        return None
    return round(val, 1)


def _read_steps(hass: HomeAssistant, entity_id: str | None) -> int | None:
    if not entity_id:
        return None
    val, _ = _parse_numeric_state(hass.states.get(entity_id))
    if val is None or val < 0:
        return None
    return int(round(val))


def _parse_duration_min(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, str) and ":" in raw:
        parts = [p.strip() for p in raw.split(":")]
        try:
            nums = [float(p) for p in parts]
            if len(nums) == 3:
                return round(nums[0] * 60.0 + nums[1] + nums[2] / 60.0, 1)
            if len(nums) == 2:
                return round(nums[0] + nums[1] / 60.0, 1)
        except ValueError:
            return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val < 0:
        return None
    if val > 20000:  # Garmin milliseconds
        return round(val / 60000.0, 1)
    if val > 240:  # Seconds
        return round(val / 60.0, 1)
    return round(val, 1)


def _activity_type_label(raw: Any) -> str:
    if isinstance(raw, dict):
        return str(raw.get("typeKey") or raw.get("type") or "other")
    return str(raw or "other")


def _parse_single_activity(raw: dict[str, Any]) -> CompletedActivity | None:
    name = str(
        raw.get("activityName")
        or raw.get("name")
        or raw.get("title")
        or _activity_type_label(raw.get("activityType"))
    ).strip()
    if not name or name.lower() in INVALID_STATES:
        return None
    cal_raw = raw.get("calories") if raw.get("calories") is not None else raw.get("activeKilocalories")
    try:
        calories = round(float(cal_raw), 1) if cal_raw is not None else None
    except (TypeError, ValueError):
        calories = None
    dist_raw = raw.get("distance") if raw.get("distance") is not None else raw.get("distance_km")
    try:
        dist_val = float(dist_raw) if dist_raw is not None else None
        distance_km = (
            round(dist_val / 1000.0, 2)
            if dist_val is not None and raw.get("distance") is not None and dist_val > 100
            else (round(dist_val, 2) if dist_val is not None else None)
        )
    except (TypeError, ValueError):
        distance_km = None
    start = raw.get("startTimeLocal") or raw.get("startTime") or raw.get("start_time")
    return CompletedActivity(
        activity_id=str(raw.get("activityId") or raw.get("id") or ""),
        name=name,
        activity_type=_activity_type_label(raw.get("activityType") or raw.get("activity_type")),
        start_time=str(start) if start else None,
        duration_min=_parse_duration_min(
            raw.get("duration_min") if raw.get("duration_min") is not None else raw.get("duration")
        ),
        calories=calories,
        distance_km=distance_km,
    )


def _read_recent_activities(
    hass: HomeAssistant, entity_id: str | None
) -> list[CompletedActivity]:
    if not entity_id:
        return []
    st = hass.states.get(entity_id)
    if st is None or str(st.state).strip().lower() in INVALID_STATES:
        return []
    attrs: dict[str, Any] = dict(st.attributes) if st.attributes else {}
    out: list[CompletedActivity] = []
    raw_list = attrs.get("last_activities") or attrs.get("activities")
    if isinstance(raw_list, list):
        for entry in raw_list[:10]:
            if isinstance(entry, dict):
                parsed = _parse_single_activity(entry)
                if parsed is not None:
                    out.append(parsed)
    elif attrs.get("activityName") or attrs.get("activityType"):
        parsed = _parse_single_activity(dict(attrs))
        if parsed is not None:
            out.append(parsed)
    return out


def _extract_event_date_str(raw_start: Any) -> str:
    if isinstance(raw_start, dict):
        val = raw_start.get("dateTime") or raw_start.get("date") or ""
    else:
        val = raw_start or ""
    return str(val).strip()


class PersonNutritionCoordinator(DataUpdateCoordinator[PersonDashboardState]):
    """Coordinator calculating daily targets, TDEE, and intake for one person."""

    def __init__(
        self,
        hass: HomeAssistant,
        runtime: DomainRuntime,
        profile: PersonProfile,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{profile.person_id}",
            update_interval=timedelta(minutes=15),
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=0.0, immediate=True
            ),
        )
        self.runtime = runtime
        self.profile = profile
        self._unsubs: list[Callable[[], None]] = []

    def setup_listeners(self) -> None:
        """Subscribe to mapped HA biometric entities and daily rollover timers."""
        self._clear_listeners()
        mappings = self.profile.entity_mappings
        watched = [
            ent
            for ent in (
                mappings.weight_entity,
                mappings.body_fat_entity,
                mappings.active_kcal_entity,
                mappings.total_kcal_entity,
                mappings.steps_entity,
                mappings.workout_calendar_entity,
                mappings.activities_entity,
            )
            if ent
        ]
        if watched:

            @callback
            def _on_entity_change(_event: Event[EventStateChangedData]) -> None:
                self.hass.async_create_task(self.async_request_refresh())

            self._unsubs.append(
                async_track_state_change_event(self.hass, watched, _on_entity_change)
            )

        async def _on_eod_snapshot(_now: object) -> None:
            await self.async_persist_today_snapshot()

        async def _on_midnight(_now: object) -> None:
            await self.async_request_refresh()

        self._unsubs.append(
            async_track_time_change(
                self.hass, _on_eod_snapshot, hour=23, minute=55, second=0
            )
        )
        self._unsubs.append(
            async_track_time_change(
                self.hass, _on_midnight, hour=0, minute=0, second=5
            )
        )

    def _clear_listeners(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    async def async_shutdown(self) -> None:
        """Cancel all state/time listeners and DataUpdateCoordinator refresh timers."""
        self._clear_listeners()
        await super().async_shutdown()

    async def _fetch_calendar_workouts(
        self, today: date
    ) -> tuple[list[ScheduledCalendarWorkout], dict[date, list[ScheduledCalendarWorkout]]]:
        cal_entity = self.profile.entity_mappings.workout_calendar_entity
        if not cal_entity:
            return [], {}
        if not self.hass.services.has_service("calendar", "get_events"):
            return [], {}
        start_dt = datetime.combine(today, time.min, tzinfo=dt_util.DEFAULT_TIME_ZONE)
        end_dt = datetime.combine(
            today + timedelta(days=6), time.max, tzinfo=dt_util.DEFAULT_TIME_ZONE
        )
        try:
            resp = await self.hass.services.async_call(
                "calendar",
                "get_events",
                {
                    "entity_id": cal_entity,
                    "start_date_time": start_dt.isoformat(),
                    "end_date_time": end_dt.isoformat(),
                },
                blocking=True,
                return_response=True,
            )
        except Exception as exc:  # noqa: BLE001
            log_event(
                _LOGGER,
                logging.WARNING,
                "workout_calendar_fetch_failed",
                entity_id=cal_entity,
                error_type=type(exc).__name__,
            )
            return [], {}

        flat: list[ScheduledCalendarWorkout] = []
        by_date: dict[date, list[ScheduledCalendarWorkout]] = {}
        if not isinstance(resp, dict):
            return flat, by_date
        ent_payload = resp.get(cal_entity)
        if ent_payload is None and len(resp) == 1:
            ent_payload = next(iter(resp.values()))
        if not isinstance(ent_payload, dict):
            return flat, by_date
        events_raw = ent_payload.get("events")
        if not isinstance(events_raw, list):
            return flat, by_date
        for ev in events_raw:
            if not isinstance(ev, dict):
                continue
            summary = str(ev.get("summary") or "").strip()
            if not summary:
                continue
            start_str = _extract_event_date_str(ev.get("start"))
            end_str = _extract_event_date_str(ev.get("end")) or None
            if len(start_str) < 10:
                continue
            try:
                ev_date = date.fromisoformat(start_str[:10])
            except ValueError:
                continue
            item = ScheduledCalendarWorkout(
                summary=summary,
                start=start_str,
                end=end_str,
                description=str(ev.get("description") or "").strip(),
            )
            flat.append(item)
            by_date.setdefault(ev_date, []).append(item)
        return flat, by_date

    async def _async_update_data(self) -> PersonDashboardState:
        today = dt_util.now().date()
        latest_snap = await self.runtime.profile_repo.get_latest_biometric_snapshot(
            self.profile.person_id
        )

        mappings = self.profile.entity_mappings
        live_weight = _read_weight_kg(self.hass, mappings.weight_entity)
        live_bf = _read_percentage(self.hass, mappings.body_fat_entity)
        live_active = _read_energy_kcal(self.hass, mappings.active_kcal_entity)
        live_total = _read_energy_kcal(self.hass, mappings.total_kcal_entity)
        live_steps = _read_steps(self.hass, mappings.steps_entity)
        recent_activities = _read_recent_activities(self.hass, mappings.activities_entity)

        if live_weight is not None:
            weight_kg = live_weight
            weight_source = "sensor"
        elif latest_snap and latest_snap.weight_kg is not None:
            weight_kg = latest_snap.weight_kg
            weight_source = "snapshot"
        else:
            weight_kg = self.profile.initial_weight_kg
            weight_source = "initial"

        if live_bf is not None:
            body_fat_pct = live_bf
            body_fat_source = "sensor"
        elif latest_snap and latest_snap.body_fat_pct is not None:
            body_fat_pct = latest_snap.body_fat_pct
            body_fat_source = "snapshot"
        elif self.profile.body_fat_pct is not None:
            body_fat_pct = self.profile.body_fat_pct
            body_fat_source = "profile"
        else:
            body_fat_pct = None
            body_fat_source = "none"

        age_years = max(10, today.year - self.profile.birth_year)
        bmr_kcal, bmr_formula = calculate_bmr(
            weight_kg=weight_kg,
            height_cm=self.profile.height_cm,
            age_years=age_years,
            sex=self.profile.sex,
            body_fat_pct=body_fat_pct,
        )

        past_snaps_28d = await self.runtime.profile_repo.get_recent_biometric_snapshots(
            self.profile.person_id, before_date=today, days=28
        )
        prior_w_ema = next(
            (s.weight_ema_kg for s in reversed(past_snaps_28d) if s.weight_ema_kg is not None),
            None,
        )
        weight_ema_kg = (
            latest_snap.weight_ema_kg
            if latest_snap is not None and latest_snap.snap_date == today and latest_snap.weight_ema_kg is not None
            else update_ema(prior_w_ema, weight_kg, alpha=0.12)
        )
        prior_bf_ema = next(
            (
                s.body_fat_ema_pct
                for s in reversed(past_snaps_28d)
                if s.body_fat_ema_pct is not None
            ),
            None,
        )
        if (
            latest_snap is not None
            and latest_snap.snap_date == today
            and latest_snap.body_fat_ema_pct is not None
        ):
            body_fat_ema_pct: float | None = latest_snap.body_fat_ema_pct
        elif body_fat_pct is not None:
            body_fat_ema_pct = update_ema(prior_bf_ema, body_fat_pct, alpha=0.10)
        else:
            body_fat_ema_pct = prior_bf_ema

        if self.profile.tdee_engine == TDEEEngineType.ADAPTIVE:
            win_start = today - timedelta(days=28)
            win_end = today - timedelta(days=1)
            macros_map = await self.runtime.meal_repo.get_daily_macros_range(
                self.profile.person_id, win_start, win_end
            )
            intake_by_date = {d: m.kcal for d, m in macros_map.items()}
            prev_adaptive = await self.runtime.profile_repo.get_adaptive_state(
                self.profile.person_id
            )
            tdee_res = calculate_adaptive_tdee(
                bmr_kcal=bmr_kcal,
                bmr_formula=bmr_formula,
                activity_level=self.profile.activity_level,
                past_snapshots=past_snaps_28d,
                daily_intake_by_date=intake_by_date,
                today_active_kcal=live_active,
                today_total_kcal=live_total,
                base_correction_factor=self.profile.wearable_correction_factor,
                prev_adaptive_state=prev_adaptive,
                today=today,
            )
            if tdee_res.adaptive_tdee_kcal is not None and (
                prev_adaptive is None
                or prev_adaptive.last_calibrated_date is None
                or prev_adaptive.last_calibrated_date < today
            ):
                await self.runtime.profile_repo.upsert_adaptive_state(
                    AdaptiveState(
                        person_id=self.profile.person_id,
                        last_calibrated_date=today,
                        adaptive_tdee_kcal=tdee_res.adaptive_tdee_kcal,
                        wearable_multiplier=(
                            tdee_res.wearable_multiplier
                            if tdee_res.wearable_multiplier is not None
                            else self.profile.wearable_correction_factor
                        ),
                        confidence=tdee_res.adaptive_confidence or "medium",
                    )
                )
        elif self.profile.tdee_engine == TDEEEngineType.WEARABLE_DYNAMIC:
            past_snaps_7d = [
                s for s in past_snaps_28d if s.snap_date >= today - timedelta(days=7)
            ]
            tdee_res = calculate_wearable_dynamic_tdee(
                bmr_kcal=bmr_kcal,
                bmr_formula=bmr_formula,
                activity_level=self.profile.activity_level,
                past_snapshots=past_snaps_7d,
                today_active_kcal=live_active,
                today_total_kcal=live_total,
                correction_factor=self.profile.wearable_correction_factor,
            )
        else:
            tdee_res = calculate_static_tdee(
                bmr_kcal=bmr_kcal,
                bmr_formula=bmr_formula,
                activity_level=self.profile.activity_level,
            )

        cal_workouts, cal_by_date = await self._fetch_calendar_workouts(today)
        today_workouts = await self.runtime.workout_repo.get_scheduled_for_date(
            self.profile.person_id, today
        )
        day_override = await self.runtime.workout_repo.get_override(
            self.profile.person_id, today
        )
        if day_override is not None:
            is_training_day = day_override.is_training_day
            training_day_source = "manual_on" if day_override.is_training_day else "manual_off"
        elif cal_by_date.get(today):
            is_training_day = True
            training_day_source = "calendar"
        elif today_workouts:
            is_training_day = True
            training_day_source = "schedule"
        else:
            is_training_day = False
            training_day_source = "rest"

        targets = calculate_daily_targets(
            profile=self.profile,
            weight_kg=weight_kg,
            body_fat_pct=body_fat_pct,
            tdee_result=tdee_res,
            is_training_day=is_training_day,
        )
        totals = await self.runtime.meal_repo.get_daily_totals(
            self.profile.person_id, today
        )
        meals = await self.runtime.meal_repo.list_meals_for_date(
            self.profile.person_id, today
        )
        planned_pairs = await self.runtime.plan_repo.get_plan_items_for_range(
            self.profile.person_id, today, today
        )

        return PersonDashboardState(
            profile=self.profile,
            today=today,
            weight_kg=weight_kg,
            body_fat_pct=body_fat_pct,
            active_kcal=live_active,
            total_kcal=live_total,
            steps=live_steps,
            tdee=tdee_res,
            targets=targets,
            totals=totals,
            meals=meals,
            planned_today=[item for _, item in planned_pairs],
            latest_weight_kg=weight_kg,
            weight_source=weight_source,
            weight_entity_id=mappings.weight_entity or None,
            weight_entity_name=_entity_friendly_name(self.hass, mappings.weight_entity),
            weight_sensor_available=(live_weight is not None),
            body_fat_source=body_fat_source,
            body_fat_entity_id=mappings.body_fat_entity or None,
            body_fat_entity_name=_entity_friendly_name(
                self.hass, mappings.body_fat_entity
            ),
            body_fat_sensor_available=(live_bf is not None),
            weight_ema_kg=weight_ema_kg,
            body_fat_ema_pct=body_fat_ema_pct,
            is_training_day=is_training_day,
            training_day_source=training_day_source,
            today_workouts=today_workouts,
            calendar_workouts=cal_workouts,
            calendar_workouts_by_date=cal_by_date,
            recent_activities=recent_activities,
        )

    async def async_persist_today_snapshot(
        self, override_weight_kg: float | None = None
    ) -> BiometricSnapshot:
        """Persist today's biometric values into SQLite (updating weight & BF EMA)."""
        state = self.data or await self._async_update_data()
        today = dt_util.now().date()
        weight_val = override_weight_kg if override_weight_kg is not None else state.weight_kg

        past_snaps = await self.runtime.profile_repo.get_recent_biometric_snapshots(
            self.profile.person_id, before_date=today, days=30
        )
        prev_w_ema = next(
            (s.weight_ema_kg for s in reversed(past_snaps) if s.weight_ema_kg is not None),
            None,
        )
        w_ema = update_ema(prev_w_ema, weight_val, alpha=0.12)

        prev_bf_ema = next(
            (s.body_fat_ema_pct for s in reversed(past_snaps) if s.body_fat_ema_pct is not None),
            None,
        )
        bf_ema = (
            update_ema(prev_bf_ema, state.body_fat_pct, alpha=0.10)
            if state.body_fat_pct is not None
            else prev_bf_ema
        )

        snapshot = BiometricSnapshot(
            snapshot_id=f"snap_{self.profile.person_id}_{today.isoformat()}_{uuid.uuid4().hex[:6]}",
            person_id=self.profile.person_id,
            snap_date=today,
            weight_kg=round(weight_val, 2),
            weight_ema_kg=w_ema,
            body_fat_pct=state.body_fat_pct,
            body_fat_ema_pct=bf_ema,
            active_kcal=state.active_kcal,
            total_kcal=state.total_kcal,
            steps=state.steps,
            computed_tdee=state.tdee.tdee_kcal,
            target_kcal=state.targets.target_kcal,
            adaptive_tdee_kcal=state.tdee.adaptive_tdee_kcal,
            wearable_multiplier_used=state.tdee.wearable_multiplier,
        )
        await self.runtime.profile_repo.upsert_biometric_snapshot(snapshot)
        log_event(
            _LOGGER,
            logging.INFO,
            "biometric_snapshot_saved",
            person_id=self.profile.person_id,
            snap_date=today.isoformat(),
        )
        return snapshot
