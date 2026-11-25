"""Home Assistant sensor entities for each monitored person."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.ha_calories_buddy.const import CONF_PERSON_ID, DOMAIN, VERSION
from custom_components.ha_calories_buddy.coordinator import (
    PersonDashboardState,
    PersonNutritionCoordinator,
)
from custom_components.ha_calories_buddy.runtime import DomainRuntime


@dataclass(frozen=True, kw_only=True)
class CaloriesBuddySensorDescription(SensorEntityDescription):
    """Describe a ha-calories-buddy sensor entity."""

    value_fn: Callable[[PersonDashboardState], float | int]
    attrs_fn: Callable[[PersonDashboardState], dict[str, Any]] | None = None


def _slot_breakdown_attrs(state: PersonDashboardState) -> dict[str, Any]:
    return {
        "meals_confirmed": state.totals.confirmed_count,
        "by_slot": {
            slot: {
                "kcal": m.kcal,
                "protein_g": m.protein_g,
                "carbs_g": m.carbs_g,
                "fat_g": m.fat_g,
            }
            for slot, m in state.totals.by_slot.items()
        },
    }


SENSOR_DESCRIPTIONS: tuple[CaloriesBuddySensorDescription, ...] = (
    CaloriesBuddySensorDescription(
        key="calories_consumed",
        translation_key="calories_consumed",
        native_unit_of_measurement="kcal",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:fire",
        value_fn=lambda s: round(s.totals.consumed.kcal, 1),
        attrs_fn=_slot_breakdown_attrs,
    ),
    CaloriesBuddySensorDescription(
        key="calories_target",
        translation_key="calories_target",
        native_unit_of_measurement="kcal",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:target",
        value_fn=lambda s: round(s.targets.target_kcal, 1),
        attrs_fn=lambda s: {
            "mode": s.targets.mode.value,
            "goal_type": s.profile.goal_type.value,
            "bmr_kcal": s.tdee.bmr_kcal,
            "tdee_kcal": s.tdee.tdee_kcal,
            "safety_warnings": s.targets.safety_warnings,
        },
    ),
    CaloriesBuddySensorDescription(
        key="calories_remaining",
        translation_key="calories_remaining",
        native_unit_of_measurement="kcal",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:scale-balance",
        value_fn=lambda s: round(s.targets.target_kcal - s.totals.consumed.kcal, 1),
    ),
    CaloriesBuddySensorDescription(
        key="protein_consumed",
        translation_key="protein_consumed",
        native_unit_of_measurement="g",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:food-steak",
        value_fn=lambda s: round(s.totals.consumed.protein_g, 1),
        attrs_fn=lambda s: {
            "target_g": round(s.targets.protein_g, 1),
            "remaining_g": round(s.targets.protein_g - s.totals.consumed.protein_g, 1),
        },
    ),
    CaloriesBuddySensorDescription(
        key="carbs_consumed",
        translation_key="carbs_consumed",
        native_unit_of_measurement="g",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:bread-slice",
        value_fn=lambda s: round(s.totals.consumed.carbs_g, 1),
        attrs_fn=lambda s: {
            "target_g": round(s.targets.carbs_g, 1),
            "remaining_g": round(s.targets.carbs_g - s.totals.consumed.carbs_g, 1),
        },
    ),
    CaloriesBuddySensorDescription(
        key="fat_consumed",
        translation_key="fat_consumed",
        native_unit_of_measurement="g",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water",
        value_fn=lambda s: round(s.totals.consumed.fat_g, 1),
        attrs_fn=lambda s: {
            "target_g": round(s.targets.fat_g, 1),
            "remaining_g": round(s.targets.fat_g - s.totals.consumed.fat_g, 1),
        },
    ),
    CaloriesBuddySensorDescription(
        key="tdee",
        translation_key="tdee",
        native_unit_of_measurement="kcal",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:run-fast",
        value_fn=lambda s: round(s.tdee.tdee_kcal, 1),
        attrs_fn=lambda s: {
            "engine": s.tdee.engine_used.value,
            "bmr_kcal": s.tdee.bmr_kcal,
            "bmr_formula": s.tdee.bmr_formula,
            "wearable_baseline_kcal": s.tdee.wearable_baseline_kcal,
            "intraday_delta_kcal": s.tdee.intraday_delta_kcal,
            "fallback_to_static": s.tdee.fallback_to_static,
            "days_used": s.tdee.days_used,
            "weight_kg": s.weight_kg,
        },
    ),
    CaloriesBuddySensorDescription(
        key="pending_confirmations",
        translation_key="pending_confirmations",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:alert-circle-check-outline",
        value_fn=lambda s: s.totals.pending_count,
    ),
    CaloriesBuddySensorDescription(
        key="daily_adherence_pct",
        translation_key="daily_adherence_pct",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:percent-circle-outline",
        value_fn=lambda s: (
            round((s.totals.consumed.kcal / s.targets.target_kcal) * 100.0, 1)
            if s.targets.target_kcal > 0
            else 0.0
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors for a Person Profile entry."""
    person_id = entry.data.get(CONF_PERSON_ID)
    if not person_id:
        return
    runtime: DomainRuntime = hass.data[DOMAIN]
    person_rt = runtime.persons.get(person_id)
    if person_rt is None:
        return

    async_add_entities(
        CaloriesBuddySensor(person_rt.coordinator, desc)
        for desc in SENSOR_DESCRIPTIONS
    )


class CaloriesBuddySensor(
    CoordinatorEntity[PersonNutritionCoordinator], SensorEntity
):
    """Sensor exposing daily nutritional telemetry for one person."""

    entity_description: CaloriesBuddySensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PersonNutritionCoordinator,
        description: CaloriesBuddySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        person = coordinator.profile
        self._attr_unique_id = f"{person.person_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, person.person_id)},
            name=f"Calories Buddy — {person.display_name}",
            manufacturer="ha-calories-buddy",
            model="Personal Dietitian Profile",
            sw_version=VERSION,
        )

    @property
    def native_value(self) -> float | int | None:
        """Return the sensor value from coordinator data."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return optional extra state attributes."""
        if self.coordinator.data is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
