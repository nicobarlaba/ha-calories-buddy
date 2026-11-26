"""The Butler: Smart Shopping List aggregation, yield conversion, and todo.* dedup."""

from __future__ import annotations

import json
import math
import re
import uuid
from datetime import UTC, date, datetime
from typing import Any

from custom_components.ha_calories_buddy.core.models import (
    EffectiveFood,
    MealPlanItem,
    PersonProfile,
    PlanItemStatus,
    PlannedIngredient,
    PreferenceEntry,
    PreferenceStance,
    PreparationState,
    ShoppingDraft,
    ShoppingDraftItem,
)
from custom_components.ha_calories_buddy.core.nutrition.yield_factors import (
    default_yield_for_category,
)
from custom_components.ha_calories_buddy.core.planner.preferences import PreferenceEngine
from custom_components.ha_calories_buddy.core.planner.slot_resolver import (
    extract_logged_slot_keys,
)
from custom_components.ha_calories_buddy.core.storage.food_repo import FoodRepository
from custom_components.ha_calories_buddy.core.storage.meal_repo import MealRepository
from custom_components.ha_calories_buddy.core.storage.plan_repo import MealPlanRepository
from custom_components.ha_calories_buddy.core.storage.seeder import normalize_text
from custom_components.ha_calories_buddy.core.storage.shopping_repo import (
    ShoppingRepository,
)

AISLE_ORDER: tuple[str, ...] = (
    "produce",
    "meat_fish",
    "dairy_eggs",
    "pantry",
    "beverages_snacks",
    "other",
)

CATEGORY_TO_AISLE: dict[str, str] = {
    "produce": "produce",
    "fruit": "produce",
    "vegetables": "produce",
    "potatoes": "produce",
    "poultry": "meat_fish",
    "red_meat": "meat_fish",
    "fish": "meat_fish",
    "dairy": "dairy_eggs",
    "egg": "dairy_eggs",
    "rice": "pantry",
    "pasta": "pantry",
    "grains": "pantry",
    "oats": "pantry",
    "legumes": "pantry",
    "bread": "pantry",
    "oil": "pantry",
    "nuts": "pantry",
    "snack": "beverages_snacks",
    "beverage": "beverages_snacks",
    "other": "other",
}

_CANNED_LEGUME_TOKENS = ("canned", "scatola", "barattolo", "lessi", "lessati", "precotti")


def aisle_for_category(category: str) -> str:
    """Resolve food category to a canonical supermarket aisle key."""
    return CATEGORY_TO_AISLE.get((category or "other").strip().lower(), "other")


def convert_planned_to_purchase_grams(
    grams: float,
    state: PreparationState,
    category: str,
    yield_factor: float,
    food_ref_state: PreparationState,
    name: str,
) -> float:
    """Convert cooked recipe quantities back to raw purchase weight using yield_factor."""
    if grams <= 0.0:
        return 0.0
    if state != PreparationState.COOKED:
        return grams

    cat = (category or "other").strip().lower()
    norm_name = normalize_text(name)
    if cat == "legumes" and (
        food_ref_state == PreparationState.COOKED
        or any(tok in norm_name for tok in _CANNED_LEGUME_TOKENS)
    ):
        return grams

    factor = (
        yield_factor
        if yield_factor > 0.0 and abs(yield_factor - 1.0) > 1e-6
        else default_yield_for_category(cat)
    )
    if factor > 0.0:
        return grams / factor
    return grams


def format_purchase_quantity_and_unit(
    total_grams: float,
    category: str,
    name: str,
) -> tuple[float, str]:
    """Convert raw grams into clean grocery units (eggs -> pcs, liquids -> ml, else g)."""
    safe_grams = max(1.0, float(total_grams))
    cat = (category or "other").strip().lower()
    norm_name = normalize_text(name)
    tokens = set(norm_name.split())

    is_egg_white = "albume" in norm_name or "albumi" in norm_name or "egg white" in norm_name
    is_whole_egg = not is_egg_white and (
        cat == "egg"
        or "uovo" in tokens
        or "uova" in tokens
        or "egg" in tokens
        or "eggs" in tokens
    )
    if is_whole_egg:
        pcs = max(1, round(safe_grams / 60.0))
        return float(pcs), "pcs"

    is_liquid = cat == "beverage" or any(
        tok in norm_name for tok in ("latte", "milk", "brodo", "broth", "bevanda", "succo", "juice")
    )
    if is_liquid:
        ml = float(math.ceil(safe_grams / 10.0) * 10)
        return ml, "ml"

    if safe_grams < 100.0:
        rounded = float(max(5, math.ceil(safe_grams / 5.0) * 5))
    else:
        rounded = float(math.ceil(safe_grams / 10.0) * 10)
    return rounded, "g"


def _format_qty_unit(quantity: float, unit: str, language: str) -> str:
    u = (unit or "g").strip().lower()
    if u == "pcs":
        unit_lbl = "pz" if language == "it" else "pcs"
        qty_val = int(round(quantity)) if abs(quantity - round(quantity)) < 0.05 else round(quantity, 1)
        return f"{qty_val} {unit_lbl}"
    if u == "g":
        if quantity >= 1000.0 and int(round(quantity)) % 100 == 0:
            kg = quantity / 1000.0
            kg_str = f"{int(kg)}" if abs(kg - round(kg)) < 0.01 else f"{kg:.1f}"
            return f"{kg_str} kg"
        return f"{int(round(quantity))}g"
    if u == "ml":
        if quantity >= 1000.0 and int(round(quantity)) % 100 == 0:
            liters = quantity / 1000.0
            l_str = f"{int(liters)}" if abs(liters - round(liters)) < 0.01 else f"{liters:.1f}"
            return f"{l_str} L"
        return f"{int(round(quantity))} ml"
    qty_num = int(round(quantity)) if abs(quantity - round(quantity)) < 0.05 else round(quantity, 1)
    return f"{qty_num} {unit}"


def format_todo_item_summary(
    item: ShoppingDraftItem,
    language: str = "en",
) -> str:
    """Format a ShoppingDraftItem cleanly for Home Assistant todo.add_item."""
    qty_str = _format_qty_unit(item.quantity, item.unit, language)
    if item.brand and item.brand.strip():
        return f"{item.label} ({item.brand.strip()}) — {qty_str}"
    return f"{item.label} — {qty_str}"


def normalize_todo_base_name(summary: str) -> str:
    """Extract normalized product base name from a todo summary line for deduplication."""
    head = re.split(r"\s+[—\-:]\s+", summary.strip(), maxsplit=1)[0]
    head = re.sub(r"\([^)]*\)", " ", head)
    head = re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:kg|g|gr|ml|l|lt|pz|pcs|x)?\b", " ", head, flags=re.IGNORECASE)
    return normalize_text(head)


def filter_deduplicated_items(
    items: list[ShoppingDraftItem],
    existing_todo_summaries: list[str],
) -> tuple[list[ShoppingDraftItem], list[ShoppingDraftItem]]:
    """Split checked items into (to_add, skipped_duplicates) against active todo.* items."""
    existing_norms = [
        normalize_todo_base_name(s) for s in existing_todo_summaries if normalize_todo_base_name(s)
    ]

    def _is_duplicate(item: ShoppingDraftItem) -> bool:
        target = normalize_todo_base_name(item.label)
        if not target:
            return False
        target_tokens = {tok for tok in target.split() if len(tok) >= 4}
        for ext in existing_norms:
            if target == ext:
                return True
            if target in ext or ext in target:
                return True
            ext_tokens = {tok for tok in ext.split() if len(tok) >= 4}
            if target_tokens and target_tokens == ext_tokens:
                return True
        return False

    to_add: list[ShoppingDraftItem] = []
    skipped: list[ShoppingDraftItem] = []
    for item in items:
        if not item.checked:
            continue
        if _is_duplicate(item):
            skipped.append(item)
        else:
            to_add.append(item)
    return to_add, skipped


def _parse_plan_ingredients(item: MealPlanItem) -> list[PlannedIngredient]:
    try:
        raw = json.loads(item.ingredients_json or "[]")
    except ValueError:
        return []
    if not isinstance(raw, list):
        return []
    out: list[PlannedIngredient] = []
    for entry in raw:
        try:
            out.append(PlannedIngredient.model_validate(entry))
        except Exception:  # noqa: BLE001
            continue
    return out


class ShoppingListService:
    """Aggregates remaining planned meals into an editable aisle-sorted ShoppingDraft."""

    def __init__(
        self,
        *,
        plan_repo: MealPlanRepository,
        meal_repo: MealRepository,
        food_repo: FoodRepository,
        shopping_repo: ShoppingRepository,
        preference_engine: PreferenceEngine,
    ) -> None:
        self._plan_repo = plan_repo
        self._meal_repo = meal_repo
        self._food_repo = food_repo
        self._shopping_repo = shopping_repo
        self._pref_engine = preference_engine

    async def generate_draft(
        self,
        primary_profile: PersonProfile,
        profiles_to_include: list[PersonProfile],
        start_date: date,
        end_date: date,
    ) -> ShoppingDraft:
        """Aggregate remaining unconsumed/uncovered planned meals into a persistent draft."""
        if end_date < start_date:
            start_date, end_date = end_date, start_date

        seen_profiles: list[PersonProfile] = []
        seen_ids: set[str] = set()
        for p in [primary_profile, *profiles_to_include]:
            if p.person_id not in seen_ids:
                seen_ids.add(p.person_id)
                seen_profiles.append(p)

        brand_entries: list[PreferenceEntry] = []
        seen_pref_keys: set[tuple[str, str]] = set()
        for p in seen_profiles:
            ctx = await self._pref_engine.build_context(p, reference_date=start_date)
            for entry in ctx.effective_entries:
                if entry.stance != PreferenceStance.PREFERRED_BRAND:
                    continue
                k = (entry.target_type.lower(), normalize_text(entry.target_value))
                if k not in seen_pref_keys:
                    seen_pref_keys.add(k)
                    brand_entries.append(entry)

        food_cache: dict[str, EffectiveFood | None] = {}
        name_cache: dict[str, EffectiveFood | None] = {}
        primary_plan_id: str | None = None

        active_plan = await self._plan_repo.get_active_plan(
            primary_profile.person_id, reference_date=start_date
        )
        if active_plan is not None:
            primary_plan_id = active_plan.plan_id

        # Bucket key -> aggregation state dict
        buckets: dict[tuple[str, str], dict[str, Any]] = {}

        for profile in seen_profiles:
            plan_pairs = await self._plan_repo.get_plan_items_for_range(
                profile.person_id,
                start_date,
                end_date,
                status=PlanItemStatus.PLANNED,
            )
            if not plan_pairs:
                continue

            logged_meals = await self._meal_repo.list_meals_for_range(
                profile.person_id,
                start_date,
                end_date,
                include_deleted=False,
            )
            logged_slots_by_date: dict[date, set[str]] = {}
            by_date_meals: dict[date, list[Any]] = {}
            for m in logged_meals:
                by_date_meals.setdefault(m.log_date, []).append(m)
            for d, day_meals in by_date_meals.items():
                logged_slots_by_date[d] = extract_logged_slot_keys(day_meals)

            for plan_date, plan_item in plan_pairs:
                if normalize_text(plan_item.meal_slot) in logged_slots_by_date.get(
                    plan_date, set()
                ):
                    continue

                ings = _parse_plan_ingredients(plan_item)
                for ing in ings:
                    food = await self._resolve_ingredient_food(
                        ing, profile.person_id, food_cache, name_cache
                    )
                    category = food.category if food else "other"
                    yield_factor = food.yield_factor if food else 1.0
                    food_ref_state = (
                        food.preparation_state
                        if food
                        else PreparationState.READY_TO_EAT
                    )
                    purchase_g = convert_planned_to_purchase_grams(
                        ing.grams,
                        ing.preparation_state,
                        category,
                        yield_factor,
                        food_ref_state,
                        ing.name,
                    )
                    label = (
                        food.localized_name(primary_profile.language)
                        if food is not None
                        else ing.name.strip()
                    )
                    brand = (ing.brand or (food.brand if food else None) or "").strip() or None
                    if brand is None and brand_entries:
                        brand = self._match_preferred_brand(
                            label, food, brand_entries
                        )

                    key_id = food.food_id if food is not None else normalize_text(label)
                    key_brand = normalize_text(brand or "")
                    b_key = (key_id, key_brand)
                    if b_key not in buckets:
                        buckets[b_key] = {
                            "food_id": food.food_id if food is not None else None,
                            "label": label,
                            "brand": brand,
                            "category": category,
                            "grams": 0.0,
                            "source_meals": [],
                        }
                    buckets[b_key]["grams"] += purchase_g
                    meal_tag = f"{plan_date.isoformat()} {plan_item.meal_slot}: {plan_item.title}"
                    if meal_tag not in buckets[b_key]["source_meals"]:
                        buckets[b_key]["source_meals"].append(meal_tag)

        draft_id = f"draft_{uuid.uuid4().hex[:12]}"
        items: list[ShoppingDraftItem] = []
        for bucket in buckets.values():
            qty, unit = format_purchase_quantity_and_unit(
                bucket["grams"], bucket["category"], bucket["label"]
            )
            aisle = aisle_for_category(bucket["category"])
            items.append(
                ShoppingDraftItem(
                    draft_item_id=f"sitem_{uuid.uuid4().hex[:10]}",
                    draft_id=draft_id,
                    food_id=bucket["food_id"],
                    label=bucket["label"],
                    brand=bucket["brand"],
                    quantity=qty,
                    unit=unit,
                    category=bucket["category"],
                    aisle=aisle,
                    checked=True,
                    source_meals=bucket["source_meals"],
                )
            )

        aisle_rank = {a: idx for idx, a in enumerate(AISLE_ORDER)}
        items.sort(
            key=lambda it: (aisle_rank.get(it.aisle, 99), normalize_text(it.label))
        )

        draft = ShoppingDraft(
            draft_id=draft_id,
            person_id=primary_profile.person_id,
            plan_id=primary_plan_id,
            start_date=start_date,
            end_date=end_date,
            scope_person_ids=[p.person_id for p in seen_profiles],
            status="draft",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            items=items,
        )
        return await self._shopping_repo.save_draft(draft)

    async def _resolve_ingredient_food(
        self,
        ing: PlannedIngredient,
        person_id: str,
        food_cache: dict[str, EffectiveFood | None],
        name_cache: dict[str, EffectiveFood | None],
    ) -> EffectiveFood | None:
        if ing.food_id:
            if ing.food_id not in food_cache:
                food_cache[ing.food_id] = await self._food_repo.get_effective_food(
                    ing.food_id, person_id=person_id
                )
            if food_cache[ing.food_id] is not None:
                return food_cache[ing.food_id]

        norm_name = normalize_text(ing.name)
        if not norm_name:
            return None
        if norm_name not in name_cache:
            hits = await self._food_repo.search(
                ing.name, person_id=person_id, brand=ing.brand, limit=1
            )
            name_cache[norm_name] = hits[0] if hits else None
        return name_cache[norm_name]

    @staticmethod
    def _match_preferred_brand(
        label: str,
        food: EffectiveFood | None,
        brand_entries: list[PreferenceEntry],
    ) -> str | None:
        """Attach a preferred brand from household/person PreferenceEntry rules."""
        haystack = normalize_text(
            " ".join(
                filter(
                    None,
                    [
                        label,
                        food.name if food else "",
                        food.name_it if food else "",
                        food.category if food else "",
                    ],
                )
            )
        )
        cat_norm = normalize_text(food.category if food else "")
        for entry in brand_entries:
            t_type = (entry.target_type or "").strip().lower()
            t_val = entry.target_value.strip()
            t_norm = normalize_text(t_val)
            notes_str = entry.notes.strip()
            notes_norm = normalize_text(notes_str)
            if t_type == "brand":
                if notes_norm and any(
                    tok in haystack for tok in notes_norm.split() if len(tok) >= 3
                ):
                    return t_val
                if t_norm and t_norm in haystack:
                    return t_val
            elif t_type in ("ingredient", "category", "dish"):
                if (t_norm and t_norm in haystack) or (cat_norm and t_norm == cat_norm):
                    return notes_str or t_val
        return None
