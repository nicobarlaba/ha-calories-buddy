"""Switch entity representing whether today is an active Training Day."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.ha_calories_buddy.const import CONF_PERSON_ID, DOMAIN, VERSION
from custom_components.ha_calories_buddy.coordinator import (
    PersonNutritionCoordinator,
)
from custom_components.ha_calories_buddy.core.models import TrainingDayOverride
from custom_components.ha_calories_buddy.runtime import DomainRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Training Day switch entity for a Person Profile entry."""
    person_id = entry.data.get(CONF_PERSON_ID)
    if not person_id:
        return
    runtime: DomainRuntime = hass.data[DOMAIN]
    person_rt = runtime.persons.get(person_id)
    if person_rt is None:
        return

    async_add_entities([CaloriesBuddyTrainingDaySwitch(person_rt.coordinator, runtime)])


class CaloriesBuddyTrainingDaySwitch(
    CoordinatorEntity[PersonNutritionCoordinator], SwitchEntity
):
    """Toggle manual/auto Training Day status for today (activates training targets)."""

    _attr_has_entity_name = True
    _attr_translation_key = "training_day"
    _attr_icon = "mdi:dumbbell"

    def __init__(
        self,
        coordinator: PersonNutritionCoordinator,
        runtime: DomainRuntime,
    ) -> None:
        super().__init__(coordinator)
        self._runtime = runtime
        person = coordinator.profile
        self._person_id = person.person_id
        self._attr_unique_id = f"{person.person_id}_training_day"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, person.person_id)},
            name=f"Calories Buddy — {person.display_name}",
            manufacturer="ha-calories-buddy",
            model="Personal Dietitian Profile",
            sw_version=VERSION,
        )

    @property
    def is_on(self) -> bool | None:
        """Return True when today is an active training day."""
        if self.coordinator.data is None:
            return None
        return bool(self.coordinator.data.is_training_day)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose workout source, scheduled sessions, and whether training targets apply."""
        data = self.coordinator.data
        if data is None:
            return None
        manual = data.profile.manual_targets
        training_targets_active = bool(
            data.is_training_day
            and manual is not None
            and manual.training_kcal is not None
        )
        return {
            "source": data.training_day_source,
            "scheduled_workouts": [w.model_dump(mode="json") for w in data.today_workouts],
            "calendar_workouts": [
                w.model_dump(mode="json")
                for w in data.calendar_workouts_by_date.get(data.today, [])
            ],
            "training_targets_active": training_targets_active,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Mark today as a manual Training Day and recompute targets."""
        today = self.coordinator.data.today if self.coordinator.data else None
        if today is None:
            await self.coordinator.async_refresh()
            assert self.coordinator.data is not None
            today = self.coordinator.data.today
        await self._runtime.workout_repo.set_override(
            TrainingDayOverride(
                person_id=self._person_id,
                override_date=today,
                is_training_day=True,
                source="manual",
            )
        )
        await self._runtime.async_notify_person_updated(
            self._person_id, "training_day_on"
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Mark today as a manual Rest Day and recompute targets."""
        today = self.coordinator.data.today if self.coordinator.data else None
        if today is None:
            await self.coordinator.async_refresh()
            assert self.coordinator.data is not None
            today = self.coordinator.data.today
        await self._runtime.workout_repo.set_override(
            TrainingDayOverride(
                person_id=self._person_id,
                override_date=today,
                is_training_day=False,
                source="manual",
            )
        )
        await self._runtime.async_notify_person_updated(
            self._person_id, "training_day_off"
        )
