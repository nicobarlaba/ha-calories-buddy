"""Idempotent baseline food catalog seeder."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
import uuid
from datetime import UTC, datetime
from pathlib import Path

from custom_components.ha_calories_buddy.core.logging import get_logger, log_event
from custom_components.ha_calories_buddy.core.storage.database import Database

_LOGGER = get_logger(__name__)
SEED_FILE = Path(__file__).resolve().parent / "seed_foods.json"


def normalize_text(value: str) -> str:
    """Normalize food or brand text for accent-insensitive, punctuation-free matching."""
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^a-z0-9\s]", " ", ascii_only.lower())
    return " ".join(cleaned.split())


async def seed_database_if_empty(db: Database) -> int:
    """Load baseline seed foods idempotently without overwriting user modifications."""
    raw_text = await asyncio.to_thread(SEED_FILE.read_text, encoding="utf-8")
    raw_items = json.loads(raw_text)
    now = datetime.now(UTC).isoformat()
    inserted = 0
    backfilled = 0

    async with db.transaction() as conn:
        for item in raw_items:
            food_id = item["food_id"]
            name_it = item.get("name_it")
            aliases_norm = [normalize_text(a) for a in item.get("aliases", [])]
            if name_it:
                aliases_norm.append(normalize_text(name_it))
            aliases_text = "|".join(dict.fromkeys(a for a in aliases_norm if a))

            async with conn.execute(
                "SELECT source, name_it, aliases_text FROM global_foods WHERE food_id = ?",
                (food_id,),
            ) as cursor:
                existing = await cursor.fetchone()

            if existing is not None:
                # Idempotently backfill Italian display names & paired aliases on untouched seed rows
                if existing["source"] == "seed" and (
                    existing["name_it"] != name_it
                    or existing["aliases_text"] != aliases_text
                ):
                    await conn.execute(
                        """
                        UPDATE global_foods
                        SET name_it = ?, aliases_text = ?
                        WHERE food_id = ? AND source = 'seed'
                        """,
                        (name_it, aliases_text, food_id),
                    )
                    backfilled += 1
                continue

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
                """,
                (
                    food_id,
                    item["name"],
                    name_it,
                    normalize_text(item["name"]),
                    aliases_text,
                    None,
                    None,
                    None,
                    item.get("category", "other"),
                    item.get("preparation_state", "ready_to_eat"),
                    float(item["kcal"]),
                    float(item["protein_g"]),
                    float(item["carbs_g"]),
                    float(item["fat_g"]),
                    float(item["fiber_g"]) if item.get("fiber_g") is not None else None,
                    "eu",
                    float(item.get("yield_factor", 1.0)),
                    "seed",
                    1,
                    0,
                    now,
                ),
            )

            for portion in item.get("portions", []):
                await conn.execute(
                    """
                    INSERT OR IGNORE INTO food_portions (
                        portion_id, food_id, unit_name, grams, description
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        food_id,
                        portion["unit_name"].lower(),
                        float(portion["grams"]),
                        portion.get("description", ""),
                    ),
                )
            inserted += 1

    if inserted > 0 or backfilled > 0:
        log_event(
            _LOGGER,
            logging.INFO,
            "seed_foods_synced",
            inserted=inserted,
            backfilled=backfilled,
        )
    return inserted
