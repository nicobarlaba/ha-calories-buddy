"""Two-tier preference merging, learning analytics, and zero-trust safety gate."""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, Field

from custom_components.ha_calories_buddy.core.models import (
    EffectiveFood,
    MealFeedbackRating,
    PersonProfile,
    PreferenceEntry,
    PreferenceScope,
    PreferenceStance,
)
from custom_components.ha_calories_buddy.core.storage.meal_repo import MealRepository
from custom_components.ha_calories_buddy.core.storage.preference_repo import (
    PreferenceRepository,
)
from custom_components.ha_calories_buddy.core.storage.seeder import normalize_text

HARD_STANCES = frozenset(
    {
        PreferenceStance.ALLERGY,
        PreferenceStance.INTOLERANCE,
        PreferenceStance.BLACKLIST,
    }
)

# Bilingual EN/IT expansion keywords & banned categories for hard-constraint verification
_SYNONYM_MAP: dict[str, tuple[str, ...]] = {
    "gluten": (
        "gluten",
        "glutine",
        "wheat",
        "grano",
        "frumento",
        "pasta",
        "spaghetti",
        "penne",
        "fusilli",
        "rigatoni",
        "bread",
        "pane",
        "farro",
        "spelt",
        "barley",
        "orzo",
        "rye",
        "segale",
        "seitan",
        "couscous",
        "fette biscottate",
        "flour",
        "farina",
    ),
    "lactose": (
        "lactose",
        "lattosio",
        "milk",
        "latte",
        "yogurt",
        "cheese",
        "formaggio",
        "mozzarella",
        "parmigiano",
        "grana",
        "parmesan",
        "ricotta",
        "cottage cheese",
        "fiocchi di latte",
        "butter",
        "burro",
        "cream",
        "panna",
        "whey",
    ),
    "dairy": (
        "lactose",
        "lattosio",
        "milk",
        "latte",
        "yogurt",
        "cheese",
        "formaggio",
        "mozzarella",
        "parmigiano",
        "grana",
        "parmesan",
        "ricotta",
        "cottage cheese",
        "fiocchi di latte",
        "butter",
        "burro",
        "cream",
        "panna",
        "whey",
    ),
    "nuts": (
        "nuts",
        "frutta a guscio",
        "almond",
        "almonds",
        "mandorle",
        "mandorla",
        "walnut",
        "walnuts",
        "noce",
        "noci",
        "hazelnut",
        "nocciole",
        "cashew",
        "anacardi",
        "pistachio",
        "pistacchi",
        "peanut",
        "peanuts",
        "arachidi",
    ),
    "peanuts": ("peanut", "peanuts", "arachidi", "arachide"),
    "egg": ("egg", "eggs", "uovo", "uova", "albume", "albumi", "egg white"),
    "soy": ("soy", "soia", "tofu", "edamame", "tempeh", "miso"),
    "fish": (
        "fish",
        "pesce",
        "salmon",
        "salmone",
        "tuna",
        "tonno",
        "cod",
        "merluzzo",
        "nasello",
        "trout",
        "trota",
        "sea bass",
        "branzino",
        "orata",
    ),
    "shellfish": (
        "shellfish",
        "crostacei",
        "molluschi",
        "shrimp",
        "gamberi",
        "prawn",
        "crab",
        "granchio",
        "mussel",
        "cozze",
        "vongole",
        "calamari",
    ),
    "sesame": ("sesame", "sesamo", "tahini"),
    "pork": (
        "pork",
        "maiale",
        "suino",
        "prosciutto",
        "ham",
        "bacon",
        "pancetta",
        "lonza",
        "salami",
        "salame",
    ),
    "vegetarian": (
        "chicken",
        "pollo",
        "turkey",
        "tacchino",
        "beef",
        "manzo",
        "pork",
        "maiale",
        "bresaola",
        "prosciutto",
        "ham",
        "meat",
        "carne",
        "salmon",
        "salmone",
        "tuna",
        "tonno",
        "cod",
        "merluzzo",
        "fish",
        "pesce",
        "shrimp",
        "gamberi",
    ),
    "vegan": (
        "chicken",
        "pollo",
        "turkey",
        "tacchino",
        "beef",
        "manzo",
        "pork",
        "maiale",
        "bresaola",
        "prosciutto",
        "ham",
        "meat",
        "carne",
        "salmon",
        "salmone",
        "tuna",
        "tonno",
        "cod",
        "merluzzo",
        "fish",
        "pesce",
        "shrimp",
        "gamberi",
        "egg",
        "uovo",
        "uova",
        "albume",
        "milk",
        "latte",
        "yogurt",
        "cheese",
        "formaggio",
        "mozzarella",
        "parmigiano",
        "ricotta",
        "butter",
        "burro",
        "whey",
        "honey",
        "miele",
    ),
    "pescatarian": (
        "chicken",
        "pollo",
        "turkey",
        "tacchino",
        "beef",
        "manzo",
        "pork",
        "maiale",
        "bresaola",
        "prosciutto",
        "ham",
        "meat",
        "carne",
    ),
}

_CANONICAL_CONSTRAINT_ALIASES: dict[str, str] = {
    "glutine": "gluten",
    "celiac": "gluten",
    "celiaco": "gluten",
    "celiachia": "gluten",
    "lattosio": "lactose",
    "latticini": "dairy",
    "latte": "dairy",
    "frutta a guscio": "nuts",
    "frutta secca": "nuts",
    "noci": "nuts",
    "arachidi": "peanuts",
    "uova": "egg",
    "uovo": "egg",
    "soia": "soy",
    "pesce": "fish",
    "crostacei": "shellfish",
    "molluschi": "shellfish",
    "sesamo": "sesame",
    "maiale": "pork",
    "suino": "pork",
    "vegetariano": "vegetarian",
    "vegetariana": "vegetarian",
    "vegano": "vegan",
    "vegana": "vegan",
    "pescetariano": "pescatarian",
    "pescetariana": "pescatarian",
}

_BANNED_CATEGORIES_BY_REGIME: dict[str, frozenset[str]] = {
    "vegetarian": frozenset({"poultry", "red_meat", "fish"}),
    "vegan": frozenset({"poultry", "red_meat", "fish", "egg", "dairy"}),
    "pescatarian": frozenset({"poultry", "red_meat"}),
    "dairy_free": frozenset({"dairy"}),
    "lactose": frozenset({"dairy"}),
    "dairy": frozenset({"dairy"}),
}


class PreferenceContext(BaseModel):
    """Merged dietary rules, frequency learnings, and feedback bias for one person."""

    person_id: str
    dietary_regime: str = "omnivore"
    effective_entries: list[PreferenceEntry] = Field(default_factory=list)
    hard_constraints: list[str] = Field(default_factory=list)
    banned_keywords: list[str] = Field(default_factory=list)
    banned_categories: list[str] = Field(default_factory=list)
    liked_items: list[str] = Field(default_factory=list)
    disliked_items: list[str] = Field(default_factory=list)
    preferred_brands: list[str] = Field(default_factory=list)
    top_frequent_foods: list[str] = Field(default_factory=list)
    recent_meals_last_7d: list[str] = Field(default_factory=list)
    eaten_today_foods: list[str] = Field(default_factory=list)
    eaten_today_categories: list[str] = Field(default_factory=list)
    liked_titles: list[str] = Field(default_factory=list)
    disliked_titles: list[str] = Field(default_factory=list)
    never_suggest_titles: list[str] = Field(default_factory=list)


class PreferenceEngine:
    """Two-tier dietary preference merger and deterministic zero-trust safety gate."""

    def __init__(
        self,
        pref_repo: PreferenceRepository,
        meal_repo: MealRepository,
    ) -> None:
        self._pref_repo = pref_repo
        self._meal_repo = meal_repo

    async def build_context(
        self,
        profile: PersonProfile,
        reference_date: date | None = None,
    ) -> PreferenceContext:
        """Merge global household rules with person overrides and consumption history."""
        ref_date = reference_date or date.today()
        raw_entries = await self._pref_repo.list_preferences(
            profile.person_id, include_global=True
        )
        # Merge global -> person so person strictly overrides global on matching target
        merged_map: dict[tuple[str, str], PreferenceEntry] = {}
        for entry in raw_entries:
            if entry.scope == PreferenceScope.GLOBAL:
                key = (entry.target_type.lower(), normalize_text(entry.target_value))
                merged_map[key] = entry
        for entry in raw_entries:
            if entry.scope == PreferenceScope.PERSON:
                key = (entry.target_type.lower(), normalize_text(entry.target_value))
                merged_map[key] = entry

        effective_entries = list(merged_map.values())

        hard_terms: set[str] = set()
        for raw_term in (
            *profile.allergies,
            *profile.intolerances,
            *profile.blacklist,
        ):
            cleaned = raw_term.strip()
            if cleaned:
                hard_terms.add(cleaned)

        regime = (profile.dietary_regime or "omnivore").strip().lower()
        canonical_regime = _CANONICAL_CONSTRAINT_ALIASES.get(
            normalize_text(regime), normalize_text(regime)
        )
        if canonical_regime and canonical_regime not in ("omnivore", "onnivoro"):
            hard_terms.add(canonical_regime)

        liked_items: list[str] = []
        disliked_items: list[str] = []
        preferred_brands: list[str] = []

        for entry in effective_entries:
            if entry.stance in HARD_STANCES:
                hard_terms.add(entry.target_value)
            elif entry.stance in (PreferenceStance.LIKE, PreferenceStance.LOVE):
                liked_items.append(entry.target_value)
            elif entry.stance == PreferenceStance.DISLIKE:
                disliked_items.append(entry.target_value)
            elif entry.stance == PreferenceStance.PREFERRED_BRAND:
                preferred_brands.append(entry.target_value)

        banned_keywords, banned_categories = self._expand_hard_terms(
            sorted(hard_terms)
        )

        frequent_rows = await self._meal_repo.get_top_frequent_foods(
            profile.person_id, limit=20, min_distinct_days=3
        )
        top_foods: list[str] = []
        for row in frequent_rows:
            name = str(row.get("display_name") or "").strip()
            brand = row.get("brand")
            if name:
                top_foods.append(f"{name} ({brand})" if brand else name)

        recent_meals = await self._meal_repo.get_recent_meal_titles(
            profile.person_id, days=7, reference_date=ref_date
        )
        eaten_today_foods, eaten_today_categories = (
            await self._meal_repo.get_eaten_today_foods(profile.person_id, ref_date)
        )
        feedbacks = await self._pref_repo.list_feedback(profile.person_id)

        liked_titles: list[str] = []
        disliked_titles: list[str] = []
        never_suggest: list[str] = []
        for fb in feedbacks:
            if fb.rating == MealFeedbackRating.LIKE:
                liked_titles.append(fb.title)
            elif fb.rating == MealFeedbackRating.DISLIKE:
                disliked_titles.append(fb.title)
            elif fb.rating == MealFeedbackRating.NEVER_SUGGEST:
                never_suggest.append(fb.title)

        return PreferenceContext(
            person_id=profile.person_id,
            dietary_regime=profile.dietary_regime,
            effective_entries=effective_entries,
            hard_constraints=sorted(hard_terms),
            banned_keywords=sorted(banned_keywords),
            banned_categories=sorted(banned_categories),
            liked_items=liked_items,
            disliked_items=disliked_items,
            preferred_brands=preferred_brands,
            top_frequent_foods=top_foods,
            recent_meals_last_7d=recent_meals,
            eaten_today_foods=eaten_today_foods,
            eaten_today_categories=eaten_today_categories,
            liked_titles=liked_titles,
            disliked_titles=disliked_titles,
            never_suggest_titles=never_suggest,
        )

    @staticmethod
    def _expand_hard_terms(terms: list[str]) -> tuple[set[str], set[str]]:
        keywords: set[str] = set()
        categories: set[str] = set()

        for term in terms:
            norm = normalize_text(term)
            if not norm:
                continue
            keywords.add(norm)
            canonical = _CANONICAL_CONSTRAINT_ALIASES.get(norm, norm)
            keywords.add(canonical)
            if canonical in _SYNONYM_MAP:
                for kw in _SYNONYM_MAP[canonical]:
                    keywords.add(normalize_text(kw))
            if canonical in _BANNED_CATEGORIES_BY_REGIME:
                categories.update(_BANNED_CATEGORIES_BY_REGIME[canonical])
            if norm in _BANNED_CATEGORIES_BY_REGIME:
                categories.update(_BANNED_CATEGORIES_BY_REGIME[norm])
        return keywords, categories

    @staticmethod
    def check_hard_constraints(
        *,
        title: str,
        ingredient_names: list[str],
        resolved_foods: list[EffectiveFood | None] | None = None,
        context: PreferenceContext,
    ) -> list[str]:
        """Deterministically verify that no allergen, blacklist, or banned regime slips in."""
        violations: list[str] = []
        norm_never = {normalize_text(t) for t in context.never_suggest_titles if normalize_text(t)}
        norm_title = normalize_text(title)
        if norm_title and norm_title in norm_never:
            violations.append(f"never_suggest:{title}")

        banned_kws = [kw for kw in context.banned_keywords if kw]
        banned_cats = {c.lower() for c in context.banned_categories}

        if banned_kws:
            for kw in banned_kws:
                if _word_match(kw, norm_title):
                    violations.append(f"title_matches_forbidden:{kw}")

            for name in ingredient_names:
                norm_ing = normalize_text(name)
                for kw in banned_kws:
                    if _word_match(kw, norm_ing):
                        violations.append(f"ingredient:{name}->forbidden:{kw}")

        if resolved_foods:
            for food in resolved_foods:
                if food is None:
                    continue
                if food.category.lower() in banned_cats:
                    violations.append(
                        f"category:{food.name}({food.category})->forbidden_regime"
                    )
                text_pool = " ".join(
                    filter(
                        None,
                        [
                            normalize_text(food.name),
                            normalize_text(food.name_it or ""),
                            normalize_text(food.brand or ""),
                            *food.aliases,
                        ],
                    )
                )
                for kw in banned_kws:
                    if _word_match(kw, text_pool):
                        violations.append(f"food:{food.name}->forbidden:{kw}")

        # Deduplicate while preserving order
        return list(dict.fromkeys(violations))


def _word_match(keyword: str, haystack: str) -> bool:
    """Word-boundary match preventing false positives (e.g. 'noce' vs 'nocera')."""
    if not keyword or not haystack:
        return False
    pattern = rf"(?:^|\b){re.escape(keyword)}(?:\b|$)"
    return bool(re.search(pattern, haystack))
