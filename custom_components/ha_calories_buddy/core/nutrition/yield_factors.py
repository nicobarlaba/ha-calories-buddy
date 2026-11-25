"""Raw vs. cooked yield conversion and state ambiguity detection."""

from __future__ import annotations

from custom_components.ha_calories_buddy.core.models import PreparationState

CATEGORY_YIELD_FACTORS: dict[str, float] = {
    "rice": 2.5,
    "pasta": 2.2,
    "grains": 2.5,
    "legumes": 2.4,
    "oats": 2.8,
    "poultry": 0.75,
    "red_meat": 0.72,
    "fish": 0.80,
    "potatoes": 0.95,
    "vegetables": 0.85,
}

READY_TO_EAT_CATEGORIES = frozenset(
    {"oil", "dairy", "fruit", "bread", "nuts", "snack", "beverage", "egg", "other"}
)


def default_yield_for_category(category: str) -> float:
    """Return standard cooked/raw yield factor for a category."""
    return CATEGORY_YIELD_FACTORS.get(category.lower(), 1.0)


def resolve_preparation_state(
    category: str,
    input_grams: float,
    input_state: PreparationState,
    ref_state: PreparationState,
) -> tuple[PreparationState, bool]:
    """Resolve unspecified preparation states and flag high-risk ambiguities.

    Returns (effective_state, is_ambiguous).
    """
    if input_state != PreparationState.UNSPECIFIED:
        return input_state, False

    cat = category.lower()
    if ref_state == PreparationState.READY_TO_EAT or cat in READY_TO_EAT_CATEGORIES:
        return PreparationState.READY_TO_EAT, False

    if cat in {"rice", "pasta", "grains", "oats"}:
        # 25g-160g is the plausible single-portion dry weight range
        if 25.0 <= input_grams <= 160.0:
            return PreparationState.RAW, False
        return ref_state, True

    if cat == "legumes":
        # Canned cooked vs dry legumes differ ~2.5-3x in calories: always ask when unspecified
        return ref_state, True

    if cat in {"poultry", "red_meat", "fish"}:
        if 40.0 <= input_grams <= 500.0:
            return PreparationState.RAW, False
        return ref_state, True

    return ref_state, False


def convert_to_reference_grams(
    input_grams: float,
    effective_state: PreparationState,
    ref_state: PreparationState,
    yield_factor: float,
) -> float:
    """Convert logged weight into the reference weight of the stored food record."""
    if input_grams <= 0.0:
        return 0.0

    factor = yield_factor if yield_factor > 0.0 else 1.0
    if (
        effective_state == ref_state
        or effective_state == PreparationState.READY_TO_EAT
        or ref_state == PreparationState.READY_TO_EAT
        or effective_state == PreparationState.UNSPECIFIED
        or ref_state == PreparationState.UNSPECIFIED
    ):
        return input_grams

    if ref_state == PreparationState.RAW and effective_state == PreparationState.COOKED:
        return input_grams / factor

    if ref_state == PreparationState.COOKED and effective_state == PreparationState.RAW:
        return input_grams * factor

    return input_grams
