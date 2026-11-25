"""Per-person DataUpdateCoordinator syncing mapped HA sensors with domain core."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

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
    BiometricSnapshot,
    DailyTotals,
    MealLog,
    MealPlanItem,
    PersonProfile,
    TDEEEngineType,
)
from custom_components.ha_calories_buddy.core.physiology.bmr import calculate_bmr
from custom_components.ha_calories_buddy.core.physiology.targets import (
    TargetResult,
    calculate_daily_targets,
)
from custom_components.ha_calories_buddy.core.physiology.tdee import (
    TDEEResult,
    calculate_static_tdee,
    calculate_wearable_dynamic_tdee,
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

        past_snaps = await self.runtime.profile_repo.get_recent_biometric_snapshots(
            self.profile.person_id, before_date=today, days=7
        )

        if self.profile.tdee_engine == TDEEEngineType.WEARABLE_DYNAMIC:
            tdee_res = calculate_wearable_dynamic_tdee(
                bmr_kcal=bmr_kcal,
                bmr_formula=bmr_formula,
                activity_level=self.profile.activity_level,
                past_snapshots=past_snaps,
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

        targets = calculate_daily_targets(
            profile=self.profile,
            weight_kg=weight_kg,
            body_fat_pct=body_fat_pct,
            tdee_result=tdee_res,
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
        )

    async def async_persist_today_snapshot(
        self, override_weight_kg: float | None = None
    ) -> BiometricSnapshot:
        """Persist today's biometric values into SQLite (updating weight EMA)."""
        state = self.data or await self._async_update_data()
        today = dt_util.now().date()
        weight_val = override_weight_kg if override_weight_kg is not None else state.weight_kg

        prev_snap = await self.runtime.profile_repo.get_latest_biometric_snapshot(
            self.profile.person_id
        )
        if prev_snap and prev_snap.weight_ema_kg is not None and prev_snap.snap_date < today:
            alpha = 0.12
            ema = round(alpha * weight_val + (1.0 - alpha) * prev_snap.weight_ema_kg, 2)
        else:
            ema = round(weight_val, 2)

        snapshot = BiometricSnapshot(
            snapshot_id=f"snap_{self.profile.person_id}_{today.isoformat()}_{uuid.uuid4().hex[:6]}",
            person_id=self.profile.person_id,
            snap_date=today,
            weight_kg=round(weight_val, 2),
            weight_ema_kg=ema,
            body_fat_pct=state.body_fat_pct,
            active_kcal=state.active_kcal,
            total_kcal=state.total_kcal,
            steps=state.steps,
            computed_tdee=state.tdee.tdee_kcal,
            target_kcal=state.targets.target_kcal,
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
