"""Repository for weekly/daily meal plans, days, and planned meal items."""

from __future__ import annotations

import json
from datetime import date, datetime

import aiosqlite

from custom_components.ha_calories_buddy.core.models import (
    Macros,
    MealPlan,
    MealPlanDay,
    MealPlanItem,
    PlanItemStatus,
)
from custom_components.ha_calories_buddy.core.storage.database import Database


class MealPlanRepository:
    """CRUD repository for generated meal plans and individual meal slots."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def save_plan(self, plan: MealPlan) -> MealPlan:
        """Persist a new MealPlan atomically, archiving overlapping active plans."""
        async with self._db.transaction() as conn:
            await conn.execute(
                """
                UPDATE meal_plans
                SET status = 'archived'
                WHERE person_id = ? AND status = 'active'
                  AND NOT (end_date < ? OR start_date > ?)
                """,
                (
                    plan.person_id,
                    plan.start_date.isoformat(),
                    plan.end_date.isoformat(),
                ),
            )
            await conn.execute(
                """
                INSERT INTO meal_plans (
                    plan_id, person_id, start_date, end_date, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.plan_id,
                    plan.person_id,
                    plan.start_date.isoformat(),
                    plan.end_date.isoformat(),
                    plan.status,
                    plan.created_at.isoformat(),
                ),
            )

            for day in plan.days:
                await conn.execute(
                    """
                    INSERT INTO meal_plan_days (
                        plan_day_id, plan_id, plan_date, target_kcal,
                        target_protein, target_carbs, target_fat, target_fiber
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        day.plan_day_id,
                        plan.plan_id,
                        day.plan_date.isoformat(),
                        day.target_macros.kcal,
                        day.target_macros.protein_g,
                        day.target_macros.carbs_g,
                        day.target_macros.fat_g,
                        day.target_macros.fiber_g,
                    ),
                )
                for item in day.items:
                    await conn.execute(
                        """
                        INSERT INTO meal_plan_items (
                            plan_item_id, plan_day_id, meal_slot, title,
                            recipe_json, ingredients_json, kcal, protein,
                            carbs, fat, fiber, prep_minutes, status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item.plan_item_id,
                            day.plan_day_id,
                            item.meal_slot,
                            item.title,
                            item.recipe_json,
                            item.ingredients_json,
                            item.macros.kcal,
                            item.macros.protein_g,
                            item.macros.carbs_g,
                            item.macros.fat_g,
                            item.macros.fiber_g,
                            item.prep_minutes,
                            item.status.value,
                        ),
                    )
        return plan

    async def get_active_plan(
        self, person_id: str, reference_date: date | None = None
    ) -> MealPlan | None:
        """Fetch the active plan covering reference_date, or the latest active plan."""
        ref = (reference_date or date.today()).isoformat()
        row = await self._db.fetch_one(
            """
            SELECT * FROM meal_plans
            WHERE person_id = ? AND status = 'active'
              AND start_date <= ? AND end_date >= ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (person_id, ref, ref),
        )
        if row is None:
            row = await self._db.fetch_one(
                """
                SELECT * FROM meal_plans
                WHERE person_id = ? AND status = 'active'
                ORDER BY start_date DESC, created_at DESC
                LIMIT 1
                """,
                (person_id,),
            )
        if row is None:
            return None
        return await self._hydrate_plan(row)

    async def get_plan_items_for_range(
        self,
        person_id: str,
        start_date: date,
        end_date: date,
        *,
        status: PlanItemStatus | None = None,
    ) -> list[tuple[date, MealPlanItem]]:
        """Return (plan_date, MealPlanItem) pairs across active plans inside [start_date, end_date]."""
        params: list[str] = [person_id, start_date.isoformat(), end_date.isoformat()]
        status_sql = ""
        if status is not None:
            status_sql = "AND mpi.status = ?"
            params.append(status.value)

        rows = await self._db.fetch_all(
            f"""
            SELECT mpd.plan_date AS _plan_date, mpi.*
            FROM meal_plan_items mpi
            JOIN meal_plan_days mpd ON mpi.plan_day_id = mpd.plan_day_id
            JOIN meal_plans mp ON mpd.plan_id = mp.plan_id
            WHERE mp.person_id = ? AND mp.status = 'active'
              AND mpd.plan_date >= ? AND mpd.plan_date <= ?
              {status_sql}
            ORDER BY mpd.plan_date ASC, mpi.rowid ASC
            """,
            tuple(params),
        )
        return [
            (date.fromisoformat(row["_plan_date"]), self._row_to_item(row))
            for row in rows
        ]

    async def get_plan_item(
        self, person_id: str, plan_item_id: str
    ) -> tuple[MealPlanDay, MealPlanItem] | None:
        """Fetch one planned item along with its parent MealPlanDay."""
        item_row = await self._db.fetch_one(
            """
            SELECT mpi.*
            FROM meal_plan_items mpi
            JOIN meal_plan_days mpd ON mpi.plan_day_id = mpd.plan_day_id
            JOIN meal_plans mp ON mpd.plan_id = mp.plan_id
            WHERE mpi.plan_item_id = ? AND mp.person_id = ?
            """,
            (plan_item_id, person_id),
        )
        if item_row is None:
            return None

        day_row = await self._db.fetch_one(
            "SELECT * FROM meal_plan_days WHERE plan_day_id = ?",
            (item_row["plan_day_id"],),
        )
        if day_row is None:
            return None

        sibling_rows = await self._db.fetch_all(
            "SELECT * FROM meal_plan_items WHERE plan_day_id = ? ORDER BY rowid ASC",
            (day_row["plan_day_id"],),
        )
        day = self._row_to_day(
            day_row, [self._row_to_item(r) for r in sibling_rows]
        )
        return day, self._row_to_item(item_row)

    async def get_plan_day(
        self, person_id: str, plan_date: date
    ) -> MealPlanDay | None:
        """Fetch active MealPlanDay and all its items for person_id and plan_date."""
        day_row = await self._db.fetch_one(
            """
            SELECT mpd.*
            FROM meal_plan_days mpd
            JOIN meal_plans mp ON mpd.plan_id = mp.plan_id
            WHERE mp.person_id = ? AND mp.status = 'active' AND mpd.plan_date = ?
            ORDER BY mp.created_at DESC
            LIMIT 1
            """,
            (person_id, plan_date.isoformat()),
        )
        if day_row is None:
            return None
        item_rows = await self._db.fetch_all(
            "SELECT * FROM meal_plan_items WHERE plan_day_id = ? ORDER BY rowid ASC",
            (day_row["plan_day_id"],),
        )
        return self._row_to_day(
            day_row, [self._row_to_item(r) for r in item_rows]
        )

    async def replace_plan_item(self, item: MealPlanItem) -> None:
        """Replace title, recipe, ingredients, macros, and prep_minutes on an existing item."""
        await self._db.execute(
            """
            UPDATE meal_plan_items
            SET meal_slot = ?, title = ?, recipe_json = ?, ingredients_json = ?,
                kcal = ?, protein = ?, carbs = ?, fat = ?, fiber = ?,
                prep_minutes = ?, status = ?
            WHERE plan_item_id = ?
            """,
            (
                item.meal_slot,
                item.title,
                item.recipe_json,
                item.ingredients_json,
                item.macros.kcal,
                item.macros.protein_g,
                item.macros.carbs_g,
                item.macros.fat_g,
                item.macros.fiber_g,
                item.prep_minutes,
                item.status.value,
                item.plan_item_id,
            ),
        )

    async def update_plan_item_status(
        self, person_id: str, plan_item_id: str, status: PlanItemStatus
    ) -> bool:
        """Transition a planned item's status ensuring person ownership."""
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                """
                UPDATE meal_plan_items
                SET status = ?
                WHERE plan_item_id = ?
                  AND plan_day_id IN (
                      SELECT mpd.plan_day_id FROM meal_plan_days mpd
                      JOIN meal_plans mp ON mpd.plan_id = mp.plan_id
                      WHERE mp.person_id = ?
                  )
                """,
                (status.value, plan_item_id, person_id),
            )
            return bool(cursor.rowcount > 0)

    async def update_plan_day_targets(
        self, plan_day_id: str, target_macros: Macros
    ) -> None:
        """Persist updated target macros on a MealPlanDay (e.g., multi-day smoothing)."""
        await self._db.execute(
            """
            UPDATE meal_plan_days
            SET target_kcal = ?, target_protein = ?, target_carbs = ?,
                target_fat = ?, target_fiber = ?
            WHERE plan_day_id = ?
            """,
            (
                target_macros.kcal,
                target_macros.protein_g,
                target_macros.carbs_g,
                target_macros.fat_g,
                target_macros.fiber_g,
                plan_day_id,
            ),
        )

    async def _hydrate_plan(self, plan_row: aiosqlite.Row) -> MealPlan:
        plan_id = str(plan_row["plan_id"])
        day_rows = await self._db.fetch_all(
            "SELECT * FROM meal_plan_days WHERE plan_id = ? ORDER BY plan_date ASC",
            (plan_id,),
        )
        day_ids = [str(r["plan_day_id"]) for r in day_rows]
        items_by_day: dict[str, list[MealPlanItem]] = {}
        if day_ids:
            placeholders = ",".join("?" for _ in day_ids)
            item_rows = await self._db.fetch_all(
                f"SELECT * FROM meal_plan_items WHERE plan_day_id IN ({placeholders}) ORDER BY rowid ASC",
                tuple(day_ids),
            )
            for row in item_rows:
                it = self._row_to_item(row)
                items_by_day.setdefault(it.plan_day_id, []).append(it)

        days = [
            self._row_to_day(d_row, items_by_day.get(str(d_row["plan_day_id"]), []))
            for d_row in day_rows
        ]
        return MealPlan(
            plan_id=plan_id,
            person_id=str(plan_row["person_id"]),
            start_date=date.fromisoformat(plan_row["start_date"]),
            end_date=date.fromisoformat(plan_row["end_date"]),
            status=str(plan_row["status"]),
            created_at=datetime.fromisoformat(plan_row["created_at"]),
            days=days,
        )

    @staticmethod
    def _row_to_day(
        row: aiosqlite.Row, items: list[MealPlanItem]
    ) -> MealPlanDay:
        keys = row.keys()
        fiber_val = (
            float(row["target_fiber"])
            if "target_fiber" in keys and row["target_fiber"] is not None
            else None
        )
        return MealPlanDay(
            plan_day_id=str(row["plan_day_id"]),
            plan_id=str(row["plan_id"]),
            plan_date=date.fromisoformat(row["plan_date"]),
            target_macros=Macros(
                kcal=float(row["target_kcal"]),
                protein_g=float(row["target_protein"]),
                carbs_g=float(row["target_carbs"]),
                fat_g=float(row["target_fat"]),
                fiber_g=fiber_val,
            ),
            items=items,
        )

    @staticmethod
    def _row_to_item(row: aiosqlite.Row) -> MealPlanItem:
        keys = row.keys()
        recipe_raw = str(row["recipe_json"] or "{}")
        macro_warn = False
        try:
            recipe_meta = json.loads(recipe_raw)
            if isinstance(recipe_meta, dict):
                macro_warn = bool(recipe_meta.get("macro_fit_warning", False))
        except ValueError:
            pass
        fiber_val = (
            float(row["fiber"])
            if "fiber" in keys and row["fiber"] is not None
            else None
        )
        prep_val = (
            int(row["prep_minutes"])
            if "prep_minutes" in keys and row["prep_minutes"] is not None
            else 20
        )
        return MealPlanItem(
            plan_item_id=str(row["plan_item_id"]),
            plan_day_id=str(row["plan_day_id"]),
            meal_slot=str(row["meal_slot"]),
            title=str(row["title"]),
            recipe_json=recipe_raw,
            ingredients_json=str(row["ingredients_json"] or "[]"),
            macros=Macros(
                kcal=float(row["kcal"]),
                protein_g=float(row["protein"]),
                carbs_g=float(row["carbs"]),
                fat_g=float(row["fat"]),
                fiber_g=fiber_val,
            ),
            prep_minutes=prep_val,
            macro_fit_warning=macro_warn,
            status=PlanItemStatus(row["status"]),
        )
