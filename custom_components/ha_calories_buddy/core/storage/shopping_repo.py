"""Repository for editable shopping drafts and aisle-sorted grocery items."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import aiosqlite

from custom_components.ha_calories_buddy.core.models import (
    ShoppingDraft,
    ShoppingDraftItem,
)
from custom_components.ha_calories_buddy.core.storage.database import Database


class ShoppingRepository:
    """CRUD repository for editable shopping lists before/after todo.* sync."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def save_draft(self, draft: ShoppingDraft) -> ShoppingDraft:
        """Persist a new ShoppingDraft atomically, superseding prior uncommitted drafts."""
        now_iso = datetime.now(UTC).isoformat()
        scope_json = json.dumps(draft.scope_person_ids)
        async with self._db.transaction() as conn:
            await conn.execute(
                """
                UPDATE shopping_drafts
                SET status = 'superseded', updated_at = ?
                WHERE person_id = ? AND status = 'draft'
                """,
                (now_iso, draft.person_id),
            )
            await conn.execute(
                """
                INSERT INTO shopping_drafts (
                    draft_id, person_id, plan_id, start_date, end_date,
                    scope_person_ids_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft.draft_id,
                    draft.person_id,
                    draft.plan_id,
                    draft.start_date.isoformat() if draft.start_date else None,
                    draft.end_date.isoformat() if draft.end_date else None,
                    scope_json,
                    draft.status,
                    draft.created_at.isoformat(),
                    now_iso,
                ),
            )
            for item in draft.items:
                await conn.execute(
                    """
                    INSERT INTO shopping_draft_items (
                        draft_item_id, draft_id, food_id, label, brand,
                        quantity, unit, category, aisle, checked, source_meals_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.draft_item_id,
                        draft.draft_id,
                        item.food_id,
                        item.label,
                        item.brand,
                        float(item.quantity),
                        item.unit,
                        item.category,
                        item.aisle,
                        1 if item.checked else 0,
                        json.dumps(item.source_meals),
                    ),
                )
        return await self.get_draft(draft.draft_id) or draft

    async def get_latest_draft(
        self, person_id: str, include_committed: bool = False
    ) -> ShoppingDraft | None:
        """Fetch the most recent active (or committed) draft for a person."""
        if include_committed:
            row = await self._db.fetch_one(
                """
                SELECT * FROM shopping_drafts
                WHERE person_id = ? AND status IN ('draft', 'committed')
                ORDER BY COALESCE(updated_at, created_at) DESC
                LIMIT 1
                """,
                (person_id,),
            )
        else:
            row = await self._db.fetch_one(
                """
                SELECT * FROM shopping_drafts
                WHERE person_id = ? AND status = 'draft'
                ORDER BY COALESCE(updated_at, created_at) DESC
                LIMIT 1
                """,
                (person_id,),
            )
        if row is None:
            return None
        return await self._hydrate_draft(row)

    async def get_draft(self, draft_id: str) -> ShoppingDraft | None:
        """Fetch a single draft with all its items."""
        row = await self._db.fetch_one(
            "SELECT * FROM shopping_drafts WHERE draft_id = ?",
            (draft_id,),
        )
        if row is None:
            return None
        return await self._hydrate_draft(row)

    async def update_draft_item(
        self,
        draft_id: str,
        draft_item_id: str,
        *,
        label: str | None = None,
        quantity: float | None = None,
        unit: str | None = None,
        aisle: str | None = None,
        brand: str | None = None,
        update_brand: bool = False,
        checked: bool | None = None,
    ) -> ShoppingDraft | None:
        """Update properties on a single ShoppingDraftItem."""
        now_iso = datetime.now(UTC).isoformat()
        sets: list[str] = []
        params: list[object] = []
        if label is not None:
            sets.append("label = ?")
            params.append(label.strip())
        if quantity is not None:
            sets.append("quantity = ?")
            params.append(max(0.1, float(quantity)))
        if unit is not None:
            sets.append("unit = ?")
            params.append(unit.strip() or "g")
        if aisle is not None:
            sets.append("aisle = ?")
            params.append(aisle.strip() or "other")
        if update_brand:
            sets.append("brand = ?")
            params.append(brand.strip() if brand and brand.strip() else None)
        if checked is not None:
            sets.append("checked = ?")
            params.append(1 if checked else 0)
        if not sets:
            return await self.get_draft(draft_id)

        params.extend([draft_item_id, draft_id])
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                f"""
                UPDATE shopping_draft_items
                SET {', '.join(sets)}
                WHERE draft_item_id = ? AND draft_id = ?
                """,
                tuple(params),
            )
            if cursor.rowcount == 0:
                return None
            await conn.execute(
                "UPDATE shopping_drafts SET updated_at = ? WHERE draft_id = ?",
                (now_iso, draft_id),
            )
        return await self.get_draft(draft_id)

    async def add_draft_item(
        self, draft_id: str, item: ShoppingDraftItem
    ) -> ShoppingDraft | None:
        """Append a new item to an existing ShoppingDraft."""
        now_iso = datetime.now(UTC).isoformat()
        existing = await self.get_draft(draft_id)
        if existing is None:
            return None
        async with self._db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO shopping_draft_items (
                    draft_item_id, draft_id, food_id, label, brand,
                    quantity, unit, category, aisle, checked, source_meals_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.draft_item_id,
                    draft_id,
                    item.food_id,
                    item.label,
                    item.brand,
                    float(item.quantity),
                    item.unit,
                    item.category,
                    item.aisle,
                    1 if item.checked else 0,
                    json.dumps(item.source_meals),
                ),
            )
            await conn.execute(
                "UPDATE shopping_drafts SET updated_at = ? WHERE draft_id = ?",
                (now_iso, draft_id),
            )
        return await self.get_draft(draft_id)

    async def delete_draft_item(
        self, draft_id: str, draft_item_id: str
    ) -> ShoppingDraft | None:
        """Remove an item from a ShoppingDraft."""
        now_iso = datetime.now(UTC).isoformat()
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "DELETE FROM shopping_draft_items WHERE draft_item_id = ? AND draft_id = ?",
                (draft_item_id, draft_id),
            )
            if cursor.rowcount == 0:
                return None
            await conn.execute(
                "UPDATE shopping_drafts SET updated_at = ? WHERE draft_id = ?",
                (now_iso, draft_id),
            )
        return await self.get_draft(draft_id)

    async def mark_draft_committed(self, draft_id: str) -> ShoppingDraft | None:
        """Mark a ShoppingDraft as committed to Home Assistant todo.*."""
        now_iso = datetime.now(UTC).isoformat()
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE shopping_drafts SET status = 'committed', updated_at = ? WHERE draft_id = ?",
                (now_iso, draft_id),
            )
            if cursor.rowcount == 0:
                return None
        return await self.get_draft(draft_id)

    async def delete_draft(self, draft_id: str) -> bool:
        """Hard-delete a ShoppingDraft and its cascaded items."""
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "DELETE FROM shopping_drafts WHERE draft_id = ?",
                (draft_id,),
            )
            return bool(cursor.rowcount > 0)

    async def _hydrate_draft(self, row: aiosqlite.Row) -> ShoppingDraft:
        keys = row.keys()
        item_rows = await self._db.fetch_all(
            "SELECT * FROM shopping_draft_items WHERE draft_id = ? ORDER BY rowid ASC",
            (row["draft_id"],),
        )
        scope_ids: list[str] = []
        if "scope_person_ids_json" in keys and row["scope_person_ids_json"]:
            try:
                parsed = json.loads(str(row["scope_person_ids_json"]))
                if isinstance(parsed, list):
                    scope_ids = [str(p) for p in parsed]
            except ValueError:
                scope_ids = []
        return ShoppingDraft(
            draft_id=str(row["draft_id"]),
            person_id=str(row["person_id"]),
            plan_id=row["plan_id"],
            start_date=(
                date.fromisoformat(str(row["start_date"]))
                if "start_date" in keys and row["start_date"]
                else None
            ),
            end_date=(
                date.fromisoformat(str(row["end_date"]))
                if "end_date" in keys and row["end_date"]
                else None
            ),
            scope_person_ids=scope_ids,
            status=str(row["status"] or "draft"),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=(
                datetime.fromisoformat(str(row["updated_at"]))
                if "updated_at" in keys and row["updated_at"]
                else datetime.fromisoformat(str(row["created_at"]))
            ),
            items=[self._row_to_item(r) for r in item_rows],
        )

    @staticmethod
    def _row_to_item(row: aiosqlite.Row) -> ShoppingDraftItem:
        keys = row.keys()
        source_meals: list[str] = []
        if "source_meals_json" in keys and row["source_meals_json"]:
            try:
                parsed = json.loads(str(row["source_meals_json"]))
                if isinstance(parsed, list):
                    source_meals = [str(s) for s in parsed]
            except ValueError:
                source_meals = []
        return ShoppingDraftItem(
            draft_item_id=str(row["draft_item_id"]),
            draft_id=str(row["draft_id"]),
            food_id=row["food_id"],
            label=str(row["label"]),
            brand=row["brand"] if "brand" in keys else None,
            quantity=float(row["quantity"]),
            unit=str(row["unit"] or "g"),
            category=str(row["category"] or "other"),
            aisle=str(row["aisle"] or "other") if "aisle" in keys else "other",
            checked=bool(int(row["checked"])),
            source_meals=source_meals,
        )
