"""Idempotent baseline food catalog seeder."""

from __future__ import annotations

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
    raw_items = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    now = datetime.now(UTC).isoformat()
    inserted = 0

    async with db.transaction() as conn:
        for item in raw_items:
            food_id = item["food_id"]
            async with conn.execute(
                "SELECT 1 FROM global_foods WHERE food_id = ?",
                (food_id,),
            ) as cursor:
                exists = await cursor.fetchone()
            if exists is not None:
                continue

            aliases_norm = [normalize_text(a) for a in item.get("aliases", [])]
            aliases_text = "|".join(dict.fromkeys(a for a in aliases_norm if a))

            await conn.execute(
                """
                INSERT INTO global_foods (
                    food_id, name, normalized_name, aliases_text,
                    brand, normalized_brand, barcode_ean, category,
                    preparation_state, kcal_per_100g, protein_per_100g,
                    carbs_per_100g, fat_per_100g, fiber_per_100g,
                    label_standard, yield_factor, source, verified,
                    atwater_warning, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    food_id,
                    item["name"],
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

    if inserted > 0:
        log_event(_LOGGER, logging.INFO, "seed_foods_loaded", count=inserted)
    return inserted
