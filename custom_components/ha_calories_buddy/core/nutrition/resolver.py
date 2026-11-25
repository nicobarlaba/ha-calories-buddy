"""Four-tier Food Resolution Waterfall and deterministic item computation."""

from __future__ import annotations

import logging
from typing import Protocol

from pydantic import BaseModel

from custom_components.ha_calories_buddy.core.logging import get_logger, log_event
from custom_components.ha_calories_buddy.core.models import (
    ConfidenceLevel,
    EffectiveFood,
    FoodItem,
    FoodSource,
    LabelStandard,
    Macros,
    PreparationState,
    WarningCode,
)
from custom_components.ha_calories_buddy.core.nutrition.calculator import (
    compute_macros_for_grams,
)
from custom_components.ha_calories_buddy.core.nutrition.providers.base import FoodProvider
from custom_components.ha_calories_buddy.core.nutrition.units import (
    convert_to_grams,
    normalize_unit,
)
from custom_components.ha_calories_buddy.core.nutrition.yield_factors import (
    convert_to_reference_grams,
    resolve_preparation_state,
)
from custom_components.ha_calories_buddy.core.storage.food_repo import FoodRepository
from custom_components.ha_calories_buddy.core.storage.seeder import normalize_text

_LOGGER = get_logger(__name__)


class LLMEstimatorProtocol(Protocol):
    """Protocol for last-resort LLM food macro estimation."""

    async def estimate_food(
        self,
        name: str,
        brand: str | None,
        preparation_state: PreparationState,
        language: str,
    ) -> FoodItem | None:
        """Estimate nutrition per 100g when structured sources have no match."""


class ResolvedMealItem(BaseModel):
    """Fully resolved, deterministically computed meal item ready for persistence."""

    food_id: str | None = None
    display_name: str
    brand: str | None = None
    input_quantity: float
    input_unit: str
    input_state: PreparationState
    effective_state: PreparationState
    resolved_grams_ref: float
    macros: Macros
    confidence: ConfidenceLevel
    warning_code: WarningCode | None = None
    warning_detail: str | None = None
    food_source: FoodSource | None = None


class FoodResolver:
    """Resolve food items in strict waterfall order and compute exact macros."""

    def __init__(
        self,
        food_repo: FoodRepository,
        off_provider: FoodProvider | None = None,
        usda_provider: FoodProvider | None = None,
    ) -> None:
        self._food_repo = food_repo
        self._off = off_provider
        self._usda = usda_provider

    async def lookup_barcode(
        self, barcode: str, person_id: str | None = None
    ) -> EffectiveFood | None:
        """Resolve barcode via local Vault first, then Open Food Facts (auto-caching)."""
        existing = await self._food_repo.get_by_barcode(barcode, person_id=person_id)
        if existing is not None:
            return existing

        if self._off is not None:
            external = await self._off.lookup_barcode(barcode)
            if external is not None:
                await self._food_repo.upsert_global_food(external)
                return await self._food_repo.get_effective_food(
                    external.food_id, person_id=person_id
                )
        return None

    async def search_foods(
        self,
        query: str,
        person_id: str | None = None,
        brand: str | None = None,
        language: str = "en",
        include_external: bool = True,
        limit: int = 15,
    ) -> list[EffectiveFood]:
        """Search local Vault and optionally merge external Open Food Facts hits."""
        local = await self._food_repo.search(
            query, person_id=person_id, brand=brand, limit=limit
        )
        if not include_external or self._off is None or len(local) >= limit:
            return local

        external_items = await self._off.search(
            query, brand=brand, language=language, limit=max(1, limit - len(local))
        )
        seen_ids = {f.food_id for f in local}
        seen_barcodes = {f.barcode_ean for f in local if f.barcode_ean}
        merged = list(local)
        for ext in external_items:
            if ext.food_id in seen_ids or (
                ext.barcode_ean and ext.barcode_ean in seen_barcodes
            ):
                continue
            await self._food_repo.upsert_global_food(ext)
            eff = await self._food_repo.get_effective_food(
                ext.food_id, person_id=person_id
            )
            if eff is not None:
                merged.append(eff)
                seen_ids.add(eff.food_id)
        return merged[:limit]

    async def resolve_item(
        self,
        *,
        person_id: str,
        name: str,
        quantity: float,
        unit: str = "g",
        state: PreparationState = PreparationState.UNSPECIFIED,
        brand: str | None = None,
        barcode: str | None = None,
        food_id: str | None = None,
        language: str = "en",
        llm_estimator: LLMEstimatorProtocol | None = None,
    ) -> ResolvedMealItem:
        """Resolve one meal item through the 4-tier waterfall and compute macros."""
        canonical_unit = normalize_unit(unit)
        effective_food = await self._resolve_food_entity(
            person_id=person_id,
            name=name,
            brand=brand,
            state=state,
            barcode=barcode,
            food_id=food_id,
            language=language,
        )

        # Tier 4: Last-resort LLM estimation (never blindly saved as verified)
        if effective_food is None and llm_estimator is not None:
            estimated = await llm_estimator.estimate_food(
                name=name,
                brand=brand,
                preparation_state=state,
                language=language,
            )
            if estimated is not None:
                effective_food = self._food_to_effective_unpersisted(estimated)

        if effective_food is None:
            log_event(
                _LOGGER,
                logging.INFO,
                "food_resolution_missed",
                has_brand=bool(brand),
            )
            return ResolvedMealItem(
                food_id=None,
                display_name=f"{name} ({brand})" if brand else name,
                brand=brand,
                input_quantity=quantity,
                input_unit=canonical_unit,
                input_state=state,
                effective_state=state,
                resolved_grams_ref=0.0,
                macros=Macros(kcal=0.0, protein_g=0.0, carbs_g=0.0, fat_g=0.0),
                confidence=ConfidenceLevel.LOW,
                warning_code=WarningCode.LLM_ESTIMATED,
                warning_detail="Food not found in database or LLM offline",
                food_source=None,
            )

        input_grams, unknown_unit = convert_to_grams(
            quantity, canonical_unit, effective_food
        )
        effective_state, is_ambiguous = resolve_preparation_state(
            category=effective_food.category,
            input_grams=input_grams,
            input_state=state,
            ref_state=effective_food.preparation_state,
        )
        ref_grams = convert_to_reference_grams(
            input_grams=input_grams,
            effective_state=effective_state,
            ref_state=effective_food.preparation_state,
            yield_factor=effective_food.yield_factor,
        )
        macros = compute_macros_for_grams(effective_food.macros_per_100g, ref_grams)

        confidence = ConfidenceLevel.HIGH
        warning_code: WarningCode | None = None
        warning_detail: str | None = None

        if effective_food.source == FoodSource.LLM_ESTIMATED:
            confidence = ConfidenceLevel.LOW
            warning_code = WarningCode.LLM_ESTIMATED
            warning_detail = "Nutritional values estimated by AI; please verify"
        elif effective_food.atwater_warning and not effective_food.verified:
            confidence = ConfidenceLevel.MEDIUM
            warning_code = WarningCode.ATWATER_WARNING
            warning_detail = "Product label calories diverge from macro sum"
        elif is_ambiguous:
            confidence = ConfidenceLevel.MEDIUM
            warning_code = WarningCode.AMBIGUOUS_STATE
            warning_detail = "Raw vs cooked weight was not specified"
        elif unknown_unit:
            confidence = ConfidenceLevel.MEDIUM
            warning_code = WarningCode.UNKNOWN_UNIT
            warning_detail = f"Unknown unit '{canonical_unit}' defaulted to 100g"

        return ResolvedMealItem(
            food_id=(
                None
                if effective_food.source == FoodSource.LLM_ESTIMATED
                else effective_food.food_id
            ),
            display_name=effective_food.preferred_alias or effective_food.name,
            brand=effective_food.brand or brand,
            input_quantity=quantity,
            input_unit=canonical_unit,
            input_state=state,
            effective_state=effective_state,
            resolved_grams_ref=round(ref_grams, 2),
            macros=macros,
            confidence=confidence,
            warning_code=warning_code,
            warning_detail=warning_detail,
            food_source=effective_food.source,
        )

    async def _resolve_food_entity(
        self,
        *,
        person_id: str,
        name: str,
        brand: str | None,
        state: PreparationState,
        barcode: str | None,
        food_id: str | None,
        language: str,
    ) -> EffectiveFood | None:
        if food_id:
            by_id = await self._food_repo.get_effective_food(
                food_id, person_id=person_id
            )
            if by_id is not None:
                return by_id

        if barcode:
            by_barcode = await self.lookup_barcode(barcode, person_id=person_id)
            if by_barcode is not None:
                return by_barcode

        preferred_state = None if state == PreparationState.UNSPECIFIED else state

        # Tier 1 & 2 (strict brand when a brand was explicitly requested)
        if brand:
            branded_local = await self._food_repo.find_best_match(
                name,
                brand=brand,
                person_id=person_id,
                preferred_state=preferred_state,
                strict_brand=True,
            )
            if branded_local is not None:
                return branded_local

            # Try Open Food Facts specifically for the requested brand before generic fallback
            if self._off is not None:
                off_hits = await self._off.search(
                    name, brand=brand, language=language, limit=3
                )
                norm_brand = normalize_text(brand)
                branded_hits = [
                    h
                    for h in off_hits
                    if h.brand and norm_brand in normalize_text(h.brand)
                ]
                chosen = branded_hits[0] if branded_hits else (off_hits[0] if off_hits else None)
                if chosen is not None:
                    await self._food_repo.upsert_global_food(chosen)
                    return await self._food_repo.get_effective_food(
                        chosen.food_id, person_id=person_id
                    )

        # Tier 1 & 2 (unbranded / general Vault match)
        local_match = await self._food_repo.find_best_match(
            name,
            brand=brand,
            person_id=person_id,
            preferred_state=preferred_state,
            strict_brand=False,
        )
        if local_match is not None:
            return local_match

        # Tier 3: Open Food Facts -> optional USDA
        for provider in (self._off, self._usda):
            if provider is None:
                continue
            hits = await provider.search(name, brand=brand, language=language, limit=3)
            if hits:
                chosen = hits[0]
                await self._food_repo.upsert_global_food(chosen)
                return await self._food_repo.get_effective_food(
                    chosen.food_id, person_id=person_id
                )

        return None

    @staticmethod
    def _food_to_effective_unpersisted(food: FoodItem) -> EffectiveFood:
        return EffectiveFood(
            food_id=food.food_id,
            name=food.name,
            normalized_name=food.normalized_name,
            aliases=food.aliases,
            brand=food.brand,
            barcode_ean=food.barcode_ean,
            category=food.category,
            preparation_state=food.preparation_state,
            macros_per_100g=food.macros_per_100g,
            label_standard=LabelStandard.EU,
            yield_factor=food.yield_factor,
            source=FoodSource.LLM_ESTIMATED,
            verified=False,
            atwater_warning=food.atwater_warning,
        )
