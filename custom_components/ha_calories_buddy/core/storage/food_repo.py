"""Two-tier Food Vault repository (Global Food Vault + Person Food Overrides)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import aiosqlite

from custom_components.ha_calories_buddy.core.models import (
    EffectiveFood,
    FoodItem,
    FoodPortion,
    FoodSource,
    LabelStandard,
    Macros,
    PersonFoodOverride,
    PreparationState,
)
from custom_components.ha_calories_buddy.core.storage.database import Database
from custom_components.ha_calories_buddy.core.storage.seeder import normalize_text

CATEGORY_GROUPS: dict[str, tuple[str, ...]] = {
    "protein": ("poultry", "red_meat", "fish", "egg"),
    "carbs": ("rice", "pasta", "grains", "oats", "bread", "potatoes"),
    "legumes": ("legumes",),
    "dairy": ("dairy",),
    "produce": ("vegetables", "fruit"),
    "fats": ("oil", "nuts"),
    "other": ("snack", "beverage", "other"),
}


class FoodRepository:
    """Repository for global foods, portions, and per-person food overrides."""

    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def _expand_categories(category: str | None) -> tuple[str, ...]:
        if not category:
            return ()
        key = category.strip().lower()
        if not key or key == "all":
            return ()
        return CATEGORY_GROUPS.get(key, (key,))

    async def get_by_id(self, food_id: str) -> FoodItem | None:
        """Fetch a global food item by ID."""
        row = await self._db.fetch_one(
            "SELECT * FROM global_foods WHERE food_id = ?",
            (food_id,),
        )
        if row is None:
            return None
        portions = await self._get_portions_for_food(food_id)
        return self._row_to_food(row, portions)

    async def get_override(
        self, person_id: str, food_id: str
    ) -> PersonFoodOverride | None:
        """Fetch a person's override for a given food if one exists."""
        row = await self._db.fetch_one(
            "SELECT * FROM person_food_overrides WHERE person_id = ? AND food_id = ?",
            (person_id, food_id),
        )
        if row is None:
            return None
        return self._row_to_override(row)

    async def get_effective_food(
        self, food_id: str, person_id: str | None = None
    ) -> EffectiveFood | None:
        """Return a FoodItem merged with any per-person override."""
        food = await self.get_by_id(food_id)
        if food is None:
            return None
        override = await self.get_override(person_id, food_id) if person_id else None
        return self._merge_effective_food(food, override)

    async def get_by_barcode(
        self, ean: str, person_id: str | None = None
    ) -> EffectiveFood | None:
        """Look up a food by EAN/UPC barcode, applying person override if present."""
        clean_ean = ean.strip()
        if not clean_ean:
            return None
        row = await self._db.fetch_one(
            "SELECT * FROM global_foods WHERE barcode_ean = ?",
            (clean_ean,),
        )
        if row is None:
            return None
        portions = await self._get_portions_for_food(row["food_id"])
        food = self._row_to_food(row, portions)
        override = (
            await self.get_override(person_id, food.food_id) if person_id else None
        )
        return self._merge_effective_food(food, override)

    async def list_foods(
        self,
        person_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EffectiveFood]:
        """List foods from the Global Vault merged with optional person overrides."""
        rows = await self._db.fetch_all(
            """
            SELECT * FROM global_foods
            ORDER BY verified DESC, name ASC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return await self._hydrate_rows(rows, person_id)

    async def list_foods_paginated(
        self,
        person_id: str | None = None,
        *,
        query: str = "",
        category: str | None = None,
        limit: int = 20,
        offset: int = 0,
        language: str = "en",
    ) -> tuple[list[EffectiveFood], int]:
        """Search or browse the Global Vault with deterministic pagination and category filtering."""
        safe_limit = max(1, min(100, int(limit)))
        safe_offset = max(0, int(offset))
        categories = self._expand_categories(category)
        norm_q = normalize_text(query)

        if not norm_q:
            where_sql = ""
            params: list[str | int] = []
            if categories:
                placeholders = ",".join("?" for _ in categories)
                where_sql = f"WHERE LOWER(category) IN ({placeholders})"
                params.extend(categories)

            count_row = await self._db.fetch_one(
                f"SELECT COUNT(*) AS cnt FROM global_foods {where_sql}",
                tuple(params),
            )
            total = int(count_row["cnt"]) if count_row is not None else 0
            order_col = (
                "COALESCE(name_it, name)" if language == "it" else "name"
            )
            rows = await self._db.fetch_all(
                f"""
                SELECT * FROM global_foods
                {where_sql}
                ORDER BY verified DESC, {order_col} ASC
                LIMIT ? OFFSET ?
                """,
                tuple([*params, safe_limit, safe_offset]),
            )
            items = await self._hydrate_rows(rows, person_id)
            return items, total

        matches = await self.search(
            query,
            person_id=person_id,
            limit=200,
        )
        if categories:
            cat_set = set(categories)
            matches = [m for m in matches if m.category.lower() in cat_set]
        total = len(matches)
        return matches[safe_offset : safe_offset + safe_limit], total

    async def search(
        self,
        query: str,
        person_id: str | None = None,
        brand: str | None = None,
        limit: int = 20,
    ) -> list[EffectiveFood]:
        """Search foods across names, aliases, brands, and personal preferred aliases."""
        norm_q = normalize_text(query)
        norm_brand = normalize_text(brand) if brand else ""
        if not norm_q and not norm_brand:
            return await self.list_foods(person_id=person_id, limit=limit)

        tokens = [t for t in norm_q.split() if len(t) >= 2] or [norm_q]
        like_clauses: list[str] = []
        params: list[str | int] = []

        for tok in tokens[:5]:
            pattern = f"%{tok}%"
            like_clauses.append(
                "(gf.normalized_name LIKE ? OR gf.aliases_text LIKE ? OR "
                "LOWER(COALESCE(gf.name_it, '')) LIKE ? OR "
                "COALESCE(gf.normalized_brand, '') LIKE ? OR "
                "COALESCE(pfo.preferred_alias, '') LIKE ?)"
            )
            params.extend([pattern, pattern, pattern, pattern, pattern])

        where_sql = " AND ".join(like_clauses)
        if person_id:
            sql = f"""
                SELECT gf.* FROM global_foods gf
                LEFT JOIN person_food_overrides pfo
                    ON gf.food_id = pfo.food_id AND pfo.person_id = ?
                WHERE {where_sql}
                LIMIT 100
            """
            query_params: list[str | int] = [person_id, *params]
        else:
            sql = f"""
                SELECT gf.* FROM global_foods gf
                LEFT JOIN person_food_overrides pfo ON 1 = 0
                WHERE {where_sql}
                LIMIT 100
            """
            query_params = params

        rows = await self._db.fetch_all(sql, tuple(query_params))

        # Also try OR-matching if AND produced zero hits (e.g. extra descriptive words)
        if not rows and len(tokens) > 1:
            or_clauses = " OR ".join(like_clauses)
            if person_id:
                or_sql = f"""
                    SELECT DISTINCT gf.* FROM global_foods gf
                    LEFT JOIN person_food_overrides pfo
                        ON gf.food_id = pfo.food_id AND pfo.person_id = ?
                    WHERE {or_clauses}
                    LIMIT 100
                """
                rows = await self._db.fetch_all(or_sql, tuple([person_id, *params]))
            else:
                or_sql = f"""
                    SELECT DISTINCT gf.* FROM global_foods gf
                    LEFT JOIN person_food_overrides pfo ON 1 = 0
                    WHERE {or_clauses}
                    LIMIT 100
                """
                rows = await self._db.fetch_all(or_sql, tuple(params))

        candidates = await self._hydrate_rows(rows, person_id)
        scored = sorted(
            candidates,
            key=lambda ef: self._score_candidate(ef, norm_q, norm_brand),
            reverse=True,
        )
        return scored[:limit]

    async def find_best_match(
        self,
        name: str,
        brand: str | None = None,
        person_id: str | None = None,
        preferred_state: PreparationState | None = None,
        strict_brand: bool = False,
    ) -> EffectiveFood | None:
        """Find the single best matching food in local Vault/Overrides."""
        candidates = await self.search(name, person_id=person_id, brand=brand, limit=25)
        if not candidates:
            return None

        norm_q = normalize_text(name)
        norm_brand = normalize_text(brand) if brand else ""

        filtered = candidates
        if strict_brand and norm_brand:
            filtered = [
                c
                for c in candidates
                if self._matches_brand(c, norm_brand)
            ]
            if not filtered:
                return None

        best = max(
            filtered,
            key=lambda ef: self._score_candidate(
                ef, norm_q, norm_brand, preferred_state=preferred_state
            ),
        )
        lexical = self._lexical_score(best, norm_q)
        # Require at least 50% lexical overlap (20.0+ out of 40.0+) to avoid false matches
        if norm_q and lexical < 20.0:
            return None
        return best

    async def upsert_global_food(self, food: FoodItem) -> FoodItem:
        """Insert or update a food item and its standard portions in the Global Vault."""
        now = datetime.now(UTC).isoformat()
        norm_name = normalize_text(food.name)
        norm_brand = normalize_text(food.brand) if food.brand else None
        aliases_norm = [normalize_text(a) for a in food.aliases]
        aliases_text = "|".join(dict.fromkeys(a for a in aliases_norm if a))

        async with self._db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO global_foods (
                    food_id, name, name_it, normalized_name, aliases_text,
                    brand, normalized_brand, barcode_ean, category,
                    preparation_state, kcal_per_100g, protein_per_100g,
                    carbs_per_100g, fat_per_100g, fiber_per_100g,
                    label_standard, yield_factor, source, verified,
                    atwater_warning, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(food_id) DO UPDATE SET
                    name = excluded.name,
                    name_it = COALESCE(excluded.name_it, global_foods.name_it),
                    normalized_name = excluded.normalized_name,
                    aliases_text = CASE
                        WHEN excluded.aliases_text = '' THEN global_foods.aliases_text
                        ELSE excluded.aliases_text
                    END,
                    brand = excluded.brand,
                    normalized_brand = excluded.normalized_brand,
                    barcode_ean = COALESCE(excluded.barcode_ean, global_foods.barcode_ean),
                    category = excluded.category,
                    preparation_state = excluded.preparation_state,
                    kcal_per_100g = excluded.kcal_per_100g,
                    protein_per_100g = excluded.protein_per_100g,
                    carbs_per_100g = excluded.carbs_per_100g,
                    fat_per_100g = excluded.fat_per_100g,
                    fiber_per_100g = excluded.fiber_per_100g,
                    label_standard = excluded.label_standard,
                    yield_factor = excluded.yield_factor,
                    source = excluded.source,
                    verified = excluded.verified,
                    atwater_warning = excluded.atwater_warning,
                    updated_at = excluded.updated_at
                """,
                (
                    food.food_id,
                    food.name,
                    food.name_it,
                    norm_name,
                    aliases_text,
                    food.brand,
                    norm_brand,
                    food.barcode_ean,
                    food.category,
                    food.preparation_state.value,
                    food.macros_per_100g.kcal,
                    food.macros_per_100g.protein_g,
                    food.macros_per_100g.carbs_g,
                    food.macros_per_100g.fat_g,
                    food.macros_per_100g.fiber_g,
                    food.label_standard.value,
                    food.yield_factor,
                    food.source.value,
                    1 if food.verified else 0,
                    1 if food.atwater_warning else 0,
                    now,
                ),
            )

            for portion in food.portions:
                await conn.execute(
                    """
                    INSERT INTO food_portions (portion_id, food_id, unit_name, grams, description)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(food_id, unit_name) DO UPDATE SET
                        grams = excluded.grams,
                        description = excluded.description
                    """,
                    (
                        portion.portion_id or str(uuid.uuid4()),
                        food.food_id,
                        portion.unit_name.lower(),
                        portion.grams,
                        portion.description,
                    ),
                )

        saved = await self.get_by_id(food.food_id)
        return saved or food

    async def upsert_person_override(
        self, override: PersonFoodOverride
    ) -> PersonFoodOverride:
        """Insert or update a per-person override for a global food."""
        now = datetime.now(UTC).isoformat()
        portions_json = json.dumps(
            {k.lower(): float(v) for k, v in override.custom_portions.items()}
        )

        async with self._db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO person_food_overrides (
                    override_id, person_id, food_id, preferred_alias,
                    kcal_per_100g, protein_per_100g, carbs_per_100g,
                    fat_per_100g, fiber_per_100g, yield_factor,
                    custom_portions_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(person_id, food_id) DO UPDATE SET
                    preferred_alias = COALESCE(excluded.preferred_alias, person_food_overrides.preferred_alias),
                    kcal_per_100g = COALESCE(excluded.kcal_per_100g, person_food_overrides.kcal_per_100g),
                    protein_per_100g = COALESCE(excluded.protein_per_100g, person_food_overrides.protein_per_100g),
                    carbs_per_100g = COALESCE(excluded.carbs_per_100g, person_food_overrides.carbs_per_100g),
                    fat_per_100g = COALESCE(excluded.fat_per_100g, person_food_overrides.fat_per_100g),
                    fiber_per_100g = COALESCE(excluded.fiber_per_100g, person_food_overrides.fiber_per_100g),
                    yield_factor = COALESCE(excluded.yield_factor, person_food_overrides.yield_factor),
                    custom_portions_json = excluded.custom_portions_json,
                    updated_at = excluded.updated_at
                """,
                (
                    override.override_id,
                    override.person_id,
                    override.food_id,
                    override.preferred_alias,
                    override.kcal_per_100g,
                    override.protein_g_per_100g,
                    override.carbs_g_per_100g,
                    override.fat_g_per_100g,
                    override.fiber_g_per_100g,
                    override.yield_factor,
                    portions_json,
                    now,
                ),
            )

        stored = await self.get_override(override.person_id, override.food_id)
        return stored or override

    async def verify_food(self, food_id: str, verified: bool = True) -> None:
        """Mark a global food as verified (clearing warning when explicitly confirmed)."""
        await self._db.execute(
            """
            UPDATE global_foods
            SET verified = ?, atwater_warning = CASE WHEN ? = 1 THEN 0 ELSE atwater_warning END,
                updated_at = ?
            WHERE food_id = ?
            """,
            (1 if verified else 0, 1 if verified else 0, datetime.now(UTC).isoformat(), food_id),
        )

    async def _get_portions_for_food(self, food_id: str) -> list[FoodPortion]:
        rows = await self._db.fetch_all(
            "SELECT * FROM food_portions WHERE food_id = ?",
            (food_id,),
        )
        return [
            FoodPortion(
                portion_id=row["portion_id"],
                food_id=row["food_id"],
                unit_name=row["unit_name"],
                grams=float(row["grams"]),
                description=row["description"],
            )
            for row in rows
        ]

    async def _hydrate_rows(
        self, rows: list[aiosqlite.Row], person_id: str | None
    ) -> list[EffectiveFood]:
        if not rows:
            return []
        food_ids = [row["food_id"] for row in rows]
        placeholders = ",".join("?" for _ in food_ids)

        portion_rows = await self._db.fetch_all(
            f"SELECT * FROM food_portions WHERE food_id IN ({placeholders})",
            tuple(food_ids),
        )
        portions_by_food: dict[str, list[FoodPortion]] = {}
        for pr in portion_rows:
            portions_by_food.setdefault(pr["food_id"], []).append(
                FoodPortion(
                    portion_id=pr["portion_id"],
                    food_id=pr["food_id"],
                    unit_name=pr["unit_name"],
                    grams=float(pr["grams"]),
                    description=pr["description"],
                )
            )

        overrides_by_food: dict[str, PersonFoodOverride] = {}
        if person_id:
            override_rows = await self._db.fetch_all(
                f"SELECT * FROM person_food_overrides WHERE person_id = ? AND food_id IN ({placeholders})",
                tuple([person_id, *food_ids]),
            )
            for ovr in override_rows:
                overrides_by_food[ovr["food_id"]] = self._row_to_override(ovr)

        result: list[EffectiveFood] = []
        for row in rows:
            fid = row["food_id"]
            food = self._row_to_food(row, portions_by_food.get(fid, []))
            result.append(self._merge_effective_food(food, overrides_by_food.get(fid)))
        return result

    @staticmethod
    def _row_to_food(row: aiosqlite.Row, portions: list[FoodPortion]) -> FoodItem:
        aliases_raw = row["aliases_text"] or ""
        aliases = [a for a in aliases_raw.split("|") if a]
        row_keys = row.keys()
        name_it = row["name_it"] if "name_it" in row_keys else None
        return FoodItem(
            food_id=row["food_id"],
            name=row["name"],
            name_it=name_it,
            normalized_name=row["normalized_name"],
            aliases=aliases,
            brand=row["brand"],
            barcode_ean=row["barcode_ean"],
            category=row["category"],
            preparation_state=PreparationState(row["preparation_state"]),
            macros_per_100g=Macros(
                kcal=float(row["kcal_per_100g"]),
                protein_g=float(row["protein_per_100g"]),
                carbs_g=float(row["carbs_per_100g"]),
                fat_g=float(row["fat_per_100g"]),
                fiber_g=(
                    float(row["fiber_per_100g"])
                    if row["fiber_per_100g"] is not None
                    else None
                ),
            ),
            label_standard=LabelStandard(row["label_standard"]),
            yield_factor=float(row["yield_factor"]),
            source=FoodSource(row["source"]),
            verified=bool(row["verified"]),
            atwater_warning=bool(row["atwater_warning"]),
            portions=portions,
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_override(row: aiosqlite.Row) -> PersonFoodOverride:
        custom_portions_raw = json.loads(row["custom_portions_json"] or "{}")
        return PersonFoodOverride(
            override_id=row["override_id"],
            person_id=row["person_id"],
            food_id=row["food_id"],
            preferred_alias=row["preferred_alias"],
            kcal_per_100g=(
                float(row["kcal_per_100g"]) if row["kcal_per_100g"] is not None else None
            ),
            protein_g_per_100g=(
                float(row["protein_per_100g"])
                if row["protein_per_100g"] is not None
                else None
            ),
            carbs_g_per_100g=(
                float(row["carbs_per_100g"])
                if row["carbs_per_100g"] is not None
                else None
            ),
            fat_g_per_100g=(
                float(row["fat_per_100g"])
                if row["fat_per_100g"] is not None
                else None
            ),
            fiber_g_per_100g=(
                float(row["fiber_per_100g"])
                if row["fiber_per_100g"] is not None
                else None
            ),
            yield_factor=(
                float(row["yield_factor"]) if row["yield_factor"] is not None else None
            ),
            custom_portions={str(k).lower(): float(v) for k, v in custom_portions_raw.items()},
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _merge_effective_food(
        food: FoodItem, override: PersonFoodOverride | None
    ) -> EffectiveFood:
        base_macros = food.macros_per_100g
        food_portions = {p.unit_name.lower(): p.grams for p in food.portions}

        if override is None:
            return EffectiveFood(
                food_id=food.food_id,
                name=food.name,
                name_it=food.name_it,
                normalized_name=food.normalized_name,
                aliases=food.aliases,
                brand=food.brand,
                barcode_ean=food.barcode_ean,
                category=food.category,
                preparation_state=food.preparation_state,
                macros_per_100g=base_macros,
                label_standard=food.label_standard,
                yield_factor=food.yield_factor,
                source=food.source,
                verified=food.verified,
                atwater_warning=food.atwater_warning,
                has_override=False,
                food_portions=food_portions,
                person_portions={},
            )

        merged_macros = Macros(
            kcal=(
                override.kcal_per_100g
                if override.kcal_per_100g is not None
                else base_macros.kcal
            ),
            protein_g=(
                override.protein_g_per_100g
                if override.protein_g_per_100g is not None
                else base_macros.protein_g
            ),
            carbs_g=(
                override.carbs_g_per_100g
                if override.carbs_g_per_100g is not None
                else base_macros.carbs_g
            ),
            fat_g=(
                override.fat_g_per_100g
                if override.fat_g_per_100g is not None
                else base_macros.fat_g
            ),
            fiber_g=(
                override.fiber_g_per_100g
                if override.fiber_g_per_100g is not None
                else base_macros.fiber_g
            ),
        )
        return EffectiveFood(
            food_id=food.food_id,
            name=food.name,
            name_it=food.name_it,
            normalized_name=food.normalized_name,
            aliases=food.aliases,
            preferred_alias=override.preferred_alias,
            brand=food.brand,
            barcode_ean=food.barcode_ean,
            category=food.category,
            preparation_state=food.preparation_state,
            macros_per_100g=merged_macros,
            label_standard=food.label_standard,
            yield_factor=(
                override.yield_factor
                if override.yield_factor is not None
                else food.yield_factor
            ),
            source=food.source,
            verified=food.verified,
            atwater_warning=food.atwater_warning,
            has_override=True,
            food_portions=food_portions,
            person_portions=dict(override.custom_portions),
        )

    @staticmethod
    def _matches_brand(candidate: EffectiveFood, norm_brand: str) -> bool:
        if candidate.brand and norm_brand in normalize_text(candidate.brand):
            return True
        if candidate.preferred_alias and norm_brand in normalize_text(candidate.preferred_alias):
            return True
        return norm_brand in candidate.normalized_name

    @staticmethod
    def _lexical_score(candidate: EffectiveFood, norm_q: str) -> float:
        if not norm_q:
            return 0.0
        names_to_check = [candidate.normalized_name, *candidate.aliases]
        if candidate.name_it:
            names_to_check.append(normalize_text(candidate.name_it))
        if candidate.preferred_alias:
            names_to_check.append(normalize_text(candidate.preferred_alias))
        if any(n == norm_q for n in names_to_check):
            return 100.0
        if any(n.startswith(norm_q) for n in names_to_check):
            return 65.0
        if any(norm_q in n for n in names_to_check):
            return 45.0
        q_tokens = set(norm_q.split())
        best_overlap = 0.0
        for n in names_to_check:
            n_tokens = set(n.split())
            if not n_tokens:
                continue
            overlap = len(q_tokens & n_tokens) / max(len(q_tokens), 1)
            best_overlap = max(best_overlap, overlap)
        return best_overlap * 40.0

    @classmethod
    def _score_candidate(
        cls,
        candidate: EffectiveFood,
        norm_q: str,
        norm_brand: str,
        preferred_state: PreparationState | None = None,
    ) -> float:
        score = cls._lexical_score(candidate, norm_q)
        if norm_brand and cls._matches_brand(candidate, norm_brand):
            score += 40.0
        if candidate.has_override:
            score += 15.0
        if candidate.verified:
            score += 10.0
        if preferred_state and preferred_state != PreparationState.UNSPECIFIED:
            if candidate.preparation_state == preferred_state:
                score += 28.0
            elif {
                candidate.preparation_state,
                preferred_state,
            } == {PreparationState.RAW, PreparationState.COOKED}:
                score -= 12.0
        return score
