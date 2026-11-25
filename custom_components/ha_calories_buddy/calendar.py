"""Per-person Home Assistant Calendar entity rendering meals, plans, and daily summaries."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from custom_components.ha_calories_buddy.const import CONF_PERSON_ID, DOMAIN, VERSION
from custom_components.ha_calories_buddy.coordinator import PersonNutritionCoordinator
from custom_components.ha_calories_buddy.core.models import (
    MealLog,
    MealPlanItem,
    MealSlotConfig,
    MealStatus,
    PlanItemStatus,
    PlannedIngredient,
)
from custom_components.ha_calories_buddy.core.planner.slot_resolver import (
    resolve_meal_slot,
    slot_approx_minutes,
)
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


def _slot_anchor_datetime(
    target_date: date,
    slot_name: str,
    meal_slots: list[MealSlotConfig],
    language: str,
) -> datetime:
    resolved_name = resolve_meal_slot(slot_name, meal_slots, language=language)
    matched = next((s for s in meal_slots if s.name == resolved_name), None)
    mins = (
        slot_approx_minutes(matched)
        if matched is not None
        else slot_approx_minutes(MealSlotConfig(slot_id="tmp", name=resolved_name))
    )
    tz = dt_util.get_default_time_zone()
    return datetime.combine(target_date, time(hour=mins // 60, minute=mins % 60), tzinfo=tz)


def _meal_to_calendar_event(
    meal: MealLog,
    meal_slots: list[MealSlotConfig],
    language: str,
) -> CalendarEvent:
    local_logged = dt_util.as_local(meal.logged_at)
    slot_dt = _slot_anchor_datetime(meal.log_date, meal.meal_slot, meal_slots, language)

    # Anchor retrospective logs (outside +-90 min of slot's typical time) to the meal slot window
    if (
        local_logged.date() != meal.log_date
        or abs((local_logged - slot_dt).total_seconds()) > 90 * 60
    ):
        local_start = slot_dt
    else:
        local_start = local_logged

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
            f"C {i.macros.carbs_g:.1f}g F {i.macros.fat_g:.1f}g "
            f"Fib {(i.macros.fiber_g or 0.0):.1f}g)"
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


def _planned_to_calendar_event(
    plan_date: date,
    item: MealPlanItem,
    meal_slots: list[MealSlotConfig],
    language: str,
) -> CalendarEvent:
    local_start = _slot_anchor_datetime(plan_date, item.meal_slot, meal_slots, language)
    local_end = local_start + timedelta(minutes=30)
    tag = "Piano" if language == "it" else "Plan"
    summary = f"📋 [{tag} - {item.meal_slot}] {round(item.macros.kcal)} kcal — {item.title}"

    desc_lines = [
        f"⏱️ {item.prep_minutes} min | P {item.macros.protein_g:.1f}g "
        f"C {item.macros.carbs_g:.1f}g F {item.macros.fat_g:.1f}g "
        f"Fib {(item.macros.fiber_g or 0.0):.1f}g"
    ]
    try:
        raw_ings = json.loads(item.ingredients_json or "[]")
        for raw in raw_ings:
            ing = PlannedIngredient.model_validate(raw)
            desc_lines.append(f"- {ing.name}: {ing.grams:g} g")
    except Exception:  # noqa: BLE001
        pass

    return CalendarEvent(
        start=local_start,
        end=local_end,
        summary=summary,
        description="\n".join(desc_lines),
        uid=item.plan_item_id,
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
    """Per-person CalendarEntity showing logged meals, planned menus, and daily summaries."""

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
            return _meal_to_calendar_event(
                data.meals[-1],
                data.profile.meal_slots,
                data.profile.language,
            )
        next_planned = next(
            (p for p in data.planned_today if p.status == PlanItemStatus.PLANNED),
            None,
        )
        if next_planned is not None:
            return _planned_to_calendar_event(
                data.today,
                next_planned,
                data.profile.meal_slots,
                data.profile.language,
            )
        return CalendarEvent(
            start=data.today,
            end=data.today + timedelta(days=1),
            summary=(
                f"📊 0 / {round(data.targets.target_kcal)} kcal | "
                f"Target P {round(data.targets.protein_g)}g C {round(data.targets.carbs_g)}g "
                f"F {round(data.targets.fat_g)}g Fib {round(data.targets.fiber_g)}g"
            ),
            uid=f"summary_{data.profile.person_id}_{data.today.isoformat()}",
        )

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return logged meals, planned meals, and daily summaries within [start_date, end_date]."""
        start_d: date = dt_util.as_local(start_date).date()
        end_d: date = dt_util.as_local(end_date).date()
        profile = self.coordinator.profile

        meals = await self.coordinator.runtime.meal_repo.list_meals_for_range(
            person_id=profile.person_id,
            start_date=start_d,
            end_date=end_d,
        )
        planned_items = await self.coordinator.runtime.plan_repo.get_plan_items_for_range(
            person_id=profile.person_id,
            start_date=start_d,
            end_date=end_d,
            status=PlanItemStatus.PLANNED,
        )

        events: list[CalendarEvent] = []
        by_day: dict[date, list[MealLog]] = {}
        for meal in meals:
            events.append(
                _meal_to_calendar_event(meal, profile.meal_slots, profile.language)
            )
            by_day.setdefault(meal.log_date, []).append(meal)

        for plan_date, plan_item in planned_items:
            events.append(
                _planned_to_calendar_event(
                    plan_date, plan_item, profile.meal_slots, profile.language
                )
            )

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
            fiber = sum((m.total_macros.fiber_g or 0.0) for m in confirmed)
            events.append(
                CalendarEvent(
                    start=day,
                    end=day + timedelta(days=1),
                    summary=(
                        f"📊 {round(kcal)} / {round(target_kcal)} kcal | "
                        f"P {round(protein)}g C {round(carbs)}g F {round(fat)}g Fib {round(fiber)}g"
                    ),
                    uid=f"summary_{profile.person_id}_{day.isoformat()}",
                )
            )

        return events
