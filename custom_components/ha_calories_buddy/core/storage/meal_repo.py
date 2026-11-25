"""Meal log repository supporting hybrid confirmation, soft-delete, and analytics."""

from __future__ import annotations

from datetime import date, datetime
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
        confidence: ConfidenceLevel = ConfidenceLevel.HIGH,
        warning_code: WarningCode | None = None,
        warning_detail: str | None = None,
    ) -> MealLog | None:
        """Update a logged item and atomically recalculate parent meal totals."""
        existing = await self.get_meal_log(log_id)
        if existing is None or existing.person_id != person_id:
            return None

        async with self._db.transaction() as conn:
            await conn.execute(
                """
                UPDATE meal_log_items
                SET food_id = COALESCE(?, food_id),
                    display_name = COALESCE(?, display_name),
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
                (
                    food_id,
                    display_name,
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
                ),
            )

            async with conn.execute(
                """
                SELECT
                    COALESCE(SUM(kcal), 0) AS kcal,
                    COALESCE(SUM(protein), 0) AS protein,
                    COALESCE(SUM(carbs), 0) AS carbs,
                    COALESCE(SUM(fat), 0) AS fat,
                    SUM(fiber) AS fiber
                FROM meal_log_items
                WHERE log_id = ?
                """,
                (log_id,),
            ) as cursor:
                agg = await cursor.fetchone()

            if agg is not None:
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

        return await self.get_meal_log(log_id)

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

    async def get_top_frequent_foods(
        self, person_id: str, limit: int = 25
    ) -> list[dict[str, Any]]:
        """Return most frequently consumed foods/brands for NLP hints & preference scoring."""
        rows = await self._db.fetch_all(
            """
            SELECT mli.food_id, mli.display_name, mli.brand, COUNT(*) AS freq,
                   MAX(ml.log_date) AS last_date
            FROM meal_log_items mli
            JOIN meal_logs ml ON mli.log_id = ml.log_id
            WHERE ml.person_id = ? AND ml.status = ?
            GROUP BY COALESCE(mli.food_id, mli.display_name), mli.brand
            ORDER BY freq DESC, last_date DESC
            LIMIT ?
            """,
            (person_id, MealStatus.CONFIRMED.value, limit),
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
