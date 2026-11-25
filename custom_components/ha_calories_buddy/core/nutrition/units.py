"""Unit normalization and household portion conversion to grams."""

from __future__ import annotations

from custom_components.ha_calories_buddy.core.models import EffectiveFood

UNIT_ALIASES: dict[str, str] = {
    "g": "g",
    "gr": "g",
    "gram": "g",
    "grams": "g",
    "grammo": "g",
    "grammi": "g",
    "kg": "kg",
    "chilogrammo": "kg",
    "chilogrammi": "kg",
    "oz": "oz",
    "lb": "lb",
    "lbs": "lb",
    "ml": "ml",
    "l": "l",
    "lt": "l",
    "litro": "l",
    "litri": "l",
    "tbsp": "tbsp",
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
    "cucchiaio": "tbsp",
    "cucchiai": "tbsp",
    "tsp": "tsp",
    "teaspoon": "tsp",
    "teaspoons": "tsp",
    "cucchiaino": "tsp",
    "cucchiaini": "tsp",
    "cup": "cup",
    "cups": "cup",
    "tazza": "cup",
    "tazze": "cup",
    "slice": "slice",
    "slices": "slice",
    "fetta": "slice",
    "fette": "slice",
    "piece": "piece",
    "pieces": "piece",
    "pz": "piece",
    "pezzo": "piece",
    "pezzi": "piece",
    "can": "can",
    "cans": "can",
    "tin": "can",
    "scatoletta": "can",
    "scatolette": "can",
    "barattolo": "can",
    "pack": "pack",
    "packet": "pack",
    "confezione": "pack",
    "confezioni": "pack",
    "bustina": "pack",
    "vasetto": "pack",
    "serving": "serving",
    "servings": "serving",
    "porzione": "serving",
    "porzioni": "serving",
}

MASS_FACTORS_TO_G: dict[str, float] = {
    "g": 1.0,
    "kg": 1000.0,
    "oz": 28.3495,
    "lb": 453.592,
}

CATEGORY_DEFAULT_PORTIONS_G: dict[str, dict[str, float]] = {
    "oil": {"tbsp": 10.0, "tsp": 4.0},
    "dairy": {"cup": 240.0, "tbsp": 15.0, "tsp": 5.0, "pack": 150.0},
    "egg": {"piece": 55.0},
    "bread": {"slice": 35.0, "piece": 60.0},
    "legumes": {"can": 240.0, "tbsp": 20.0},
    "fish": {"can": 56.0, "piece": 150.0},
    "poultry": {"piece": 150.0, "slice": 20.0},
    "fruit": {"piece": 150.0},
    "vegetables": {"piece": 120.0},
    "nuts": {"tbsp": 15.0, "tsp": 5.0, "piece": 3.0},
    "snack": {"tbsp": 18.0, "tsp": 6.0, "serving": 30.0},
    "other": {"tbsp": 15.0, "tsp": 5.0, "cup": 200.0, "serving": 100.0},
}


def normalize_unit(unit: str) -> str:
    """Normalize multilingual unit strings into canonical unit identifiers."""
    clean = unit.strip().lower().rstrip(".")
    return UNIT_ALIASES.get(clean, clean)


def convert_to_grams(
    quantity: float,
    unit: str,
    food: EffectiveFood | None = None,
) -> tuple[float, bool]:
    """Convert quantity + unit into grams.

    Priority:
    1. Standard mass units (`g`, `kg`, `oz`, `lb`).
    2. Person custom portions (`food.person_portions`).
    3. Food-specific standard portions (`food.food_portions`).
    4. Volume density (`ml`, `l`, adjusting `0.92` g/ml for oils).
    5. Category culinary portion defaults.
    Returns `(grams, unknown_unit)`.
    """
    if quantity <= 0.0:
        return 0.0, True

    canon = normalize_unit(unit)

    if canon in MASS_FACTORS_TO_G:
        return round(quantity * MASS_FACTORS_TO_G[canon], 2), False

    if food is not None:
        if canon in food.person_portions:
            return round(quantity * food.person_portions[canon], 2), False
        if canon in food.food_portions:
            return round(quantity * food.food_portions[canon], 2), False

    category = food.category.lower() if food is not None else "other"

    if canon == "ml":
        density = 0.92 if category == "oil" else 1.0
        return round(quantity * density, 2), False
    if canon == "l":
        density = 0.92 if category == "oil" else 1.0
        return round(quantity * 1000.0 * density, 2), False

    cat_portions = CATEGORY_DEFAULT_PORTIONS_G.get(
        category, CATEGORY_DEFAULT_PORTIONS_G["other"]
    )
    if canon in cat_portions:
        return round(quantity * cat_portions[canon], 2), False

    other_portions = CATEGORY_DEFAULT_PORTIONS_G["other"]
    if canon in other_portions:
        return round(quantity * other_portions[canon], 2), False

    # Unknown unit: treat conservatively as 100g per unit and flag for confirmation
    return round(quantity * 100.0, 2), True
