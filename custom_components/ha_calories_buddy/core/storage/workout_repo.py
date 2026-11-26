"""Repository for weekly recurring workouts, daily training overrides, and planner context."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import aiosqlite

from custom_components.ha_calories_buddy.core.models import (
    ScheduledCalendarWorkout,
    TrainingDayOverride,
    WorkoutActivityType,
    WorkoutScheduleItem,
)
from custom_components.ha_calories_buddy.core.storage.database import Database

_WEEKDAY_NAMES_EN = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
_WEEKDAY_NAMES_IT = (
    "Lunedi",
    "Martedi",
    "Mercoledi",
    "Giovedi",
    "Venerdi",
    "Sabato",
    "Domenica",
)


class WorkoutRepository:
    """Repository for weekly workout schedules and daily training-day overrides."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def list_schedule(self, person_id: str) -> list[WorkoutScheduleItem]:
        """List recurring weekly workout items for a person ordered by weekday & time."""
        rows = await self._db.fetch_all(
            """
            SELECT * FROM workout_schedules
            WHERE person_id = ?
            ORDER BY weekday ASC, time_of_day ASC, title ASC
            """,
            (person_id,),
        )
        return [self._row_to_schedule(row) for row in rows]

    async def upsert_schedule_item(
        self, item: WorkoutScheduleItem
    ) -> WorkoutScheduleItem:
        """Create or update a recurring workout schedule entry."""
        now_iso = datetime.now(UTC).isoformat()
        sid = item.schedule_id or f"wrk_{uuid.uuid4().hex[:10]}"
        async with self._db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO workout_schedules (
                    schedule_id, person_id, weekday, title, activity_type,
                    duration_min, estimated_kcal, time_of_day, notes, enabled, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(schedule_id) DO UPDATE SET
                    weekday = excluded.weekday,
                    title = excluded.title,
                    activity_type = excluded.activity_type,
                    duration_min = excluded.duration_min,
                    estimated_kcal = excluded.estimated_kcal,
                    time_of_day = excluded.time_of_day,
                    notes = excluded.notes,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    sid,
                    item.person_id,
                    int(item.weekday),
                    item.title.strip(),
                    item.activity_type.value,
                    int(item.duration_min),
                    item.estimated_kcal,
                    item.time_of_day.strip() or "18:00",
                    item.notes.strip(),
                    1 if item.enabled else 0,
                    now_iso,
                ),
            )
        return item.model_copy(update={"schedule_id": sid})

    async def replace_weekly_schedule(
        self, person_id: str, items: list[WorkoutScheduleItem]
    ) -> list[WorkoutScheduleItem]:
        """Atomically replace a person's entire recurring weekly schedule."""
        now_iso = datetime.now(UTC).isoformat()
        saved: list[WorkoutScheduleItem] = []
        async with self._db.transaction() as conn:
            await conn.execute(
                "DELETE FROM workout_schedules WHERE person_id = ?",
                (person_id,),
            )
            for item in items:
                sid = item.schedule_id or f"wrk_{uuid.uuid4().hex[:10]}"
                await conn.execute(
                    """
                    INSERT INTO workout_schedules (
                        schedule_id, person_id, weekday, title, activity_type,
                        duration_min, estimated_kcal, time_of_day, notes, enabled, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sid,
                        person_id,
                        int(item.weekday),
                        item.title.strip(),
                        item.activity_type.value,
                        int(item.duration_min),
                        item.estimated_kcal,
                        item.time_of_day.strip() or "18:00",
                        item.notes.strip(),
                        1 if item.enabled else 0,
                        now_iso,
                    ),
                )
                saved.append(item.model_copy(update={"schedule_id": sid, "person_id": person_id}))
        return saved

    async def delete_schedule_item(self, person_id: str, schedule_id: str) -> bool:
        """Delete a recurring workout schedule item."""
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "DELETE FROM workout_schedules WHERE person_id = ? AND schedule_id = ?",
                (person_id, schedule_id),
            )
            return bool(cursor.rowcount > 0)

    async def get_override(
        self, person_id: str, target_date: date
    ) -> TrainingDayOverride | None:
        """Fetch an explicit training-day override for target_date, if set."""
        row = await self._db.fetch_one(
            """
            SELECT * FROM training_day_overrides
            WHERE person_id = ? AND override_date = ?
            """,
            (person_id, target_date.isoformat()),
        )
        if row is None:
            return None
        return TrainingDayOverride(
            person_id=str(row["person_id"]),
            override_date=date.fromisoformat(str(row["override_date"])),
            is_training_day=bool(int(row["is_training_day"])),
            source=str(row["source"] or "manual"),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    async def set_override(
        self, override: TrainingDayOverride
    ) -> TrainingDayOverride:
        """Set or replace an explicit training-day override for a date."""
        now_iso = datetime.now(UTC).isoformat()
        async with self._db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO training_day_overrides (
                    person_id, override_date, is_training_day, source, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(person_id, override_date) DO UPDATE SET
                    is_training_day = excluded.is_training_day,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (
                    override.person_id,
                    override.override_date.isoformat(),
                    1 if override.is_training_day else 0,
                    override.source,
                    now_iso,
                ),
            )
        return override

    async def clear_override(self, person_id: str, target_date: date) -> bool:
        """Clear an explicit training-day override so auto schedule/calendar resumes."""
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                """
                DELETE FROM training_day_overrides
                WHERE person_id = ? AND override_date = ?
                """,
                (person_id, target_date.isoformat()),
            )
            return bool(cursor.rowcount > 0)

    async def get_scheduled_for_date(
        self, person_id: str, target_date: date
    ) -> list[WorkoutScheduleItem]:
        """Return enabled recurring workouts matching target_date.weekday()."""
        rows = await self._db.fetch_all(
            """
            SELECT * FROM workout_schedules
            WHERE person_id = ? AND weekday = ? AND enabled = 1
            ORDER BY time_of_day ASC, title ASC
            """,
            (person_id, target_date.weekday()),
        )
        return [self._row_to_schedule(row) for row in rows]

    async def build_workout_prompt_context(
        self,
        person_id: str,
        start_date: date,
        num_days: int,
        *,
        language: str = "en",
        calendar_workouts_by_date: dict[date, list[ScheduledCalendarWorkout]] | None = None,
    ) -> str:
        """Build a concise day-by-day workout schedule context for AI Meal Planner prompts."""
        schedule_items = [
            it for it in await self.list_schedule(person_id) if it.enabled
        ]
        by_weekday: dict[int, list[WorkoutScheduleItem]] = {}
        for item in schedule_items:
            by_weekday.setdefault(item.weekday, []).append(item)

        cal_map = calendar_workouts_by_date or {}
        if not schedule_items and not cal_map:
            return ""

        day_names = _WEEKDAY_NAMES_IT if language == "it" else _WEEKDAY_NAMES_EN
        lines: list[str] = []
        has_any_training = False

        for offset in range(max(1, num_days)):
            d = start_date + timedelta(days=offset)
            ovr = await self.get_override(person_id, d)
            sched_for_day = by_weekday.get(d.weekday(), [])
            cal_for_day = cal_map.get(d, [])

            if ovr is not None:
                is_train = ovr.is_training_day
            elif cal_for_day or sched_for_day:
                is_train = True
            else:
                is_train = False

            if is_train:
                has_any_training = True
                parts: list[str] = []
                for cw in cal_for_day:
                    parts.append(f"{cw.summary} (calendar)")
                for sw in sched_for_day:
                    parts.append(f"{sw.title} [{sw.activity_type.value}, {sw.duration_min}m]")
                desc = "; ".join(parts) if parts else "Training session"
                lines.append(
                    f"- {d.isoformat()} ({day_names[d.weekday()]}): TRAINING DAY — {desc}"
                )
            else:
                lines.append(
                    f"- {d.isoformat()} ({day_names[d.weekday()]}): REST DAY"
                )

        if not has_any_training:
            return ""
        return "\n".join(lines)

    @staticmethod
    def _row_to_schedule(row: aiosqlite.Row) -> WorkoutScheduleItem:
        try:
            act_type = WorkoutActivityType(str(row["activity_type"]))
        except ValueError:
            act_type = WorkoutActivityType.OTHER
        return WorkoutScheduleItem(
            schedule_id=str(row["schedule_id"]),
            person_id=str(row["person_id"]),
            weekday=int(row["weekday"]),
            title=str(row["title"]),
            activity_type=act_type,
            duration_min=int(row["duration_min"]),
            estimated_kcal=(
                float(row["estimated_kcal"])
                if row["estimated_kcal"] is not None
                else None
            ),
            time_of_day=str(row["time_of_day"] or "18:00"),
            notes=str(row["notes"] or ""),
            enabled=bool(int(row["enabled"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )
