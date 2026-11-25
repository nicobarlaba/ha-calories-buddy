"""Async Open Food Facts API client with EU Atwater validation."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

import aiohttp

from custom_components.ha_calories_buddy.core.logging import get_logger, log_event
from custom_components.ha_calories_buddy.core.models import (
    FoodItem,
    FoodPortion,
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
USER_AGENT = "ha-calories-buddy/0.1 (Home Assistant Custom Integration)"
OFF_PRODUCT_URL = "https://world.openfoodfacts.org/api/v2/product/{ean}.json"
OFF_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
        return num if num >= 0.0 else None
    except (TypeError, ValueError):
        return None


def _infer_category_and_state(
    name: str, tags: list[str]
) -> tuple[str, PreparationState]:
    joined = " ".join(tags).lower() + " " + name.lower()
    if any(k in joined for k in ("olive-oil", "olio", "olive oil", "vegetable-oil")):
        return "oil", PreparationState.READY_TO_EAT
    if any(k in joined for k in ("basmati", "rice", "risi", "riso")):
        state = (
            PreparationState.COOKED
            if any(c in joined for c in ("cooked", "cotto", "steamed"))
            else PreparationState.RAW
        )
        return "rice", state
    if any(k in joined for k in ("pasta", "spaghetti", "penne", "fusilli")):
        state = (
            PreparationState.COOKED
            if any(c in joined for c in ("cooked", "cotta", "cotti"))
            else PreparationState.RAW
        )
        return "pasta", state
    if any(k in joined for k in ("lentil", "lenticch", "chickpea", "ceci", "bean", "fagiol")):
        if any(c in joined for c in ("canned", "in-tin", "scatola", "cooked", "bottl", "vetro")):
            return "legumes", PreparationState.COOKED
        return "legumes", PreparationState.RAW
    if any(k in joined for k in ("oat", "avena")):
        return "oats", PreparationState.RAW
    if any(k in joined for k in ("yogurt", "milk", "latte", "cheese", "formagg")):
        return "dairy", PreparationState.READY_TO_EAT
    if any(k in joined for k in ("chicken", "pollo", "turkey", "tacchino")):
        return "poultry", PreparationState.RAW
    if any(k in joined for k in ("salmon", "tuna", "tonno", "cod", "fish", "pesce")):
        if any(c in joined for c in ("canned", "scatola", "smoked", "affumicat")):
            return "fish", PreparationState.READY_TO_EAT
        return "fish", PreparationState.RAW
    if any(k in joined for k in ("bread", "pane", "fette")):
        return "bread", PreparationState.READY_TO_EAT
    return "other", PreparationState.READY_TO_EAT


class OpenFoodFactsProvider:
    """Async Open Food Facts client."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        timeout_s: float = 6.0,
    ) -> None:
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)

    async def lookup_barcode(self, ean: str) -> FoodItem | None:
        """Fetch product details from Open Food Facts by EAN/UPC code."""
        clean_ean = re.sub(r"\D", "", ean)
        if not clean_ean:
            return None

        url = OFF_PRODUCT_URL.format(ean=clean_ean)
        params = {
            "fields": (
                "code,product_name,product_name_it,product_name_en,brands,"
                "nutriments,serving_quantity,categories_tags"
            )
        }
        try:
            async with self._session.get(
                url,
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=self._timeout,
            ) as resp:
                if resp.status != 200:
                    log_event(
                        _LOGGER,
                        logging.WARNING,
                        "off_barcode_http_error",
                        status=resp.status,
                    )
                    return None
                payload = await resp.json()
        except (TimeoutError, aiohttp.ClientError, ValueError) as exc:
            log_event(
                _LOGGER,
                logging.WARNING,
                "off_barcode_request_failed",
                error_type=type(exc).__name__,
            )
            return None

        if payload.get("status") != 1 or not isinstance(payload.get("product"), dict):
            return None
        return self._product_to_food_item(payload["product"], language="it")

    async def search(
        self,
        query: str,
        brand: str | None = None,
        language: str = "en",
        limit: int = 5,
    ) -> list[FoodItem]:
        """Search Open Food Facts products by text and optional brand."""
        terms = f"{query} {brand}".strip() if brand else query.strip()
        if not terms:
            return []

        params: dict[str, str | int] = {
            "search_terms": terms,
            "search_simple": 1,
            "action": "process",
            "json": 1,
            "page_size": max(1, min(limit, 20)),
            "fields": (
                "code,product_name,product_name_it,product_name_en,brands,"
                "nutriments,serving_quantity,categories_tags"
            ),
        }
        try:
            async with self._session.get(
                OFF_SEARCH_URL,
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=self._timeout,
            ) as resp:
                if resp.status != 200:
                    log_event(
                        _LOGGER,
                        logging.WARNING,
                        "off_search_http_error",
                        status=resp.status,
                    )
                    return []
                payload = await resp.json()
        except (TimeoutError, aiohttp.ClientError, ValueError) as exc:
            log_event(
                _LOGGER,
                logging.WARNING,
                "off_search_request_failed",
                error_type=type(exc).__name__,
            )
            return []

        products = payload.get("products", [])
        if not isinstance(products, list):
            return []

        items: list[FoodItem] = []
        for prod in products:
            if not isinstance(prod, dict):
                continue
            item = self._product_to_food_item(prod, language=language)
            if item is not None:
                items.append(item)
            if len(items) >= limit:
                break
        return items

    @staticmethod
    def _product_to_food_item(
        product: dict[str, Any], language: str
    ) -> FoodItem | None:
        nutriments = product.get("nutriments")
        if not isinstance(nutriments, dict):
            return None

        name = (
            product.get(f"product_name_{language}")
            or product.get("product_name_it")
            or product.get("product_name_en")
            or product.get("product_name")
        )
        if not isinstance(name, str) or not name.strip():
            return None
        name = name.strip()

        kcal = _to_float(nutriments.get("energy-kcal_100g"))
        if kcal is None:
            kj = _to_float(nutriments.get("energy-kj_100g"))
            if kj is None and nutriments.get("energy_unit") == "kJ":
                kj = _to_float(nutriments.get("energy_100g"))
            if kj is not None:
                kcal = round(kj / 4.184, 1)

        protein = _to_float(nutriments.get("proteins_100g"))
        carbs = _to_float(nutriments.get("carbohydrates_100g"))
        fat = _to_float(nutriments.get("fat_100g"))
        fiber = _to_float(nutriments.get("fiber_100g"))

        if kcal is None or protein is None or carbs is None or fat is None:
            return None

        macros = Macros(
            kcal=round(kcal, 1),
            protein_g=round(protein, 1),
            carbs_g=round(carbs, 1),
            fat_g=round(fat, 1),
            fiber_g=round(fiber, 1) if fiber is not None else None,
        )
        atwater = check_atwater(macros, LabelStandard.EU)

        brands_raw = product.get("brands")
        brand = (
            brands_raw.split(",")[0].strip()
            if isinstance(brands_raw, str) and brands_raw.strip()
            else None
        )
        barcode_raw = product.get("code")
        barcode = str(barcode_raw).strip() if barcode_raw else None

        tags = product.get("categories_tags") or []
        if not isinstance(tags, list):
            tags = []
        category, state = _infer_category_and_state(name, [str(t) for t in tags])
        yield_factor = default_yield_for_category(category)

        food_id = f"off_{barcode}" if barcode else f"off_{uuid.uuid4().hex[:12]}"
        portions: list[FoodPortion] = []
        serving_qty = _to_float(product.get("serving_quantity"))
        if serving_qty is not None and serving_qty > 0.0:
            portions.append(
                FoodPortion(
                    food_id=food_id,
                    unit_name="serving",
                    grams=round(serving_qty, 1),
                    description="Package serving",
                )
            )

        return FoodItem(
            food_id=food_id,
            name=name,
            normalized_name=normalize_text(name),
            aliases=[normalize_text(name)],
            brand=brand,
            barcode_ean=barcode,
            category=category,
            preparation_state=state,
            macros_per_100g=macros,
            label_standard=LabelStandard.EU,
            yield_factor=yield_factor,
            source=FoodSource.OPEN_FOOD_FACTS,
            verified=False,
            atwater_warning=atwater.warning,
            portions=portions,
        )
