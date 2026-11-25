"""Deterministic meal slot normalization and time-of-day slot inference."""

from __future__ import annotations

from datetime import datetime, time

from custom_components.ha_calories_buddy.core.models import MealSlotConfig
from custom_components.ha_calories_buddy.core.storage.seeder import normalize_text

_JUNK_SLOTS = frozenset(
    {
        "",
        "unspecified",
        "unknown",
        "none",
        "null",
        "n/a",
        "other",
        "meal",
        "pasto",
        "altro",
    }
)

_CANONICAL_GROUPS: dict[str, tuple[str, ...]] = {
    "breakfast": ("breakfast", "colazione", "prima colazione", "morning"),
    "morning_snack": (
        "morning snack",
        "spuntino mattutino",
        "spuntino mattina",
        "merenda mattina",
    ),
    "lunch": ("lunch", "pranzo", "midday", "mezzogiorno"),
    "snack": (
        "snack",
        "spuntino",
        "spuntino serale",
        "spuntino notturno",
        "dopocena",
        "dopo cena",
        "merenda",
        "afternoon snack",
        "evening snack",
        "night snack",
        "late snack",
        "bedtime snack",
        "after dinner",
        "spuntino pomeridiano",
        "pre workout",
        "post workout",
    ),
    "dinner": ("dinner", "cena", "supper", "evening"),
}

_SYNONYM_TO_GROUP: dict[str, str] = {
    normalize_text(alias): group
    for group, aliases in _CANONICAL_GROUPS.items()
    for alias in aliases
}

_EVENING_SNACK_PHRASES = (
    "serale",
    "notturno",
    "dopocena",
    "dopo cena",
    "evening snack",
    "night snack",
    "late snack",
    "bedtime",
    "after dinner",
)

_DEFAULT_SLOT_TIMES: dict[str, int] = {
    "breakfast": 8 * 60,
    "morning_snack": 10 * 60 + 30,
    "lunch": 13 * 60,
    "snack": 16 * 60 + 30,
    "dinner": 20 * 60,
}


def canonical_slot_group(slot_name: str) -> str:
    """Resolve any meal slot name to a canonical culinary slot group."""
    norm = normalize_text(slot_name)
    if not norm:
        return "other"
    exact = _SYNONYM_TO_GROUP.get(norm)
    if exact is not None:
        return exact
    for alias, group in sorted(
        _SYNONYM_TO_GROUP.items(), key=lambda pair: len(pair[0]), reverse=True
    ):
        if alias and alias in norm:
            return group
    return "other"


def parse_hhmm_minutes(hhmm: str | None) -> int | None:
    """Parse an HH:MM string into minutes since midnight."""
    if not hhmm:
        return None
    parts = hhmm.strip().split(":")
    if len(parts) < 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def default_slots_for_language(language: str) -> list[MealSlotConfig]:
    """Return standard 4-slot configuration for a language."""
    if language == "it":
        return [
            MealSlotConfig(slot_id="colazione", name="Colazione", approx_time="08:00", max_cook_minutes=15),
            MealSlotConfig(slot_id="pranzo", name="Pranzo", approx_time="13:00", max_cook_minutes=30),
            MealSlotConfig(slot_id="spuntino", name="Spuntino", approx_time="16:30", max_cook_minutes=10),
            MealSlotConfig(slot_id="cena", name="Cena", approx_time="20:00", max_cook_minutes=30),
        ]
    return [
        MealSlotConfig(slot_id="breakfast", name="Breakfast", approx_time="08:00", max_cook_minutes=15),
        MealSlotConfig(slot_id="lunch", name="Lunch", approx_time="13:00", max_cook_minutes=30),
        MealSlotConfig(slot_id="snack", name="Snack", approx_time="16:30", max_cook_minutes=10),
        MealSlotConfig(slot_id="dinner", name="Dinner", approx_time="20:00", max_cook_minutes=30),
    ]


def slot_approx_minutes(slot: MealSlotConfig) -> int:
    """Resolve approximate time-of-day in minutes for a configured meal slot."""
    parsed = parse_hhmm_minutes(slot.approx_time)
    if parsed is not None and slot.approx_time != "12:30":
        return parsed
    norm_name = normalize_text(slot.name)
    if any(phrase in norm_name for phrase in _EVENING_SNACK_PHRASES):
        return 22 * 60
    group = canonical_slot_group(slot.name)
    if group in _DEFAULT_SLOT_TIMES:
        return _DEFAULT_SLOT_TIMES[group]
    return parsed if parsed is not None else 13 * 60


def default_slot_for_time(
    meal_slots: list[MealSlotConfig],
    language: str = "en",
    now_time: time | None = None,
) -> str:
    """Select the most plausible configured meal slot for the given local time."""
    slots = meal_slots or default_slots_for_language(language)
    current = now_time or datetime.now().time()
    now_mins = current.hour * 60 + current.minute

    best_slot = slots[0]
    best_dist = 24 * 60
    for slot in slots:
        target_mins = slot_approx_minutes(slot)
        dist = abs(now_mins - target_mins)
        if dist < best_dist:
            best_dist = dist
            best_slot = slot
    return best_slot.name


def resolve_meal_slot(
    raw_slot: str | None,
    meal_slots: list[MealSlotConfig],
    language: str = "en",
    now_time: time | None = None,
) -> str:
    """Normalize any raw/LLM slot value to a valid configured slot name.

    Guarantees invalid placeholders such as 'unspecified' are never persisted.
    """
    slots = meal_slots or default_slots_for_language(language)
    norm_raw = normalize_text(raw_slot or "")

    if not norm_raw or norm_raw in _JUNK_SLOTS:
        return default_slot_for_time(slots, language=language, now_time=now_time)

    # 1. Exact case/accent-insensitive match on configured slot name or slot_id
    for slot in slots:
        if normalize_text(slot.name) == norm_raw or normalize_text(slot.slot_id) == norm_raw:
            return slot.name

    # 2. Canonical EN <-> IT synonym mapping onto configured slots
    raw_group = _SYNONYM_TO_GROUP.get(norm_raw)
    if raw_group is None:
        for alias, grp in _SYNONYM_TO_GROUP.items():
            if alias in norm_raw:
                raw_group = grp
                break

    if raw_group is not None:
        for slot in slots:
            slot_group = _SYNONYM_TO_GROUP.get(normalize_text(slot.name))
            if slot_group == raw_group:
                return slot.name
        if raw_group == "morning_snack":
            for slot in slots:
                if _SYNONYM_TO_GROUP.get(normalize_text(slot.name)) == "snack":
                    return slot.name

    # 3. Partial token match on configured slots
    for slot in slots:
        norm_slot = normalize_text(slot.name)
        if norm_raw in norm_slot or norm_slot in norm_raw:
            return slot.name

    # 4. Deterministic time-of-day fallback (never return 'unspecified')
    return default_slot_for_time(slots, language=language, now_time=now_time)
