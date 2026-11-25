"""Async SQLite connection manager and schema migration runner."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from custom_components.ha_calories_buddy.core.logging import get_logger, log_event

_LOGGER = get_logger(__name__)
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


_ALTER_ADD_COL_RE = re.compile(
    r"^\s*ALTER\s+TABLE\s+([A-Za-z_][A-Za-z0-9_]*)\s+ADD\s+COLUMN\s+([A-Za-z_][A-Za-z0-9_]*)\b",
    re.IGNORECASE,
)


async def _column_exists(
    conn: aiosqlite.Connection, table_name: str, column_name: str
) -> bool:
    """Return True if column_name already exists on table_name."""
    async with conn.execute(f"PRAGMA table_info({table_name})") as cursor:
        rows = await cursor.fetchall()
    target = column_name.lower()
    return any(str(row[1]).lower() == target for row in rows)


def _split_sql_statements(sql_script: str) -> list[str]:
    """Strip SQL line comments and split semicolon-delimited DDL statements."""
    clean_lines = [
        line for line in sql_script.splitlines() if not line.lstrip().startswith("--")
    ]
    cleaned = "\n".join(clean_lines)
    return [stmt.strip() for stmt in cleaned.split(";") if stmt.strip()]


class Database:
    """Async SQLite wrapper with WAL mode, foreign keys, and versioned migrations."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = str(db_path)
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    @property
    def connection(self) -> aiosqlite.Connection:
        """Return active SQLite connection or raise if uninitialized."""
        if self._conn is None:
            raise RuntimeError("Database has not been initialized")
        return self._conn

    async def initialize(self) -> None:
        """Open connection, configure pragmas, and apply pending SQL migrations."""
        if self._conn is not None:
            return

        if self._db_path != ":memory:":
            await asyncio.to_thread(
                Path(self._db_path).parent.mkdir, parents=True, exist_ok=True
            )

        conn = await aiosqlite.connect(self._db_path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA busy_timeout=5000")
        self._conn = conn

        await self._apply_migrations()
        log_event(_LOGGER, logging.INFO, "database_initialized", path=self._db_path)

    async def close(self) -> None:
        """Close the SQLite connection cleanly."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            log_event(_LOGGER, logging.INFO, "database_closed")

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Serialize write operations and commit/rollback atomically."""
        conn = self.connection
        async with self._write_lock:
            try:
                yield conn
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    async def fetch_one(
        self, query: str, params: tuple[Any, ...] = ()
    ) -> aiosqlite.Row | None:
        """Execute a read query returning at most one row."""
        async with self.connection.execute(query, params) as cursor:
            row: aiosqlite.Row | None = await cursor.fetchone()
            return row

    async def fetch_all(
        self, query: str, params: tuple[Any, ...] = ()
    ) -> list[aiosqlite.Row]:
        """Execute a read query returning all matching rows."""
        async with self.connection.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return list(rows)

    async def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        """Execute a single write statement inside a serialized transaction."""
        async with self.transaction() as conn:
            await conn.execute(query, params)

    async def _apply_migrations(self) -> None:
        """Apply ordered *.sql files from migrations/ idempotently under the write lock."""
        conn = self.connection
        migration_files = await asyncio.to_thread(
            lambda: sorted(MIGRATIONS_DIR.glob("*.sql"))
        )
        file_contents: list[tuple[int, str]] = []
        for migration_file in migration_files:
            version_str = migration_file.name.split("_", maxsplit=1)[0]
            if not version_str.isdigit():
                continue
            sql_script = await asyncio.to_thread(
                migration_file.read_text, encoding="utf-8"
            )
            file_contents.append((int(version_str), sql_script))

        async with self._write_lock:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            await conn.commit()

            async with conn.execute("SELECT version FROM schema_migrations") as cursor:
                applied_rows = await cursor.fetchall()
            applied_versions = {int(row["version"]) for row in applied_rows}

            for version, sql_script in file_contents:
                if version in applied_versions:
                    continue
                try:
                    await self._execute_migration_script(conn, sql_script)
                    await conn.execute(
                        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                        (version, datetime.now(UTC).isoformat()),
                    )
                    await conn.commit()
                    log_event(
                        _LOGGER,
                        logging.INFO,
                        "database_migration_applied",
                        version=version,
                    )
                except Exception:
                    await conn.rollback()
                    raise

    @staticmethod
    async def _execute_migration_script(
        conn: aiosqlite.Connection, sql_script: str
    ) -> None:
        """Execute migration DDL, skipping ALTER ADD COLUMN if already present."""
        statements = _split_sql_statements(sql_script)
        for stmt in statements:
            match = _ALTER_ADD_COL_RE.match(stmt)
            if match:
                table_name, col_name = match.group(1), match.group(2)
                if await _column_exists(conn, table_name, col_name):
                    continue
            await conn.execute(stmt)
