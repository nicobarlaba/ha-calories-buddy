"""Repository for two-tier dietary preferences, hard constraints, and meal feedback."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import aiosqlite

from custom_components.ha_calories_buddy.core.models import (
    MealFeedback,
    MealFeedbackRating,
    PreferenceEntry,
    PreferenceScope,
    PreferenceStance,
)
from custom_components.ha_calories_buddy.core.storage.database import Database
from custom_components.ha_calories_buddy.core.storage.seeder import normalize_text


def canonical_recipe_signature(title: str) -> str:
    """Build a stable accent- and punctuation-insensitive signature for a recipe title."""
    return normalize_text(title)


class PreferenceRepository:
    """CRUD repository for Global + Person preferences and per-person recipe feedback."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def list_preferences(
        self,
        person_id: str | None = None,
        *,
        include_global: bool = True,
    ) -> list[PreferenceEntry]:
        """List preference entries for a person and/or global household baseline."""
        if person_id and include_global:
            rows = await self._db.fetch_all(
                """
                SELECT * FROM person_preferences
                WHERE (scope = ? AND person_id IS NULL)
                   OR (scope = ? AND person_id = ?)
                ORDER BY scope ASC, updated_at DESC
                """,
                (
                    PreferenceScope.GLOBAL.value,
                    PreferenceScope.PERSON.value,
                    person_id,
                ),
            )
        elif person_id:
            rows = await self._db.fetch_all(
                """
                SELECT * FROM person_preferences
                WHERE scope = ? AND person_id = ?
                ORDER BY updated_at DESC
                """,
                (PreferenceScope.PERSON.value, person_id),
            )
        else:
            rows = await self._db.fetch_all(
                """
                SELECT * FROM person_preferences
                WHERE scope = ? AND person_id IS NULL
                ORDER BY updated_at DESC
                """,
                (PreferenceScope.GLOBAL.value,),
            )
        return [self._row_to_pref(row) for row in rows]

    async def upsert_preference(
        self,
        *,
        scope: PreferenceScope,
        target_type: str,
        target_value: str,
        stance: PreferenceStance,
        person_id: str | None = None,
        notes: str = "",
        pref_id: str | None = None,
    ) -> PreferenceEntry:
        """Create or update a preference entry deduplicated by scope/person/target."""
        target_owner = None if scope == PreferenceScope.GLOBAL else person_id
        clean_type = target_type.strip().lower()
        clean_value = target_value.strip()
        norm_val = normalize_text(clean_value)
        now = datetime.now(UTC).isoformat()

        existing = await self._find_matching_preference(
            scope=scope,
            person_id=target_owner,
            target_type=clean_type,
            norm_value=norm_val,
        )
        chosen_id = pref_id or (
            existing.pref_id if existing else f"pref_{uuid.uuid4().hex[:12]}"
        )

        async with self._db.transaction() as conn:
            if existing is not None:
                await conn.execute(
                    """
                    UPDATE person_preferences
                    SET target_value = ?, stance = ?, notes = ?, updated_at = ?
                    WHERE pref_id = ?
                    """,
                    (clean_value, stance.value, notes.strip(), now, chosen_id),
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO person_preferences (
                        pref_id, person_id, scope, target_type, target_value,
                        stance, notes, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chosen_id,
                        target_owner,
                        scope.value,
                        clean_type,
                        clean_value,
                        stance.value,
                        notes.strip(),
                        now,
                    ),
                )

        return PreferenceEntry(
            pref_id=chosen_id,
            person_id=target_owner,
            scope=scope,
            target_type=clean_type,
            target_value=clean_value,
            stance=stance,
            notes=notes.strip(),
            updated_at=datetime.fromisoformat(now),
        )

    async def delete_preference(
        self, pref_id: str, person_id: str | None = None
    ) -> bool:
        """Delete a preference entry (enforcing owner check if person_id is provided for person scope)."""
        row = await self._db.fetch_one(
            "SELECT * FROM person_preferences WHERE pref_id = ?",
            (pref_id,),
        )
        if row is None:
            return False
        if (
            row["scope"] == PreferenceScope.PERSON.value
            and person_id is not None
            and row["person_id"] != person_id
        ):
            return False
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "DELETE FROM person_preferences WHERE pref_id = ?",
                (pref_id,),
            )
            return bool(cursor.rowcount > 0)

    async def list_feedback(self, person_id: str) -> list[MealFeedback]:
        """List saved meal/recipe ratings for a person."""
        rows = await self._db.fetch_all(
            """
            SELECT * FROM meal_feedback
            WHERE person_id = ?
            ORDER BY updated_at DESC
            """,
            (person_id,),
        )
        return [self._row_to_feedback(row) for row in rows]

    async def upsert_feedback(
        self,
        *,
        person_id: str,
        title: str,
        rating: MealFeedbackRating,
        notes: str = "",
    ) -> MealFeedback:
        """Record or update like/dislike/never_suggest feedback for a dish title."""
        sig = canonical_recipe_signature(title) or title.strip().lower()
        fid = f"fb_{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat()

        async with self._db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO meal_feedback (
                    feedback_id, person_id, recipe_signature, title, rating, notes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(person_id, recipe_signature) DO UPDATE SET
                    title = excluded.title,
                    rating = excluded.rating,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (fid, person_id, sig, title.strip(), rating.value, notes.strip(), now),
            )
        row = await self._db.fetch_one(
            "SELECT * FROM meal_feedback WHERE person_id = ? AND recipe_signature = ?",
            (person_id, sig),
        )
        if row is not None:
            return self._row_to_feedback(row)
        return MealFeedback(
            feedback_id=fid,
            person_id=person_id,
            recipe_signature=sig,
            title=title.strip(),
            rating=rating,
            notes=notes.strip(),
            updated_at=datetime.fromisoformat(now),
        )

    async def delete_feedback(self, person_id: str, feedback_id: str) -> bool:
        """Delete a meal feedback entry belonging to person_id."""
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "DELETE FROM meal_feedback WHERE feedback_id = ? AND person_id = ?",
                (feedback_id, person_id),
            )
            return bool(cursor.rowcount > 0)

    async def _find_matching_preference(
        self,
        *,
        scope: PreferenceScope,
        person_id: str | None,
        target_type: str,
        norm_value: str,
    ) -> PreferenceEntry | None:
        existing = await self.list_preferences(
            person_id=person_id if scope == PreferenceScope.PERSON else None,
            include_global=(scope == PreferenceScope.GLOBAL),
        )
        for pref in existing:
            if (
                pref.scope == scope
                and pref.target_type.lower() == target_type
                and normalize_text(pref.target_value) == norm_value
            ):
                return pref
        return None

    @staticmethod
    def _row_to_pref(row: aiosqlite.Row) -> PreferenceEntry:
        return PreferenceEntry(
            pref_id=row["pref_id"],
            person_id=row["person_id"],
            scope=PreferenceScope(row["scope"]),
            target_type=row["target_type"],
            target_value=row["target_value"],
            stance=PreferenceStance(row["stance"]),
            notes=row["notes"] or "",
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_feedback(row: aiosqlite.Row) -> MealFeedback:
        return MealFeedback(
            feedback_id=row["feedback_id"],
            person_id=row["person_id"],
            recipe_signature=row["recipe_signature"],
            title=row["title"],
            rating=MealFeedbackRating(row["rating"]),
            notes=row["notes"] or "",
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
