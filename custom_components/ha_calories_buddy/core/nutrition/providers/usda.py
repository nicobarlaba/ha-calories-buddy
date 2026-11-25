"""Optional async USDA FoodData Central client with US Atwater validation."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import aiohttp

from custom_components.ha_calories_buddy.core.logging import get_logger, log_event
from custom_components.ha_calories_buddy.core.models import (
    FoodItem,
    FoodSource,
    LabelStandard,
    Macros,
    PreparationState,
)
from custom_components.ha_calories_buddy.core.nutrition.atwater import check_atwater
from custom_components.ha_calories_buddy.core.nutrition.yield_factors import (
    default_yield_for_category,
)
from custom_components.ha_calories_buddy.core.storage.seeder import normalize_text

_LOGGER = get_logger(__name__)
USDA_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"

NUTRIENT_ENERGY_KCAL = 1008
NUTRIENT_PROTEIN = 1003
NUTRIENT_CARBS = 1005
NUTRIENT_FAT = 1004
NUTRIENT_FIBER = 1079


class USDAProvider:
    """Async client for USDA FoodData Central (enabled when an API key is supplied)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str | None = None,
        timeout_s: float = 6.0,
    ) -> None:
        self._session = session
        self._api_key = api_key.strip() if api_key else None
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)

    @property
    def enabled(self) -> bool:
        """Return True if a USDA API key is configured."""
        return bool(self._api_key)

    async def lookup_barcode(self, ean: str) -> FoodItem | None:
        """Search USDA by GTIN/UPC if enabled."""
        if not self._api_key or not ean.strip():
            return None
        results = await self.search(ean.strip(), limit=1)
        return results[0] if results else None

    async def search(
        self,
        query: str,
        brand: str | None = None,
        language: str = "en",
        limit: int = 5,
    ) -> list[FoodItem]:
        """Search USDA FoodData Central foods."""
        if not self._api_key or not query.strip():
            return []

        terms = f"{query} {brand}".strip() if brand else query.strip()
        params: dict[str, str | int] = {
            "api_key": self._api_key,
            "query": terms,
            "pageSize": max(1, min(limit, 10)),
        }
        try:
            async with self._session.get(
                USDA_SEARCH_URL,
                params=params,
                timeout=self._timeout,
            ) as resp:
                if resp.status != 200:
                    log_event(
                        _LOGGER,
                        logging.WARNING,
                        "usda_search_http_error",
                        status=resp.status,
                    )
                    return []
                payload = await resp.json()
        except (TimeoutError, aiohttp.ClientError, ValueError) as exc:
            log_event(
                _LOGGER,
                logging.WARNING,
                "usda_search_request_failed",
                error_type=type(exc).__name__,
            )
            return []

        foods = payload.get("foods", [])
        if not isinstance(foods, list):
            return []

        results: list[FoodItem] = []
        for entry in foods:
            if not isinstance(entry, dict):
                continue
            item = self._entry_to_food_item(entry)
            if item is not None:
                results.append(item)
        return results[:limit]

    @staticmethod
    def _entry_to_food_item(entry: dict[str, Any]) -> FoodItem | None:
        name = entry.get("description")
        if not isinstance(name, str) or not name.strip():
            return None
        name = name.strip()

        nutrients: dict[int, float] = {}
        for nut in entry.get("foodNutrients", []):
            nid = nut.get("nutrientId")
            val = nut.get("value")
            if isinstance(nid, int) and isinstance(val, (int, float)) and val >= 0:
                nutrients[nid] = float(val)

        if (
            NUTRIENT_ENERGY_KCAL not in nutrients
            or NUTRIENT_PROTEIN not in nutrients
            or NUTRIENT_CARBS not in nutrients
            or NUTRIENT_FAT not in nutrients
        ):
            return None

        macros = Macros(
            kcal=round(nutrients[NUTRIENT_ENERGY_KCAL], 1),
            protein_g=round(nutrients[NUTRIENT_PROTEIN], 1),
            carbs_g=round(nutrients[NUTRIENT_CARBS], 1),
            fat_g=round(nutrients[NUTRIENT_FAT], 1),
            fiber_g=(
                round(nutrients[NUTRIENT_FIBER], 1)
                if NUTRIENT_FIBER in nutrients
                else None
            ),
        )
        atwater = check_atwater(macros, LabelStandard.US)

        lower_name = name.lower()
        state = PreparationState.RAW
        if "cooked" in lower_name or "boiled" in lower_name or "grilled" in lower_name:
            state = PreparationState.COOKED
        elif "raw" not in lower_name:
            state = PreparationState.READY_TO_EAT

        category = "other"
        if "rice" in lower_name:
            category = "rice"
        elif "pasta" in lower_name:
            category = "pasta"
        elif "lentil" in lower_name or "bean" in lower_name or "chickpea" in lower_name:
            category = "legumes"
        elif "chicken" in lower_name or "turkey" in lower_name:
            category = "poultry"

        fdc_id = entry.get("fdcId")
        food_id = f"usda_{fdc_id}" if fdc_id else f"usda_{uuid.uuid4().hex[:12]}"
        brand = entry.get("brandOwner") or entry.get("brandName")

        return FoodItem(
            food_id=food_id,
            name=name,
            normalized_name=normalize_text(name),
            aliases=[normalize_text(name)],
            brand=str(brand).strip() if brand else None,
            barcode_ean=str(entry["gtinUpc"]).strip() if entry.get("gtinUpc") else None,
            category=category,
            preparation_state=state,
            macros_per_100g=macros,
            label_standard=LabelStandard.US,
            yield_factor=default_yield_for_category(category),
            source=FoodSource.USDA,
            verified=False,
            atwater_warning=atwater.warning,
        )
