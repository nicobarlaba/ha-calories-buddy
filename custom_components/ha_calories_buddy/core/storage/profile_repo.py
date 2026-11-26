"""Person profile, biometric snapshot, and two-tier preference repository."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import aiosqlite

from custom_components.ha_calories_buddy.core.models import (
    AdaptiveState,
    BiometricSnapshot,
    PersonProfile,
    PreferenceEntry,
    PreferenceScope,
    PreferenceStance,
)
from custom_components.ha_calories_buddy.core.storage.database import Database
from custom_components.ha_calories_buddy.core.storage.seeder import normalize_text


class ProfileRepository:
    """Repository for person profiles, biometric history, and preferences."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_person(self, profile: PersonProfile) -> PersonProfile:
        """Create or update a person profile."""
        profile_json = profile.model_dump_json()
        async with self._db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO persons (
                    person_id, config_entry_id, ha_person_entity, ha_user_id,
                    display_name, language, profile_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(person_id) DO UPDATE SET
                    config_entry_id = excluded.config_entry_id,
                    ha_person_entity = excluded.ha_person_entity,
                    ha_user_id = excluded.ha_user_id,
                    display_name = excluded.display_name,
                    language = excluded.language,
                    profile_json = excluded.profile_json
                """,
                (
                    profile.person_id,
                    profile.config_entry_id,
                    profile.ha_person_entity,
                    profile.ha_user_id,
                    profile.display_name,
                    profile.language,
                    profile_json,
                    profile.created_at.isoformat(),
                ),
            )
        return profile

    async def get_person(self, person_id: str) -> PersonProfile | None:
        """Fetch a person profile by person_id."""
        row = await self._db.fetch_one(
            "SELECT profile_json FROM persons WHERE person_id = ?",
            (person_id,),
        )
        return PersonProfile.model_validate_json(row["profile_json"]) if row else None

    async def get_person_by_entry_id(self, config_entry_id: str) -> PersonProfile | None:
        """Fetch a person profile by Home Assistant ConfigEntry ID."""
        row = await self._db.fetch_one(
            "SELECT profile_json FROM persons WHERE config_entry_id = ?",
            (config_entry_id,),
        )
        return PersonProfile.model_validate_json(row["profile_json"]) if row else None

    async def get_person_by_user_id(self, ha_user_id: str) -> PersonProfile | None:
        """Fetch a person profile mapped to a Home Assistant user_id."""
        row = await self._db.fetch_one(
            "SELECT profile_json FROM persons WHERE ha_user_id = ?",
            (ha_user_id,),
        )
        return PersonProfile.model_validate_json(row["profile_json"]) if row else None

    async def get_person_by_entity(self, ha_person_entity: str) -> PersonProfile | None:
        """Fetch a person profile mapped to a Home Assistant person.* entity."""
        row = await self._db.fetch_one(
            "SELECT profile_json FROM persons WHERE ha_person_entity = ?",
            (ha_person_entity,),
        )
        return PersonProfile.model_validate_json(row["profile_json"]) if row else None

    async def list_persons(self) -> list[PersonProfile]:
        """List all registered person profiles ordered by name."""
        rows = await self._db.fetch_all(
            "SELECT profile_json FROM persons ORDER BY display_name ASC"
        )
        return [PersonProfile.model_validate_json(row["profile_json"]) for row in rows]

    async def delete_person(self, person_id: str) -> bool:
        """Delete a person profile and cascade its logs/overrides/snapshots."""
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "DELETE FROM persons WHERE person_id = ?",
                (person_id,),
            )
            return bool(cursor.rowcount > 0)

    async def upsert_biometric_snapshot(
        self, snapshot: BiometricSnapshot
    ) -> BiometricSnapshot:
        """Insert or update a daily biometric snapshot."""
        async with self._db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO biometric_snapshots (
                    snapshot_id, person_id, snap_date, weight_kg, weight_ema_kg,
                    body_fat_pct, body_fat_ema_pct, active_kcal, total_kcal, steps,
                    computed_tdee, target_kcal, adaptive_tdee_kcal, wearable_multiplier_used
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(person_id, snap_date) DO UPDATE SET
                    weight_kg = COALESCE(excluded.weight_kg, biometric_snapshots.weight_kg),
                    weight_ema_kg = COALESCE(excluded.weight_ema_kg, biometric_snapshots.weight_ema_kg),
                    body_fat_pct = COALESCE(excluded.body_fat_pct, biometric_snapshots.body_fat_pct),
                    body_fat_ema_pct = COALESCE(excluded.body_fat_ema_pct, biometric_snapshots.body_fat_ema_pct),
                    active_kcal = COALESCE(excluded.active_kcal, biometric_snapshots.active_kcal),
                    total_kcal = COALESCE(excluded.total_kcal, biometric_snapshots.total_kcal),
                    steps = COALESCE(excluded.steps, biometric_snapshots.steps),
                    computed_tdee = COALESCE(excluded.computed_tdee, biometric_snapshots.computed_tdee),
                    target_kcal = COALESCE(excluded.target_kcal, biometric_snapshots.target_kcal),
                    adaptive_tdee_kcal = COALESCE(excluded.adaptive_tdee_kcal, biometric_snapshots.adaptive_tdee_kcal),
                    wearable_multiplier_used = COALESCE(excluded.wearable_multiplier_used, biometric_snapshots.wearable_multiplier_used)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.person_id,
                    snapshot.snap_date.isoformat(),
                    snapshot.weight_kg,
                    snapshot.weight_ema_kg,
                    snapshot.body_fat_pct,
                    snapshot.body_fat_ema_pct,
                    snapshot.active_kcal,
                    snapshot.total_kcal,
                    snapshot.steps,
                    snapshot.computed_tdee,
                    snapshot.target_kcal,
                    snapshot.adaptive_tdee_kcal,
                    snapshot.wearable_multiplier_used,
                ),
            )
        return snapshot

    async def get_biometric_snapshots_range(
        self,
        person_id: str,
        start_date: date,
        end_date: date,
    ) -> list[BiometricSnapshot]:
        """Return biometric snapshots across an inclusive date range."""
        rows = await self._db.fetch_all(
            """
            SELECT * FROM biometric_snapshots
            WHERE person_id = ? AND snap_date >= ? AND snap_date <= ?
            ORDER BY snap_date ASC
            """,
            (person_id, start_date.isoformat(), end_date.isoformat()),
        )
        return [self._row_to_snapshot(row) for row in rows]

    async def get_adaptive_state(self, person_id: str) -> AdaptiveState | None:
        """Fetch persisted closed-loop Adaptive TDEE state for a person."""
        row = await self._db.fetch_one(
            "SELECT * FROM adaptive_state WHERE person_id = ?",
            (person_id,),
        )
        if row is None:
            return None
        return AdaptiveState(
            person_id=str(row["person_id"]),
            last_calibrated_date=(
                date.fromisoformat(row["last_calibrated_date"])
                if row["last_calibrated_date"]
                else None
            ),
            adaptive_tdee_kcal=(
                float(row["adaptive_tdee_kcal"])
                if row["adaptive_tdee_kcal"] is not None
                else None
            ),
            wearable_multiplier=float(row["wearable_multiplier"]),
            confidence=str(row["confidence"] or "bootstrapping"),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    async def upsert_adaptive_state(self, state: AdaptiveState) -> AdaptiveState:
        """Insert or update persisted Adaptive TDEE state for a person."""
        now_iso = datetime.now(UTC).isoformat()
        async with self._db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO adaptive_state (
                    person_id, last_calibrated_date, adaptive_tdee_kcal,
                    wearable_multiplier, confidence, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(person_id) DO UPDATE SET
                    last_calibrated_date = excluded.last_calibrated_date,
                    adaptive_tdee_kcal = excluded.adaptive_tdee_kcal,
                    wearable_multiplier = excluded.wearable_multiplier,
                    confidence = excluded.confidence,
                    updated_at = excluded.updated_at
                """,
                (
                    state.person_id,
                    state.last_calibrated_date.isoformat()
                    if state.last_calibrated_date
                    else None,
                    state.adaptive_tdee_kcal,
                    state.wearable_multiplier,
                    state.confidence,
                    now_iso,
                ),
            )
        return state

    async def get_latest_biometric_snapshot(
        self, person_id: str
    ) -> BiometricSnapshot | None:
        """Return the most recent biometric snapshot for a person."""
        row = await self._db.fetch_one(
            """
            SELECT * FROM biometric_snapshots
            WHERE person_id = ?
            ORDER BY snap_date DESC
            LIMIT 1
            """,
            (person_id,),
        )
        return self._row_to_snapshot(row) if row else None

    async def get_recent_biometric_snapshots(
        self,
        person_id: str,
        before_date: date,
        days: int = 7,
    ) -> list[BiometricSnapshot]:
        """Return completed past biometric snapshots strictly before `before_date`."""
        since_date = before_date - timedelta(days=days)
        rows = await self._db.fetch_all(
            """
            SELECT * FROM biometric_snapshots
            WHERE person_id = ? AND snap_date >= ? AND snap_date < ?
            ORDER BY snap_date ASC
            """,
            (person_id, since_date.isoformat(), before_date.isoformat()),
        )
        return [self._row_to_snapshot(row) for row in rows]

    async def upsert_preference(self, pref: PreferenceEntry) -> PreferenceEntry:
        """Insert or update a global or person preference entry."""
        now = datetime.now(UTC).isoformat()
        pid = None if pref.scope == PreferenceScope.GLOBAL else pref.person_id
        if pref.scope == PreferenceScope.PERSON and not pid:
            raise ValueError("person_id is required when scope is 'person'")

        norm_target = normalize_text(pref.target_value)
        async with self._db.transaction() as conn:
            if pid is None:
                await conn.execute(
                    """
                    DELETE FROM person_preferences
                    WHERE scope = 'global' AND person_id IS NULL
                      AND target_type = ? AND lower(target_value) = ?
                    """,
                    (pref.target_type, norm_target),
                )
            else:
                await conn.execute(
                    """
                    DELETE FROM person_preferences
                    WHERE scope = 'person' AND person_id = ?
                      AND target_type = ? AND lower(target_value) = ?
                    """,
                    (pid, pref.target_type, norm_target),
                )

            await conn.execute(
                """
                INSERT INTO person_preferences (
                    pref_id, person_id, scope, target_type, target_value,
                    stance, notes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pref.pref_id or str(uuid.uuid4()),
                    pid,
                    pref.scope.value,
                    pref.target_type,
                    pref.target_value,
                    pref.stance.value,
                    pref.notes,
                    now,
                ),
            )
        return pref

    async def delete_preference(self, pref_id: str) -> bool:
        """Delete a preference entry by ID."""
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "DELETE FROM person_preferences WHERE pref_id = ?",
                (pref_id,),
            )
            return bool(cursor.rowcount > 0)

    async def get_effective_preferences(
        self, person_id: str
    ) -> list[PreferenceEntry]:
        """Merge global household preferences with strict per-person overrides."""
        global_rows = await self._db.fetch_all(
            "SELECT * FROM person_preferences WHERE scope = 'global'"
        )
        person_rows = await self._db.fetch_all(
            "SELECT * FROM person_preferences WHERE scope = 'person' AND person_id = ?",
            (person_id,),
        )

        merged: dict[tuple[str, str], PreferenceEntry] = {}
        for row in global_rows:
            entry = self._row_to_preference(row)
            key = (entry.target_type, normalize_text(entry.target_value))
            merged[key] = entry

        for row in person_rows:
            entry = self._row_to_preference(row)
            key = (entry.target_type, normalize_text(entry.target_value))
            merged[key] = entry

        return list(merged.values())

    @staticmethod
    def _row_to_snapshot(row: aiosqlite.Row) -> BiometricSnapshot:
        keys = row.keys()
        return BiometricSnapshot(
            snapshot_id=row["snapshot_id"],
            person_id=row["person_id"],
            snap_date=date.fromisoformat(row["snap_date"]),
            weight_kg=(
                float(row["weight_kg"]) if row["weight_kg"] is not None else None
            ),
            weight_ema_kg=(
                float(row["weight_ema_kg"])
                if row["weight_ema_kg"] is not None
                else None
            ),
            body_fat_pct=(
                float(row["body_fat_pct"])
                if row["body_fat_pct"] is not None
                else None
            ),
            body_fat_ema_pct=(
                float(row["body_fat_ema_pct"])
                if "body_fat_ema_pct" in keys and row["body_fat_ema_pct"] is not None
                else None
            ),
            active_kcal=(
                float(row["active_kcal"]) if row["active_kcal"] is not None else None
            ),
            total_kcal=(
                float(row["total_kcal"]) if row["total_kcal"] is not None else None
            ),
            steps=int(row["steps"]) if row["steps"] is not None else None,
            computed_tdee=(
                float(row["computed_tdee"])
                if row["computed_tdee"] is not None
                else None
            ),
            target_kcal=(
                float(row["target_kcal"]) if row["target_kcal"] is not None else None
            ),
            adaptive_tdee_kcal=(
                float(row["adaptive_tdee_kcal"])
                if "adaptive_tdee_kcal" in keys
                and row["adaptive_tdee_kcal"] is not None
                else None
            ),
            wearable_multiplier_used=(
                float(row["wearable_multiplier_used"])
                if "wearable_multiplier_used" in keys
                and row["wearable_multiplier_used"] is not None
                else None
            ),
        )

    @staticmethod
    def _row_to_preference(row: aiosqlite.Row) -> PreferenceEntry:
        return PreferenceEntry(
            pref_id=row["pref_id"],
            person_id=row["person_id"],
            scope=PreferenceScope(row["scope"]),
            target_type=row["target_type"],
            target_value=row["target_value"],
            stance=PreferenceStance(row["stance"]),
            notes=row["notes"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
