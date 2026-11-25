"""Structured recipe & meal plan schemas and prompt builders for Phase 2 Chef."""

from __future__ import annotations

from pydantic import BaseModel, Field

from custom_components.ha_calories_buddy.core.models import (
    IngredientRole,
    Macros,
    MealSlotConfig,
    PersonProfile,
    PlanGenerationOptions,
    PreparationState,
)
from custom_components.ha_calories_buddy.core.planner.preferences import (
    PreferenceContext,
)
from custom_components.ha_calories_buddy.core.planner.slot_resolver import (
    canonical_slot_group,
    effective_slot_group,
    is_sweet_default_slot_group,
)
from custom_components.ha_calories_buddy.core.storage.seeder import normalize_text

_GENERIC_HINT_FILLERS = frozenset(
    {
        "veloce",
        "velocemente",
        "rapido",
        "rapida",
        "quick",
        "fast",
        "leggero",
        "leggera",
        "light",
        "semplice",
        "simple",
        "easy",
        "facile",
        "voglia",
        "qualcosa",
        "pasto",
        "spuntino",
        "merenda",
        "serale",
        "notturno",
        "cena",
        "pranzo",
        "colazione",
        "breakfast",
        "lunch",
        "dinner",
        "snack",
        "minuti",
        "minutes",
        "something",
        "con",
        "with",
        "senza",
        "without",
        "dolce",
        "sweet",
        "salato",
        "salata",
        "savory",
        "salty",
    }
)

_SAVORY_HINT_TERMS = frozenset(
    {
        "salato",
        "salata",
        "savory",
        "salty",
        "pollo",
        "tacchino",
        "manzo",
        "carne",
        "bresaola",
        "prosciutto",
        "salumi",
        "affettati",
        "tonno",
        "salmone",
        "merluzzo",
        "pesce",
        "uova",
        "uovo",
        "frittata",
        "panino",
        "toast salato",
        "pasta",
        "riso",
        "legumi",
        "lenticchie",
        "ceci",
        "insalata",
        "verdure",
        "chicken",
        "turkey",
        "beef",
        "ham",
        "meat",
        "fish",
        "tuna",
        "salmon",
        "eggs",
        "egg",
        "sandwich",
        "wrap",
        "salad",
        "vegetables",
    }
)


def extract_hint_tokens(user_hint: str) -> set[str]:
    """Extract non-generic food/theme tokens (len >= 4) from a user hint."""
    norm = normalize_text(user_hint)
    return {
        tok
        for tok in norm.split()
        if len(tok) >= 4 and tok not in _GENERIC_HINT_FILLERS
    }


def is_savory_hint(user_hint: str) -> bool:
    """Return True when the user explicitly requests a savory or meat/fish/pasta dish."""
    norm = normalize_text(user_hint)
    if not norm:
        return False
    tokens = set(norm.split())
    return any(
        term in tokens or (" " in term and term in norm)
        for term in _SAVORY_HINT_TERMS
    )


class PlannedIngredientSpec(BaseModel):
    """Single ingredient returned by LLM prior to deterministic macro scaling."""

    name: str = Field(description="Clean ingredient name in user's language or English")
    brand: str | None = Field(default=None, description="Preferred brand if applicable")
    grams: float = Field(gt=0.0, le=2000.0, description="Initial culinary quantity in grams")
    preparation_state: PreparationState = Field(default=PreparationState.RAW)
    role: IngredientRole = Field(default=IngredientRole.PRODUCE_FIXED)
    course: str | None = Field(
        default=None,
        description=(
            "Optional meal course label for multi-component meals, e.g. "
            "'Primo', 'Secondo', 'Contorno', 'Pane / Carbo', 'Frutta / Extra' "
            "(or English 'First Course', 'Main', 'Side', 'Bread / Carbs', 'Fruit / Extra')."
        ),
    )


class PlannedMealSkeleton(BaseModel):
    """Structured recipe/meal skeleton returned by LLM."""

    meal_slot: str = Field(description="Exact slot name from allowed meal slots")
    title: str = Field(min_length=2, description="Short appetizing dish title")
    prep_minutes: int = Field(default=20, ge=0, le=180)
    steps: list[str] = Field(default_factory=list, description="Short concise preparation steps")
    tips: str = Field(default="", description="Optional short cooking/substitution tip")
    ingredients: list[PlannedIngredientSpec] = Field(min_length=1)


class MealSuggestionListSchema(BaseModel):
    """Structured output for on-demand suggest_meal."""

    suggestions: list[PlannedMealSkeleton] = Field(min_length=1, max_length=4)


class DayPlanProposalSchema(BaseModel):
    """Structured output for a full day's meals."""

    meals: list[PlannedMealSkeleton] = Field(min_length=1)


class SingleMealSwapSchema(BaseModel):
    """Structured output for single-slot swap or intra-day rebalance replacement."""

    meal: PlannedMealSkeleton


def _format_preference_block(
    ctx: PreferenceContext,
    user_hint: str = "",
) -> str:
    hint_tokens = extract_hint_tokens(user_hint)
    hint_norm = normalize_text(user_hint)

    def _matches_user_hint(food_name: str) -> bool:
        if not hint_tokens and not hint_norm:
            return False
        fnorm = normalize_text(food_name)
        if not fnorm:
            return False
        if fnorm in hint_norm or hint_norm in fnorm:
            return True
        ftokens = {tok for tok in fnorm.split() if len(tok) >= 4}
        return bool(hint_tokens and (ftokens & hint_tokens))

    parts: list[str] = [f"Dietary regime: {ctx.dietary_regime or 'omnivore'}."]
    if ctx.hard_constraints:
        parts.append(
            "STRICTLY FORBIDDEN (allergies/intolerances/blacklist - NEVER include): "
            + ", ".join(ctx.hard_constraints)
            + "."
        )
    if ctx.never_suggest_titles:
        parts.append(
            "Never suggest these dishes: " + ", ".join(ctx.never_suggest_titles[:10]) + "."
        )
    if ctx.disliked_items or ctx.disliked_titles:
        disliked = [*ctx.disliked_items[:12], *ctx.disliked_titles[:8]]
        parts.append("Avoid dislikes: " + ", ".join(disliked) + ".")
    if ctx.liked_items or ctx.liked_titles:
        liked = [*ctx.liked_items[:12], *ctx.liked_titles[:8]]
        parts.append("Prioritize favorites: " + ", ".join(liked) + ".")
    if ctx.preferred_brands:
        parts.append("Preferred brands: " + ", ".join(ctx.preferred_brands[:8]) + ".")
    filtered_eaten = [
        f for f in ctx.eaten_today_foods if not _matches_user_hint(f)
    ]
    if filtered_eaten:
        parts.append(
            "ALREADY EATEN TODAY — DO NOT repeat these main foods or protein/carb bases "
            "in your proposals unless explicitly requested by the user: "
            + ", ".join(filtered_eaten[:12])
            + "."
        )
    if hint_tokens and len(filtered_eaten) < len(ctx.eaten_today_foods):
        parts.append(
            "Note: User explicitly asked for a craving/ingredient in `User request/hint` — "
            "ignore same-day avoidance for those requested items and honor the craving cleanly."
        )
    savory_cats = [
        c
        for c in ctx.eaten_today_categories
        if c in {"pasta", "rice", "legumes", "poultry", "fish", "red_meat", "egg", "potatoes"}
    ]
    if savory_cats:
        parts.append(
            "Primary categories already eaten earlier today (favor different categories): "
            + ", ".join(savory_cats)
            + "."
        )
    if ctx.top_frequent_foods:
        parts.append(
            "Occasionally enjoyed staples (optional inspiration only — prioritize variety "
            "and NEVER repeat today's foods): "
            + ", ".join(ctx.top_frequent_foods[:12])
            + "."
        )
    if ctx.recent_meals_last_7d:
        parts.append(
            "Recent meals (vary from these when possible): "
            + ", ".join(ctx.recent_meals_last_7d[:8])
            + "."
        )
    return "\n".join(parts)


def build_suggest_meal_prompts(
    *,
    profile: PersonProfile,
    ctx: PreferenceContext,
    meal_slot: str,
    target_macros: Macros,
    max_cook_minutes: int,
    user_hint: str = "",
    count: int = 3,
    vault_catalog_hint: str = "",
) -> tuple[str, str]:
    """Build prompts asking the Chef LLM for 1-3 tailored meal options."""
    slot_group = canonical_slot_group(meal_slot)
    is_snack_or_breakfast = is_sweet_default_slot_group(slot_group)
    wants_savory = is_savory_hint(user_hint)
    hint_tokens = extract_hint_tokens(user_hint)
    has_craving = bool(user_hint.strip() and hint_tokens)

    is_large_meal = (not is_snack_or_breakfast) and target_macros.kcal >= 550.0
    course_guidance = (
        "MULTI-COURSE STRUCTURE (budget >= 550 kcal): Design each option as a complete, "
        "satisfying balanced meal rather than a tiny single plate — combine realistic courses "
        "(e.g. a main protein dish + quick vegetable side + bread/potatoes/grains + optional fruit "
        "or cheese/nuts), tagging each ingredient with its `course` ('Secondo', 'Contorno', "
        "'Pane / Carbo', 'Frutta / Extra', etc.). Use quick/parallel or no-cook sides (mixed salad, "
        "cherry tomatoes, whole-grain bread, fresh fruit) so total prep still fits max_cook_minutes.\n"
        if is_large_meal
        else ""
    )

    slot_style_guidance = ""
    if is_snack_or_breakfast and not wants_savory:
        slot_style_guidance = (
            f"SLOT STYLE ({meal_slot.upper()} — SWEET / LIGHT SNACK BY DEFAULT): "
            "Propose ONLY quick, light, sweet or sweet-neutral snack/breakfast creations "
            "(e.g. Greek yogurt, ricotta, cottage cheese, fresh fruit/berries/banana, oats/granola, "
            "fette biscottate/rusks, whole-grain toast, peanut/almond butter, nuts, dark chocolate, honey, whey). "
            "NEVER propose cured/deli meats (no bresaola, prosciutto, ham), heavy cooked lunch/dinner entrees "
            "(no chicken, turkey, beef, salmon, tuna, cod, pasta, rice entrees, legumes), or savory vegetables "
            "(no cucumbers, fennel, carrots, broccoli, peppers, mushrooms).\n"
        )
    elif is_snack_or_breakfast and wants_savory:
        slot_style_guidance = (
            f"SLOT STYLE ({meal_slot.upper()} — QUICK SAVORY SNACK): "
            "Keep each option a fast, portable light snack rather than a multi-course dinner.\n"
        )

    craving_guidance = (
        "USER CRAVING IS MANDATORY ACROSS ALL OPTIONS: The user specifically requested "
        f"'{user_hint.strip()}'. EVERY returned option MUST center around or prominently incorporate "
        "that requested request/craving — differentiate the options through creative complementary pairings "
        "(e.g., different fruits, crunch/rusks/oats, nuts/chocolate, or preparation style) instead of "
        "abandoning the user's craving on 2 out of 3 options.\n"
        if has_craving and count >= 2
        else (
            f"USER CRAVING PRIORITY: Strictly center the option around '{user_hint.strip()}'.\n"
            if has_craving
            else ""
        )
    )

    if count >= 2:
        if has_craving:
            diversity_guidance = (
                f"INDEPENDENT ALTERNATIVE OPTIONS: Return {count} distinct alternative snack/meal variants "
                "(the user will pick ONE option, NOT all together). Keep the user's requested craving at the center "
                "of every option while varying the secondary bases/toppings so no two options feel identical.\n"
            )
        elif is_snack_or_breakfast and not wants_savory:
            diversity_guidance = (
                f"INDEPENDENT ALTERNATIVE OPTIONS: Return {count} completely independent sweet/light snack options "
                "(the user will pick ONE option to eat, NOT all options together). "
                "Vary the protein/dairy base and carb/fruit toppings across options "
                "(e.g., option 1: Greek yogurt + berries + oats; option 2: ricotta + pear + dark chocolate + fette biscottate; "
                "option 3: cottage cheese / whey smoothie bowl + banana + almonds).\n"
            )
        else:
            diversity_guidance = (
                f"INDEPENDENT ALTERNATIVE OPTIONS: Return {count} completely independent meal choices "
                "(the user will pick ONE option to eat, NOT all options together). "
                "ALL options MUST use completely distinct primary protein sources AND distinct primary "
                "carbohydrate bases (e.g., option 1: fish + bread/potatoes; option 2: lean poultry/meat + "
                "rice/farro/quinoa; option 3: eggs/cheese/legumes/tofu + crusty bread/grains). NEVER suggest "
                "multiple pasta-based or legume-based options in the same response.\n"
            )
    else:
        diversity_guidance = ""

    vault_section = (
        f"\nVerified Food Vault ingredients (prefer exact names when fitting):\n{vault_catalog_hint}\n"
        if vault_catalog_hint
        else ""
    )
    system_prompt = (
        "You are an expert culinary nutrition chef designing practical home-cooked meals.\n"
        f"Output language for titles, steps, and `course` labels: {profile.language}.\n"
        "Use simple real whole ingredients (single food names without recipe adjectives like "
        "'condito' inside the ingredient name) with realistic starting weights in grams "
        "and accurate preparation_state ('raw', 'cooked', or 'ready_to_eat').\n"
        "Classify each ingredient's `role`: 'protein_anchor', 'carb_anchor', 'fat_anchor', "
        "'produce_fixed', or 'condiment_fixed'.\n"
        f"{slot_style_guidance}"
        f"{craving_guidance}"
        f"{diversity_guidance}"
        f"{course_guidance}"
        "Python will algebraically scale the anchor ingredients to hit exact macros for EACH option, "
        "so design a genuinely appetizing ~100%-budget meal per option.\n"
        "NEVER violate dietary regime, allergies, intolerances, or blacklist.\n"
        f"{_format_preference_block(ctx, user_hint=user_hint)}"
        f"{vault_section}"
    )
    hint_line = f"\nUser request/hint: {user_hint.strip()}" if user_hint.strip() else ""
    user_prompt = (
        f"Propose {count} distinct alternative meal options for meal_slot='{meal_slot}'.\n"
        f"Each individual option should target approx: {round(target_macros.kcal)} kcal | "
        f"P {round(target_macros.protein_g)}g | C {round(target_macros.carbs_g)}g | "
        f"F {round(target_macros.fat_g)}g.\n"
        f"Maximum cooking time per option: {max_cook_minutes} minutes.{hint_line}"
    )
    return system_prompt, user_prompt


def build_day_plan_prompts(
    *,
    profile: PersonProfile,
    ctx: PreferenceContext,
    plan_date_str: str,
    slots: list[MealSlotConfig],
    slot_targets: dict[str, Macros],
    options: PlanGenerationOptions,
    previous_titles: list[str] | None = None,
    vault_catalog_hint: str = "",
) -> tuple[str, str]:
    """Build prompts to generate one complete day's meals respecting per-slot budgets."""
    wants_savory_snacks = is_savory_hint(options.notes)
    slot_lines: list[str] = []
    has_large_slot = False
    for s in slots:
        max_min = options.max_cook_overrides.get(s.name, s.max_cook_minutes)
        grp = effective_slot_group(s, slots)
        t = slot_targets.get(s.name)
        is_sweet = is_sweet_default_slot_group(grp) and not wants_savory_snacks
        if grp == "breakfast" and not wants_savory_snacks:
            style_tag = " [SWEET/LIGHT BREAKFAST: yogurt/oats/fruit/rusks/ricotta/nuts; NO meat/deli/fish/savory veggies]"
        elif is_sweet:
            style_tag = (
                " [SWEET/LIGHT SNACK (2-5 min): Greek yogurt, fruit/berries/banana, "
                "fette biscottate, oats, nut butter/nuts, ricotta, dark chocolate, honey; "
                "NEVER bresaola, prosciutto, chicken, beef, fish, legumes, pasta, rice, or savory veggies]"
            )
        elif not is_sweet_default_slot_group(grp) and t is not None and t.kcal >= 550.0:
            has_large_slot = True
            style_tag = " [COMPLETE MULTI-COURSE MAIN MEAL: tag ingredient `course`]"
        else:
            style_tag = " [COMPLETE BALANCED MEAL]"

        if t is not None:
            slot_lines.append(
                f"- {s.name} (max {max_min} min): ~{round(t.kcal)} kcal, "
                f"P {round(t.protein_g)}g, C {round(t.carbs_g)}g, F {round(t.fat_g)}g{style_tag}"
            )
        else:
            slot_lines.append(f"- {s.name} (max {max_min} min){style_tag}")

    multi_course_line = (
        "For large main meals (>= 550 kcal), compose complete balanced courses (e.g. main + vegetable side "
        "+ bread/starch + optional fruit) and tag each ingredient's `course` ('Secondo', 'Contorno', "
        "'Pane / Carbo', 'Frutta / Extra', etc.).\n"
        if has_large_slot
        else ""
    )
    vault_section = (
        f"\nVerified Food Vault ingredients (prefer exact names when fitting):\n{vault_catalog_hint}\n"
        if vault_catalog_hint
        else ""
    )
    system_prompt = (
        "You are a precision personal dietitian & chef planning one full day of practical meals.\n"
        f"Output language for titles, steps, and `course` labels: {profile.language}.\n"
        "Generate exactly ONE meal for EVERY listed meal_slot matching its exact name.\n"
        "Each meal uses concrete whole ingredients in grams, explicit preparation_state, "
        "and accurate role ('protein_anchor', 'carb_anchor', 'fat_anchor', 'produce_fixed', 'condiment_fixed').\n"
        "For dry grains/pasta/legumes and raw meat/fish matching `(Raw)`/`(Crudo)` Vault items, specify RAW weights "
        "with `preparation_state='raw'` (never cooked weights under a raw food name).\n"
        "Lunch and Dinner on the SAME day MUST use completely different primary protein sources AND distinct carb bases.\n"
        "Set realistic `prep_minutes` (2-5 for cold snacks, 5-10 for breakfast, 15-30 for cooked meals) and always provide 2-4 concise `steps`.\n"
        f"{multi_course_line}"
        "NEVER include any forbidden allergen, intolerance, or non-compliant food.\n"
        f"{_format_preference_block(ctx)}"
        f"{vault_section}"
    )
    avoid_line = (
        f"\nAvoid repeating dishes already chosen this week: {', '.join(previous_titles[-12:])}."
        if previous_titles
        else ""
    )
    notes_line = f"\nAdditional notes: {options.notes.strip()}" if options.notes.strip() else ""
    prep_line = (
        "\nMeal-prep friendly mode is ON: favor batch-cookable recipes whose proteins/grains keep well."
        if options.meal_prep_mode
        else ""
    )
    user_prompt = (
        f"Plan date: {plan_date_str}\nMeal slots & macro targets:\n"
        + "\n".join(slot_lines)
        + prep_line
        + avoid_line
        + notes_line
    )
    return system_prompt, user_prompt


def build_swap_meal_prompts(
    *,
    profile: PersonProfile,
    ctx: PreferenceContext,
    meal_slot: str,
    target_macros: Macros,
    max_cook_minutes: int,
    current_title: str,
    user_instruction: str = "",
    vault_catalog_hint: str = "",
) -> tuple[str, str]:
    """Build prompts to swap or rebalance a single meal slot."""
    slot_group = canonical_slot_group(meal_slot)
    is_snack_or_breakfast = is_sweet_default_slot_group(slot_group)
    wants_savory = is_savory_hint(user_instruction)

    slot_style_guidance = ""
    if is_snack_or_breakfast and not wants_savory:
        slot_style_guidance = (
            f"SLOT STYLE ({meal_slot.upper()} — SWEET / LIGHT SNACK BY DEFAULT): "
            "Propose a quick, light, sweet or sweet-neutral snack/breakfast creation "
            "(Greek yogurt, ricotta, fruit/berries/banana, oats, fette biscottate, nut butter, dark chocolate, honey). "
            "NEVER propose bresaola, prosciutto, meat, fish, legumes, pasta, rice, or savory vegetables.\n"
        )
    multi_course_line = (
        "Since budget >= 550 kcal, compose a complete multi-course meal and tag each ingredient's `course`.\n"
        if (not is_snack_or_breakfast) and target_macros.kcal >= 550.0
        else ""
    )
    vault_section = (
        f"\nVerified Food Vault ingredients (prefer exact names when fitting):\n{vault_catalog_hint}\n"
        if vault_catalog_hint
        else ""
    )
    system_prompt = (
        "You are a personal chef creating a compliant replacement dish for one meal slot.\n"
        f"Output language for title, steps, and `course` labels: {profile.language}.\n"
        "Set meal_slot to the exact requested slot name.\n"
        "Include concrete ingredients in grams, accurate preparation_state ('raw' for dry grains/pasta matching raw Vault foods), "
        "ingredient roles, realistic `prep_minutes`, and 2-4 concise `steps`.\n"
        f"{slot_style_guidance}"
        f"{multi_course_line}"
        "NEVER violate allergies, intolerances, blacklist, or dietary regime.\n"
        f"{_format_preference_block(ctx, user_hint=user_instruction)}"
        f"{vault_section}"
    )
    instr = (
        f"User instruction: {user_instruction.strip()}"
        if user_instruction.strip()
        else "Provide a fresh, distinct alternative."
    )
    user_prompt = (
        f"Replace '{current_title}' in meal_slot='{meal_slot}' (max {max_cook_minutes} min).\n"
        f"New target budget: ~{round(target_macros.kcal)} kcal | "
        f"P {round(target_macros.protein_g)}g | C {round(target_macros.carbs_g)}g | "
        f"F {round(target_macros.fat_g)}g.\n"
        f"{instr}"
    )
    return system_prompt, user_prompt
