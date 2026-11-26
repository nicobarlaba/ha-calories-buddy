"""Meal log repository supporting hybrid confirmation, soft-delete, and analytics."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import aiosqlite

from custom_components.ha_calories_buddy.core.models import (
    ConfidenceLevel,
    DailyTotals,
    Macros,
    MealLog,
    MealLogItem,
    MealStatus,
    PreparationState,
    WarningCode,
)
from custom_components.ha_calories_buddy.core.storage.database import Database


class MealRepository:
    """Repository for meal logs, items, confirmations, and frequency queries."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def create_meal_log(self, meal_log: MealLog) -> MealLog:
        """Persist a MealLog and all its items inside one transaction."""
        async with self._db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO meal_logs (
                    log_id, person_id, log_date, meal_slot, logged_at,
                    status, source_channel, raw_input_text,
                    total_kcal, total_protein, total_carbs, total_fat,
                    total_fiber, plan_item_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    meal_log.log_id,
                    meal_log.person_id,
                    meal_log.log_date.isoformat(),
                    meal_log.meal_slot,
                    meal_log.logged_at.isoformat(),
                    meal_log.status.value,
                    meal_log.source_channel,
                    meal_log.raw_input_text,
                    meal_log.total_macros.kcal,
                    meal_log.total_macros.protein_g,
                    meal_log.total_macros.carbs_g,
                    meal_log.total_macros.fat_g,
                    meal_log.total_macros.fiber_g,
                    meal_log.plan_item_id,
                ),
            )

            for item in meal_log.items:
                await conn.execute(
                    """
                    INSERT INTO meal_log_items (
                        item_id, log_id, food_id, display_name, brand,
                        input_quantity, input_unit, input_state,
                        resolved_grams_ref, kcal, protein, carbs, fat, fiber,
                        confidence, warning_code, warning_detail
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.item_id,
                        meal_log.log_id,
                        item.food_id,
                        item.display_name,
                        item.brand,
                        item.input_quantity,
                        item.input_unit,
                        item.input_state.value,
                        item.resolved_grams_ref,
                        item.macros.kcal,
                        item.macros.protein_g,
                        item.macros.carbs_g,
                        item.macros.fat_g,
                        item.macros.fiber_g,
                        item.confidence.value,
                        item.warning_code.value if item.warning_code else None,
                        item.warning_detail,
                    ),
                )

        return meal_log

    async def get_meal_log(self, log_id: str) -> MealLog | None:
        """Fetch a single meal log with its items."""
        row = await self._db.fetch_one(
            "SELECT * FROM meal_logs WHERE log_id = ?",
            (log_id,),
        )
        if row is None:
            return None
        items = await self._get_items_for_logs([log_id])
        return self._row_to_meal_log(row, items.get(log_id, []))

    async def list_meals_for_date(
        self,
        person_id: str,
        log_date: date,
        include_deleted: bool = False,
    ) -> list[MealLog]:
        """List meal logs for a specific person and date ordered chronologically."""
        return await self.list_meals_for_range(
            person_id=person_id,
            start_date=log_date,
            end_date=log_date,
            include_deleted=include_deleted,
        )

    async def list_meals_for_range(
        self,
        person_id: str,
        start_date: date,
        end_date: date,
        include_deleted: bool = False,
    ) -> list[MealLog]:
        """List meal logs across a date range (inclusive)."""
        if include_deleted:
            rows = await self._db.fetch_all(
                """
                SELECT * FROM meal_logs
                WHERE person_id = ? AND log_date >= ? AND log_date <= ?
                ORDER BY log_date ASC, logged_at ASC
                """,
                (person_id, start_date.isoformat(), end_date.isoformat()),
            )
        else:
            rows = await self._db.fetch_all(
                """
                SELECT * FROM meal_logs
                WHERE person_id = ? AND log_date >= ? AND log_date <= ?
                  AND status != ?
                ORDER BY log_date ASC, logged_at ASC
                """,
                (
                    person_id,
                    start_date.isoformat(),
                    end_date.isoformat(),
                    MealStatus.DELETED.value,
                ),
            )
        if not rows:
            return []
        log_ids = [row["log_id"] for row in rows]
        items_by_log = await self._get_items_for_logs(log_ids)
        return [
            self._row_to_meal_log(row, items_by_log.get(row["log_id"], []))
            for row in rows
        ]

    async def get_daily_totals(self, person_id: str, log_date: date) -> DailyTotals:
        """Compute confirmed totals, per-slot breakdown, and pending confirmation count."""
        meals = await self.list_meals_for_date(person_id, log_date, include_deleted=False)
        kcal = 0.0
        protein = 0.0
        carbs = 0.0
        fat = 0.0
        fiber_total = 0.0
        has_fiber = False
        confirmed_count = 0
        pending_count = 0
        by_slot: dict[str, Macros] = {}

        for meal in meals:
            if meal.status == MealStatus.PENDING_CONFIRMATION:
                pending_count += 1
                continue
            if meal.status != MealStatus.CONFIRMED:
                continue

            confirmed_count += 1
            m = meal.total_macros
            kcal += m.kcal
            protein += m.protein_g
            carbs += m.carbs_g
            fat += m.fat_g
            if m.fiber_g is not None:
                has_fiber = True
                fiber_total += m.fiber_g

            prev = by_slot.get(meal.meal_slot)
            if prev is None:
                by_slot[meal.meal_slot] = m
            else:
                slot_fiber: float | None = None
                if prev.fiber_g is not None or m.fiber_g is not None:
                    slot_fiber = round((prev.fiber_g or 0.0) + (m.fiber_g or 0.0), 1)
                by_slot[meal.meal_slot] = Macros(
                    kcal=round(prev.kcal + m.kcal, 1),
                    protein_g=round(prev.protein_g + m.protein_g, 1),
                    carbs_g=round(prev.carbs_g + m.carbs_g, 1),
                    fat_g=round(prev.fat_g + m.fat_g, 1),
                    fiber_g=slot_fiber,
                )

        return DailyTotals(
            log_date=log_date,
            consumed=Macros(
                kcal=round(kcal, 1),
                protein_g=round(protein, 1),
                carbs_g=round(carbs, 1),
                fat_g=round(fat, 1),
                fiber_g=round(fiber_total, 1) if has_fiber else None,
            ),
            confirmed_count=confirmed_count,
            pending_count=pending_count,
            by_slot=by_slot,
        )

    async def confirm_meal(self, person_id: str, log_id: str) -> MealLog | None:
        """Promote a pending meal to confirmed status."""
        async with self._db.transaction() as conn:
            await conn.execute(
                """
                UPDATE meal_logs SET status = ?
                WHERE log_id = ? AND person_id = ? AND status != ?
                """,
                (
                    MealStatus.CONFIRMED.value,
                    log_id,
                    person_id,
                    MealStatus.DELETED.value,
                ),
            )
            await conn.execute(
                """
                UPDATE meal_log_items
                SET confidence = ?, warning_code = NULL, warning_detail = NULL
                WHERE log_id = ?
                """,
                (ConfidenceLevel.HIGH.value, log_id),
            )
        return await self.get_meal_log(log_id)

    async def update_meal_item(
        self,
        person_id: str,
        log_id: str,
        item_id: str,
        *,
        input_quantity: float,
        input_unit: str,
        input_state: PreparationState,
        resolved_grams_ref: float,
        macros: Macros,
        food_id: str | None = None,
        display_name: str | None = None,
        brand: str | None = None,
        update_brand: bool = False,
        confidence: ConfidenceLevel = ConfidenceLevel.HIGH,
        warning_code: WarningCode | None = None,
        warning_detail: str | None = None,
    ) -> MealLog | None:
        """Update a logged item and atomically recalculate parent meal totals."""
        existing = await self.get_meal_log(log_id)
        if existing is None or existing.person_id != person_id:
            return None

        brand_clause = ",\n                    brand = ?" if update_brand else ""
        params: list[Any] = [food_id, display_name]
        if update_brand:
            params.append(brand)
        params.extend(
            [
                input_quantity,
                input_unit,
                input_state.value,
                resolved_grams_ref,
                macros.kcal,
                macros.protein_g,
                macros.carbs_g,
                macros.fat_g,
                macros.fiber_g,
                confidence.value,
                warning_code.value if warning_code else None,
                warning_detail,
                item_id,
                log_id,
            ]
        )

        async with self._db.transaction() as conn:
            await conn.execute(
                f"""
                UPDATE meal_log_items
                SET food_id = COALESCE(?, food_id),
                    display_name = COALESCE(?, display_name){brand_clause},
                    input_quantity = ?,
                    input_unit = ?,
                    input_state = ?,
                    resolved_grams_ref = ?,
                    kcal = ?,
                    protein = ?,
                    carbs = ?,
                    fat = ?,
                    fiber = ?,
                    confidence = ?,
                    warning_code = ?,
                    warning_detail = ?
                WHERE item_id = ? AND log_id = ?
                """,
                tuple(params),
            )
            await self._recalculate_meal_totals(conn, person_id, log_id)

        return await self.get_meal_log(log_id)

    async def add_meal_item(
        self, person_id: str, log_id: str, item: MealLogItem
    ) -> MealLog | None:
        """Append one item to an existing non-deleted meal log and recalculate totals."""
        async with self._db.transaction() as conn:
            async with conn.execute(
                """
                SELECT log_id FROM meal_logs
                WHERE log_id = ? AND person_id = ? AND status != ?
                """,
                (log_id, person_id, MealStatus.DELETED.value),
            ) as check_cur:
                row = await check_cur.fetchone()
            if row is None:
                return None

            await conn.execute(
                """
                INSERT INTO meal_log_items (
                    item_id, log_id, food_id, display_name, brand,
                    input_quantity, input_unit, input_state,
                    resolved_grams_ref, kcal, protein, carbs, fat, fiber,
                    confidence, warning_code, warning_detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.item_id,
                    log_id,
                    item.food_id,
                    item.display_name,
                    item.brand,
                    item.input_quantity,
                    item.input_unit,
                    item.input_state.value,
                    item.resolved_grams_ref,
                    item.macros.kcal,
                    item.macros.protein_g,
                    item.macros.carbs_g,
                    item.macros.fat_g,
                    item.macros.fiber_g,
                    item.confidence.value,
                    item.warning_code.value if item.warning_code else None,
                    item.warning_detail,
                ),
            )
            await self._recalculate_meal_totals(conn, person_id, log_id)

        return await self.get_meal_log(log_id)

    async def find_active_meal_for_slot(
        self,
        person_id: str,
        log_date: date,
        meal_slot: str,
        *,
        status: MealStatus = MealStatus.CONFIRMED,
    ) -> MealLog | None:
        """Return the latest non-deleted meal log for a given date, slot, and status."""
        row = await self._db.fetch_one(
            """
            SELECT log_id FROM meal_logs
            WHERE person_id = ? AND log_date = ? AND meal_slot = ? AND status = ?
            ORDER BY logged_at DESC
            LIMIT 1
            """,
            (person_id, log_date.isoformat(), meal_slot, status.value),
        )
        if row is None:
            return None
        return await self.get_meal_log(str(row["log_id"]))

    async def delete_meal_item(
        self, person_id: str, log_id: str, item_id: str
    ) -> MealLog | None:
        """Delete one item from a meal log and recalculate (or soft-delete empty parent)."""
        existing = await self.get_meal_log(log_id)
        if existing is None or existing.person_id != person_id:
            return None

        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "DELETE FROM meal_log_items WHERE item_id = ? AND log_id = ?",
                (item_id, log_id),
            )
            if cursor.rowcount <= 0:
                return None
            await self._recalculate_meal_totals(
                conn, person_id, log_id, soft_delete_if_empty=True
            )

        return await self.get_meal_log(log_id)

    @staticmethod
    async def _recalculate_meal_totals(
        conn: Any,
        person_id: str,
        log_id: str,
        *,
        soft_delete_if_empty: bool = False,
    ) -> int:
        """Recompute parent meal_logs macro totals from its remaining items."""
        async with conn.execute(
            """
            SELECT
                COUNT(*) AS cnt,
                COALESCE(SUM(kcal), 0) AS kcal,
                COALESCE(SUM(protein), 0) AS protein,
                COALESCE(SUM(carbs), 0) AS carbs,
                COALESCE(SUM(fat), 0) AS fat,
                SUM(fiber) AS fiber
            FROM meal_log_items
            WHERE log_id = ?
            """,
            (log_id,),
        ) as agg_cur:
            agg = await agg_cur.fetchone()

        rem_cnt = int(agg["cnt"]) if agg is not None else 0
        if rem_cnt == 0 and soft_delete_if_empty:
            await conn.execute(
                """
                UPDATE meal_logs
                SET status = ?, total_kcal = 0, total_protein = 0,
                    total_carbs = 0, total_fat = 0, total_fiber = NULL
                WHERE log_id = ? AND person_id = ?
                """,
                (MealStatus.DELETED.value, log_id, person_id),
            )
        elif agg is not None:
            await conn.execute(
                """
                UPDATE meal_logs
                SET total_kcal = ?, total_protein = ?, total_carbs = ?,
                    total_fat = ?, total_fiber = ?
                WHERE log_id = ? AND person_id = ?
                """,
                (
                    round(float(agg["kcal"]), 1),
                    round(float(agg["protein"]), 1),
                    round(float(agg["carbs"]), 1),
                    round(float(agg["fat"]), 1),
                    round(float(agg["fiber"]), 1) if agg["fiber"] is not None else None,
                    log_id,
                    person_id,
                ),
            )
        return rem_cnt

    async def soft_delete_meal(self, person_id: str, log_id: str) -> bool:
        """Mark a meal log as deleted (preserving undo capability)."""
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE meal_logs SET status = ? WHERE log_id = ? AND person_id = ?",
                (MealStatus.DELETED.value, log_id, person_id),
            )
            return bool(cursor.rowcount > 0)

    async def restore_meal(self, person_id: str, log_id: str) -> bool:
        """Restore a soft-deleted meal back to confirmed."""
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE meal_logs SET status = ? WHERE log_id = ? AND person_id = ? AND status = ?",
                (
                    MealStatus.CONFIRMED.value,
                    log_id,
                    person_id,
                    MealStatus.DELETED.value,
                ),
            )
            return bool(cursor.rowcount > 0)

    async def update_meal_slot(
        self, person_id: str, log_id: str, meal_slot: str
    ) -> MealLog | None:
        """Reassign the meal_slot label of an existing meal log."""
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                """
                UPDATE meal_logs
                SET meal_slot = ?
                WHERE log_id = ? AND person_id = ? AND status != ?
                """,
                (meal_slot, log_id, person_id, MealStatus.DELETED.value),
            )
            if cursor.rowcount <= 0:
                return None
        return await self.get_meal_log(log_id)

    async def get_recent_meal_titles(
        self, person_id: str, days: int = 7, reference_date: date | None = None
    ) -> list[str]:
        """Return recent meal dish titles/descriptions from the past N days for variety."""
        end_d = reference_date or date.today()
        start_d = end_d - timedelta(days=max(1, days))
        plan_rows = await self._db.fetch_all(
            """
            SELECT DISTINCT mpi.title
            FROM meal_plan_items mpi
            JOIN meal_plan_days mpd ON mpi.plan_day_id = mpd.plan_day_id
            JOIN meal_plans mp ON mpd.plan_id = mp.plan_id
            WHERE mp.person_id = ? AND mpd.plan_date >= ? AND mpd.plan_date <= ?
            ORDER BY mpd.plan_date DESC
            LIMIT 25
            """,
            (person_id, start_d.isoformat(), end_d.isoformat()),
        )
        titles = [str(r["title"]) for r in plan_rows if r["title"]]
        if len(titles) < 15:
            log_rows = await self._db.fetch_all(
                """
                SELECT ml.raw_input_text, ml.meal_slot
                FROM meal_logs ml
                WHERE ml.person_id = ? AND ml.log_date >= ? AND ml.status = ?
                  AND ml.raw_input_text IS NOT NULL AND ml.raw_input_text != ''
                ORDER BY ml.log_date DESC, ml.logged_at DESC
                LIMIT 15
                """,
                (person_id, start_d.isoformat(), MealStatus.CONFIRMED.value),
            )
            for row in log_rows:
                txt = str(row["raw_input_text"]).strip()
                if txt and txt not in titles:
                    titles.append(txt[:80])
        return titles[:25]

    async def get_top_frequent_foods(
        self,
        person_id: str,
        limit: int = 25,
        *,
        min_distinct_days: int = 1,
    ) -> list[dict[str, Any]]:
        """Return most frequently consumed foods/brands for NLP hints & preference scoring."""
        having_sql = "HAVING COUNT(DISTINCT ml.log_date) >= ?" if min_distinct_days > 1 else ""
        params: list[Any] = [person_id, MealStatus.CONFIRMED.value]
        if min_distinct_days > 1:
            params.append(int(min_distinct_days))
        params.append(limit)
        rows = await self._db.fetch_all(
            f"""
            SELECT mli.food_id, mli.display_name, mli.brand, COUNT(*) AS freq,
                   MAX(ml.log_date) AS last_date
            FROM meal_log_items mli
            JOIN meal_logs ml ON mli.log_id = ml.log_id
            WHERE ml.person_id = ? AND ml.status = ?
            GROUP BY COALESCE(mli.food_id, mli.display_name), mli.brand
            {having_sql}
            ORDER BY freq DESC, last_date DESC
            LIMIT ?
            """,
            tuple(params),
        )
        return [
            {
                "food_id": row["food_id"],
                "display_name": row["display_name"],
                "brand": row["brand"],
                "frequency": int(row["freq"]),
                "last_date": row["last_date"],
            }
            for row in rows
        ]

    async def get_eaten_today_foods(
        self, person_id: str, target_date: date
    ) -> tuple[list[str], list[str]]:
        """Return deduplicated food names and categories already eaten on target_date."""
        rows = await self._db.fetch_all(
            """
            SELECT mli.display_name, LOWER(COALESCE(gf.category, '')) AS category
            FROM meal_log_items mli
            JOIN meal_logs ml ON mli.log_id = ml.log_id
            LEFT JOIN global_foods gf ON mli.food_id = gf.food_id
            WHERE ml.person_id = ? AND ml.log_date = ? AND ml.status != ?
            ORDER BY ml.logged_at ASC
            """,
            (person_id, target_date.isoformat(), MealStatus.DELETED.value),
        )
        food_names: list[str] = []
        categories: list[str] = []
        seen_names: set[str] = set()
        seen_cats: set[str] = set()
        for row in rows:
            name = str(row["display_name"] or "").strip()
            if name and name.lower() not in seen_names:
                seen_names.add(name.lower())
                food_names.append(name)
            cat = str(row["category"] or "").strip().lower()
            if cat and cat not in seen_cats:
                seen_cats.add(cat)
                categories.append(cat)
        return food_names, categories

    async def get_daily_macros_range(
        self,
        person_id: str,
        start_date: date,
        end_date: date,
    ) -> dict[date, Macros]:
        """Aggregate confirmed daily macros across an inclusive range in a single SQL query."""
        rows = await self._db.fetch_all(
            """
            SELECT log_date,
                   SUM(total_kcal) AS kcal,
                   SUM(total_protein) AS protein,
                   SUM(total_carbs) AS carbs,
                   SUM(total_fat) AS fat,
                   SUM(COALESCE(total_fiber, 0.0)) AS fiber
            FROM meal_logs
            WHERE person_id = ? AND log_date >= ? AND log_date <= ? AND status = ?
            GROUP BY log_date
            ORDER BY log_date ASC
            """,
            (
                person_id,
                start_date.isoformat(),
                end_date.isoformat(),
                MealStatus.CONFIRMED.value,
            ),
        )
        return {
            date.fromisoformat(str(row["log_date"])): Macros(
                kcal=round(float(row["kcal"] or 0.0), 1),
                protein_g=round(float(row["protein"] or 0.0), 1),
                carbs_g=round(float(row["carbs"] or 0.0), 1),
                fat_g=round(float(row["fat"] or 0.0), 1),
                fiber_g=round(float(row["fiber"] or 0.0), 1),
            )
            for row in rows
        }

    async def _get_items_for_logs(
        self, log_ids: list[str]
    ) -> dict[str, list[MealLogItem]]:
        placeholders = ",".join("?" for _ in log_ids)
        rows = await self._db.fetch_all(
            f"SELECT * FROM meal_log_items WHERE log_id IN ({placeholders})",
            tuple(log_ids),
        )
        grouped: dict[str, list[MealLogItem]] = {}
        for row in rows:
            item = MealLogItem(
                item_id=row["item_id"],
                log_id=row["log_id"],
                food_id=row["food_id"],
                display_name=row["display_name"],
                brand=row["brand"],
                input_quantity=float(row["input_quantity"]),
                input_unit=row["input_unit"],
                input_state=PreparationState(row["input_state"]),
                resolved_grams_ref=float(row["resolved_grams_ref"]),
                macros=Macros(
                    kcal=float(row["kcal"]),
                    protein_g=float(row["protein"]),
                    carbs_g=float(row["carbs"]),
                    fat_g=float(row["fat"]),
                    fiber_g=float(row["fiber"]) if row["fiber"] is not None else None,
                ),
                confidence=ConfidenceLevel(row["confidence"]),
                warning_code=(
                    WarningCode(row["warning_code"]) if row["warning_code"] else None
                ),
                warning_detail=row["warning_detail"],
            )
            grouped.setdefault(item.log_id, []).append(item)
        return grouped

    @staticmethod
    def _row_to_meal_log(
        row: aiosqlite.Row, items: list[MealLogItem]
    ) -> MealLog:
        return MealLog(
            log_id=row["log_id"],
            person_id=row["person_id"],
            log_date=date.fromisoformat(row["log_date"]),
            meal_slot=row["meal_slot"],
            logged_at=datetime.fromisoformat(row["logged_at"]),
            status=MealStatus(row["status"]),
            source_channel=row["source_channel"],
            raw_input_text=row["raw_input_text"],
            total_macros=Macros(
                kcal=float(row["total_kcal"]),
                protein_g=float(row["total_protein"]),
                carbs_g=float(row["total_carbs"]),
                fat_g=float(row["total_fat"]),
                fiber_g=(
                    float(row["total_fiber"])
                    if row["total_fiber"] is not None
                    else None
                ),
            ),
            plan_item_id=row["plan_item_id"],
            items=items,
        )
