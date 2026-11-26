"""SQLite repository for homemade composite recipes and their resolved ingredients."""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite

from custom_components.ha_calories_buddy.core.models import (
    HomemadeRecipe,
    Macros,
    PreparationState,
    RecipeIngredient,
)
from custom_components.ha_calories_buddy.core.nutrition.calculator import sum_macros
from custom_components.ha_calories_buddy.core.storage.database import Database
from custom_components.ha_calories_buddy.core.storage.seeder import normalize_text


def compute_recipe_breakdown(
    total_macros: Macros,
    effective_weight_g: float,
    servings: float,
) -> tuple[float, Macros, Macros]:
    """Compute grams per serving, macros per 100g, and macros per serving."""
    safe_weight = max(1.0, float(effective_weight_g))
    safe_servings = max(0.01, float(servings))
    scale_100 = 100.0 / safe_weight
    scale_srv = 1.0 / safe_servings

    grams_per_serving = round(safe_weight * scale_srv, 1)
    macros_per_100g = Macros(
        kcal=round(total_macros.kcal * scale_100, 1),
        protein_g=round(total_macros.protein_g * scale_100, 1),
        carbs_g=round(total_macros.carbs_g * scale_100, 1),
        fat_g=round(total_macros.fat_g * scale_100, 1),
        fiber_g=round((total_macros.fiber_g or 0.0) * scale_100, 1),
    )
    macros_per_serving = Macros(
        kcal=round(total_macros.kcal * scale_srv, 1),
        protein_g=round(total_macros.protein_g * scale_srv, 1),
        carbs_g=round(total_macros.carbs_g * scale_srv, 1),
        fat_g=round(total_macros.fat_g * scale_srv, 1),
        fiber_g=round((total_macros.fiber_g or 0.0) * scale_srv, 1),
    )
    return grams_per_serving, macros_per_100g, macros_per_serving


class RecipeRepository:
    """Repository managing homemade composite recipes and ingredient rows."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def save_recipe(self, recipe: HomemadeRecipe) -> HomemadeRecipe:
        """Insert or update a recipe and replace its ingredient list atomically."""
        now_iso = datetime.now(UTC).isoformat()
        created_iso = (
            recipe.created_at.isoformat() if recipe.created_at else now_iso
        )
        norm_name = normalize_text(recipe.name)

        async with self._db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO recipes (
                    recipe_id, food_id, name, normalized_name, category,
                    servings, serving_unit, raw_total_grams, final_weight_g,
                    effective_weight_g, allow_in_planner, notes,
                    created_by_person_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(recipe_id) DO UPDATE SET
                    food_id = excluded.food_id,
                    name = excluded.name,
                    normalized_name = excluded.normalized_name,
                    category = excluded.category,
                    servings = excluded.servings,
                    serving_unit = excluded.serving_unit,
                    raw_total_grams = excluded.raw_total_grams,
                    final_weight_g = excluded.final_weight_g,
                    effective_weight_g = excluded.effective_weight_g,
                    allow_in_planner = excluded.allow_in_planner,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (
                    recipe.recipe_id,
                    recipe.food_id,
                    recipe.name,
                    norm_name,
                    recipe.category,
                    float(recipe.servings),
                    recipe.serving_unit,
                    float(recipe.raw_total_grams),
                    (
                        float(recipe.final_weight_g)
                        if recipe.final_weight_g is not None
                        else None
                    ),
                    float(recipe.effective_weight_g),
                    1 if recipe.allow_in_planner else 0,
                    recipe.notes,
                    recipe.created_by_person_id,
                    created_iso,
                    now_iso,
                ),
            )

            await conn.execute(
                "DELETE FROM recipe_ingredients WHERE recipe_id = ?",
                (recipe.recipe_id,),
            )

            for idx, ing in enumerate(recipe.ingredients):
                await conn.execute(
                    """
                    INSERT INTO recipe_ingredients (
                        ingredient_id, recipe_id, sort_order, food_id, display_name,
                        input_quantity, input_unit, input_state, grams,
                        kcal, protein, carbs, fat, fiber
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ing.ingredient_id,
                        recipe.recipe_id,
                        idx,
                        ing.food_id,
                        ing.display_name,
                        float(ing.input_quantity),
                        ing.input_unit,
                        ing.input_state.value,
                        float(ing.grams),
                        float(ing.macros.kcal),
                        float(ing.macros.protein_g),
                        float(ing.macros.carbs_g),
                        float(ing.macros.fat_g),
                        ing.macros.fiber_g,
                    ),
                )

        stored = await self.get_recipe(recipe.recipe_id)
        return stored or recipe

    async def get_recipe(self, recipe_id: str) -> HomemadeRecipe | None:
        """Fetch a single recipe and its ingredients by recipe_id."""
        row = await self._db.fetch_one(
            "SELECT * FROM recipes WHERE recipe_id = ?",
            (recipe_id,),
        )
        if row is None:
            return None
        items = await self._hydrate_rows([row])
        return items[0] if items else None

    async def get_recipe_by_food_id(self, food_id: str) -> HomemadeRecipe | None:
        """Fetch a recipe linked to a given Food Vault food_id."""
        row = await self._db.fetch_one(
            "SELECT * FROM recipes WHERE food_id = ?",
            (food_id,),
        )
        if row is None:
            return None
        items = await self._hydrate_rows([row])
        return items[0] if items else None

    async def list_recipes(
        self,
        *,
        query: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[HomemadeRecipe], int]:
        """Search or list homemade recipes ordered by most recently updated."""
        safe_limit = max(1, min(100, int(limit)))
        safe_offset = max(0, int(offset))
        norm_q = normalize_text(query)

        where_sql = ""
        params: list[str | int] = []
        if norm_q:
            tokens = [t for t in norm_q.split() if len(t) >= 2] or [norm_q]
            clauses: list[str] = []
            for tok in tokens[:5]:
                pat = f"%{tok}%"
                clauses.append("(normalized_name LIKE ? OR LOWER(notes) LIKE ?)")
                params.extend([pat, pat])
            where_sql = "WHERE " + " AND ".join(clauses)

        count_row = await self._db.fetch_one(
            f"SELECT COUNT(*) AS cnt FROM recipes {where_sql}",
            tuple(params),
        )
        total = int(count_row["cnt"]) if count_row is not None else 0
        rows = await self._db.fetch_all(
            f"""
            SELECT * FROM recipes
            {where_sql}
            ORDER BY updated_at DESC, name ASC
            LIMIT ? OFFSET ?
            """,
            tuple([*params, safe_limit, safe_offset]),
        )
        return await self._hydrate_rows(rows), total

    async def list_planner_allowed_food_ids(self) -> set[str]:
        """Return food_ids of homemade recipes explicitly opted into AI meal planning."""
        rows = await self._db.fetch_all(
            "SELECT food_id FROM recipes WHERE allow_in_planner = 1"
        )
        return {str(row["food_id"]) for row in rows}

    async def delete_recipe(self, recipe_id: str) -> str | None:
        """Delete a recipe record (cascading ingredients) and return its linked food_id."""
        row = await self._db.fetch_one(
            "SELECT food_id FROM recipes WHERE recipe_id = ?",
            (recipe_id,),
        )
        if row is None:
            return None
        food_id = str(row["food_id"])
        async with self._db.transaction() as conn:
            await conn.execute(
                "DELETE FROM recipes WHERE recipe_id = ?",
                (recipe_id,),
            )
        return food_id

    async def _hydrate_rows(
        self, rows: list[aiosqlite.Row]
    ) -> list[HomemadeRecipe]:
        if not rows:
            return []
        recipe_ids = [str(r["recipe_id"]) for r in rows]
        placeholders = ",".join("?" for _ in recipe_ids)
        ing_rows = await self._db.fetch_all(
            f"""
            SELECT * FROM recipe_ingredients
            WHERE recipe_id IN ({placeholders})
            ORDER BY sort_order ASC
            """,
            tuple(recipe_ids),
        )
        ings_by_recipe: dict[str, list[RecipeIngredient]] = {}
        for ir in ing_rows:
            ings_by_recipe.setdefault(str(ir["recipe_id"]), []).append(
                RecipeIngredient(
                    ingredient_id=str(ir["ingredient_id"]),
                    recipe_id=str(ir["recipe_id"]),
                    sort_order=int(ir["sort_order"]),
                    food_id=ir["food_id"],
                    display_name=str(ir["display_name"]),
                    input_quantity=float(ir["input_quantity"]),
                    input_unit=str(ir["input_unit"]),
                    input_state=PreparationState(str(ir["input_state"])),
                    grams=float(ir["grams"]),
                    macros=Macros(
                        kcal=float(ir["kcal"]),
                        protein_g=float(ir["protein"]),
                        carbs_g=float(ir["carbs"]),
                        fat_g=float(ir["fat"]),
                        fiber_g=(
                            float(ir["fiber"])
                            if ir["fiber"] is not None
                            else None
                        ),
                    ),
                )
            )

        result: list[HomemadeRecipe] = []
        for row in rows:
            rid = str(row["recipe_id"])
            ings = ings_by_recipe.get(rid, [])
            total_macros = sum_macros([ing.macros for ing in ings])
            servings = float(row["servings"])
            eff_weight = float(row["effective_weight_g"])
            grams_srv, per_100, per_srv = compute_recipe_breakdown(
                total_macros, eff_weight, servings
            )
            result.append(
                HomemadeRecipe(
                    recipe_id=rid,
                    food_id=str(row["food_id"]),
                    name=str(row["name"]),
                    normalized_name=str(row["normalized_name"]),
                    category=str(row["category"]),
                    servings=servings,
                    serving_unit=str(row["serving_unit"]),
                    raw_total_grams=float(row["raw_total_grams"]),
                    final_weight_g=(
                        float(row["final_weight_g"])
                        if row["final_weight_g"] is not None
                        else None
                    ),
                    effective_weight_g=eff_weight,
                    grams_per_serving=grams_srv,
                    total_macros=total_macros,
                    macros_per_100g=per_100,
                    macros_per_serving=per_srv,
                    allow_in_planner=bool(row["allow_in_planner"]),
                    notes=str(row["notes"] or ""),
                    created_by_person_id=row["created_by_person_id"],
                    ingredients=ings,
                    created_at=datetime.fromisoformat(str(row["created_at"])),
                    updated_at=datetime.fromisoformat(str(row["updated_at"])),
                )
            )
        return result
