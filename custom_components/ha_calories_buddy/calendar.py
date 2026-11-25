"""Per-person Home Assistant Calendar entity rendering meals and daily summaries."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from custom_components.ha_calories_buddy.const import CONF_PERSON_ID, DOMAIN, VERSION
from custom_components.ha_calories_buddy.coordinator import PersonNutritionCoordinator
from custom_components.ha_calories_buddy.core.models import MealLog, MealStatus
from custom_components.ha_calories_buddy.runtime import DomainRuntime

CALENDAR_COLOR_PALETTE = [
    "#03a9f4",
    "#4caf50",
    "#ff9800",
    "#ab47bc",
    "#e91e63",
    "#009688",
]


def _pick_person_color(person_id: str, index: int) -> str:
    if index >= 0:
        return CALENDAR_COLOR_PALETTE[index % len(CALENDAR_COLOR_PALETTE)]
    return CALENDAR_COLOR_PALETTE[sum(ord(ch) for ch in person_id) % len(CALENDAR_COLOR_PALETTE)]


def _meal_to_calendar_event(meal: MealLog) -> CalendarEvent:
    local_start = dt_util.as_local(meal.logged_at)
    local_end = local_start + timedelta(minutes=30)
    prefix = "⚠️" if meal.status == MealStatus.PENDING_CONFIRMATION else "🍽️"
    item_names = ", ".join(item.display_name for item in meal.items[:3])
    if len(meal.items) > 3:
        item_names += f" (+{len(meal.items) - 3})"

    summary = f"{prefix} [{meal.meal_slot}] {round(meal.total_macros.kcal)} kcal"
    if item_names:
        summary = f"{summary} — {item_names}"

    lines = [
        (
            f"- {i.display_name}: {i.input_quantity:g} {i.input_unit} "
            f"({round(i.macros.kcal)} kcal | P {i.macros.protein_g:.1f}g "
            f"C {i.macros.carbs_g:.1f}g F {i.macros.fat_g:.1f}g)"
        )
        for i in meal.items
    ]
    return CalendarEvent(
        start=local_start,
        end=local_end,
        summary=summary,
        description="\n".join(lines),
        uid=meal.log_id,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the unified per-person nutrition calendar entity."""
    person_id = entry.data.get(CONF_PERSON_ID)
    if not person_id:
        return
    runtime: DomainRuntime = hass.data[DOMAIN]
    person_rt = runtime.persons.get(person_id)
    if person_rt is None:
        return

    # Use insertion order in runtime.persons so adding new members alphabetically never shifts existing colors
    insertion_ids = list(runtime.persons.keys())
    idx = insertion_ids.index(person_id) if person_id in insertion_ids else -1
    color = _pick_person_color(person_id, idx)

    async_add_entities([CaloriesBuddyCalendarEntity(person_rt.coordinator, color)])


class CaloriesBuddyCalendarEntity(
    CoordinatorEntity[PersonNutritionCoordinator], CalendarEntity
):
    """Per-person CalendarEntity showing logged meals, pending items, and daily summaries."""

    _attr_has_entity_name = True
    _attr_translation_key = "nutrition"

    def __init__(
        self,
        coordinator: PersonNutritionCoordinator,
        initial_color: str,
    ) -> None:
        super().__init__(coordinator)
        person = coordinator.profile
        self._attr_unique_id = f"{person.person_id}_nutrition_calendar"
        self._attr_initial_color = initial_color
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, person.person_id)},
            name=f"Calories Buddy — {person.display_name}",
            manufacturer="ha-calories-buddy",
            model="Personal Dietitian Profile",
            sw_version=VERSION,
        )

    @property
    def event(self) -> CalendarEvent | None:
        """Return the latest meal or daily summary event for entity state."""
        data = self.coordinator.data
        if data is None:
            return None
        if data.meals:
            return _meal_to_calendar_event(data.meals[-1])
        return CalendarEvent(
            start=data.today,
            end=data.today + timedelta(days=1),
            summary=(
                f"📊 0 / {round(data.targets.target_kcal)} kcal | "
                f"Target P {round(data.targets.protein_g)}g C {round(data.targets.carbs_g)}g F {round(data.targets.fat_g)}g"
            ),
            uid=f"summary_{data.profile.person_id}_{data.today.isoformat()}",
        )

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return all logged meals and daily summary events within [start_date, end_date]."""
        start_d: date = dt_util.as_local(start_date).date()
        end_d: date = dt_util.as_local(end_date).date()

        meals = await self.coordinator.runtime.meal_repo.list_meals_for_range(
            person_id=self.coordinator.profile.person_id,
            start_date=start_d,
            end_date=end_d,
        )

        events: list[CalendarEvent] = []
        by_day: dict[date, list[MealLog]] = {}
        for meal in meals:
            events.append(_meal_to_calendar_event(meal))
            by_day.setdefault(meal.log_date, []).append(meal)

        target_kcal = (
            self.coordinator.data.targets.target_kcal
            if self.coordinator.data is not None
            else 2000.0
        )

        for day, day_meals in sorted(by_day.items()):
            confirmed = [m for m in day_meals if m.status == MealStatus.CONFIRMED]
            if not confirmed:
                continue
            kcal = sum(m.total_macros.kcal for m in confirmed)
            protein = sum(m.total_macros.protein_g for m in confirmed)
            carbs = sum(m.total_macros.carbs_g for m in confirmed)
            fat = sum(m.total_macros.fat_g for m in confirmed)
            events.append(
                CalendarEvent(
                    start=day,
                    end=day + timedelta(days=1),
                    summary=(
                        f"📊 {round(kcal)} / {round(target_kcal)} kcal | "
                        f"P {round(protein)}g C {round(carbs)}g F {round(fat)}g"
                    ),
                    uid=f"summary_{self.coordinator.profile.person_id}_{day.isoformat()}",
                )
            )

        return events
