"""Natural-language and manual meal logging service with hybrid confirmation."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, time

from pydantic import BaseModel, Field

from custom_components.ha_calories_buddy.core.i18n import (
    WARNING_TO_KEY,
    format_message,
)
from custom_components.ha_calories_buddy.core.llm.base import LLMClient
from custom_components.ha_calories_buddy.core.llm.prompts.food_estimate import (
    EstimatedFoodSchema,
    build_food_estimate_prompts,
    schema_to_estimated_food,
)
from custom_components.ha_calories_buddy.core.llm.prompts.meal_parse import (
    ParsedMealResponseSchema,
    build_meal_parse_prompts,
)
from custom_components.ha_calories_buddy.core.logging import get_logger, log_event
from custom_components.ha_calories_buddy.core.models import (
    ConfidenceLevel,
    FoodItem,
    FoodSource,
    LabelStandard,
    Macros,
    MealLog,
    MealLogItem,
    MealStatus,
    PersonProfile,
    PreparationState,
    WarningCode,
)
from custom_components.ha_calories_buddy.core.nutrition.atwater import check_atwater
from custom_components.ha_calories_buddy.core.nutrition.calculator import (
    compute_macros_for_grams,
    sum_macros,
)
from custom_components.ha_calories_buddy.core.nutrition.resolver import (
    FoodResolver,
    ResolvedMealItem,
)
from custom_components.ha_calories_buddy.core.nutrition.units import convert_to_grams
from custom_components.ha_calories_buddy.core.nutrition.yield_factors import (
    convert_to_reference_grams,
    default_yield_for_category,
)
from custom_components.ha_calories_buddy.core.planner.slot_resolver import (
    resolve_meal_slot,
)
from custom_components.ha_calories_buddy.core.storage.food_repo import FoodRepository
from custom_components.ha_calories_buddy.core.storage.meal_repo import MealRepository
from custom_components.ha_calories_buddy.core.storage.seeder import normalize_text

_LOGGER = get_logger(__name__)


class LLMFoodEstimatorAdapter:
    """Wraps an LLMClient to satisfy FoodResolver's LLMEstimatorProtocol."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._client = llm_client

    async def estimate_food(
        self,
        name: str,
        brand: str | None,
        preparation_state: PreparationState,
        language: str,
    ) -> FoodItem | None:
        """Ask the LLM to estimate macros per 100g as a last resort."""
        sys_prompt, user_prompt = build_food_estimate_prompts(
            name=name,
            brand=brand,
            preparation_state=preparation_state,
            language=language,
        )
        try:
            estimated = await self._client.generate_structured(
                system_prompt=sys_prompt,
                user_prompt=user_prompt,
                response_model=EstimatedFoodSchema,
            )
            return schema_to_estimated_food(estimated, brand=brand)
        except Exception as exc:  # noqa: BLE001
            log_event(
                _LOGGER,
                logging.WARNING,
                "llm_food_estimation_failed",
                error_type=type(exc).__name__,
            )
            return None


class LogMealResult(BaseModel):
    """Outcome of logging a meal via voice, text, or manual action."""

    meal_log: MealLog
    needs_confirmation: bool
    summary_message: str
    warnings: list[str] = Field(default_factory=list)


class MealLoggingService:
    """Orchestrates NLP parsing, deterministic resolution, and hybrid persistence."""

    def __init__(
        self,
        meal_repo: MealRepository,
        food_repo: FoodRepository,
        resolver: FoodResolver,
        llm_client_getter: Callable[[PersonProfile], LLMClient | None],
    ) -> None:
        self._meal_repo = meal_repo
        self._food_repo = food_repo
        self._resolver = resolver
        self._get_llm = llm_client_getter

    async def log_from_text(
        self,
        profile: PersonProfile,
        text: str,
        *,
        log_date: date | None = None,
        meal_slot: str | None = None,
        source_channel: str = "voice",
        current_local_time: str | None = None,
    ) -> LogMealResult:
        """Parse natural language text via LLM and persist deterministically."""
        llm_client = self._get_llm(profile)
        if llm_client is None:
            raise RuntimeError("No LLM provider configured for natural-language meal parsing")

        frequent_foods = await self._meal_repo.get_top_frequent_foods(
            profile.person_id, limit=25
        )
        now_hhmm = current_local_time or datetime.now().strftime("%H:%M")
        sys_prompt, user_prompt = build_meal_parse_prompts(
            profile=profile,
            utterance=text,
            frequent_foods=frequent_foods,
            current_local_time=now_hhmm,
        )
        parsed = await llm_client.generate_structured(
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            response_model=ParsedMealResponseSchema,
        )

        estimator = LLMFoodEstimatorAdapter(llm_client)
        resolved_items: list[ResolvedMealItem] = []
        for parsed_item in parsed.items:
            resolved = await self._resolver.resolve_item(
                person_id=profile.person_id,
                name=parsed_item.name,
                brand=parsed_item.brand,
                quantity=parsed_item.quantity,
                unit=parsed_item.unit,
                state=parsed_item.preparation_state,
                language=profile.language,
                llm_estimator=estimator,
            )
            resolved_items.append(resolved)

        parsed_now_time: time | None = None
        if current_local_time:
            try:
                parts = current_local_time.strip().split(":")
                parsed_now_time = time(int(parts[0]), int(parts[1]))
            except (ValueError, IndexError):
                parsed_now_time = None

        chosen_slot = resolve_meal_slot(
            meal_slot or parsed.meal_slot,
            profile.meal_slots,
            language=profile.language,
            now_time=parsed_now_time,
        )
        return await self._persist_resolved_meal(
            profile=profile,
            resolved_items=resolved_items,
            meal_slot=chosen_slot,
            log_date=log_date or date.today(),
            source_channel=source_channel,
            raw_input_text=text,
        )

    async def log_manual_item(
        self,
        profile: PersonProfile,
        *,
        food_id: str | None = None,
        barcode: str | None = None,
        name: str = "",
        quantity: float = 100.0,
        unit: str = "g",
        state: PreparationState = PreparationState.UNSPECIFIED,
        meal_slot: str | None = None,
        log_date: date | None = None,
        source_channel: str = "manual",
    ) -> LogMealResult:
        """Log a single food deterministically without requiring any LLM."""
        resolved = await self._resolver.resolve_item(
            person_id=profile.person_id,
            food_id=food_id,
            barcode=barcode,
            name=name or "Custom Food",
            quantity=quantity,
            unit=unit,
            state=state,
            language=profile.language,
            llm_estimator=None,
        )
        chosen_slot = resolve_meal_slot(
            meal_slot,
            profile.meal_slots,
            language=profile.language,
        )
        return await self._persist_resolved_meal(
            profile=profile,
            resolved_items=[resolved],
            meal_slot=chosen_slot,
            log_date=log_date or date.today(),
            source_channel=source_channel,
            raw_input_text=None,
        )

    async def change_meal_slot(
        self,
        profile: PersonProfile,
        log_id: str,
        new_slot: str,
    ) -> MealLog | None:
        """Normalize and persist a new meal_slot on an existing logged meal."""
        normalized_slot = resolve_meal_slot(
            new_slot,
            profile.meal_slots,
            language=profile.language,
        )
        return await self._meal_repo.update_meal_slot(
            profile.person_id, log_id, normalized_slot
        )

    async def confirm_pending_meal(
        self,
        profile: PersonProfile,
        log_id: str,
        *,
        promote_to_vault: bool = False,
    ) -> MealLog | None:
        """Confirm a pending meal and optionally promote verified items to Vault."""
        existing = await self._meal_repo.get_meal_log(log_id)
        if existing is None or existing.person_id != profile.person_id:
            return None

        if promote_to_vault:
            for item in existing.items:
                if item.food_id is None and item.resolved_grams_ref > 0:
                    factor = 100.0 / item.resolved_grams_ref
                    macros_100g = Macros(
                        kcal=round(item.macros.kcal * factor, 1),
                        protein_g=round(item.macros.protein_g * factor, 1),
                        carbs_g=round(item.macros.carbs_g * factor, 1),
                        fat_g=round(item.macros.fat_g * factor, 1),
                        fiber_g=(
                            round(item.macros.fiber_g * factor, 1)
                            if item.macros.fiber_g is not None
                            else None
                        ),
                    )
                    atwater = check_atwater(macros_100g, LabelStandard.EU)
                    new_food = FoodItem(
                        food_id=f"user_{uuid.uuid4().hex[:12]}",
                        name=item.display_name,
                        normalized_name=normalize_text(item.display_name),
                        brand=item.brand,
                        category="other",
                        preparation_state=(
                            PreparationState.READY_TO_EAT
                            if item.input_state == PreparationState.UNSPECIFIED
                            else item.input_state
                        ),
                        macros_per_100g=macros_100g,
                        label_standard=LabelStandard.EU,
                        yield_factor=default_yield_for_category("other"),
                        source=FoodSource.MANUAL,
                        verified=True,
                        atwater_warning=atwater.warning,
                    )
                    saved_food = await self._food_repo.upsert_global_food(new_food)
                    await self._meal_repo.update_meal_item(
                        profile.person_id,
                        log_id,
                        item.item_id,
                        food_id=saved_food.food_id,
                        input_quantity=item.input_quantity,
                        input_unit=item.input_unit,
                        input_state=item.input_state,
                        resolved_grams_ref=item.resolved_grams_ref,
                        macros=item.macros,
                    )

        return await self._meal_repo.confirm_meal(profile.person_id, log_id)

    async def update_item_measurement(
        self,
        profile: PersonProfile,
        log_id: str,
        item_id: str,
        *,
        quantity: float,
        unit: str | None = None,
        state: PreparationState | None = None,
    ) -> MealLog | None:
        """Update quantity/unit/state of an existing item and recompute macros."""
        meal = await self._meal_repo.get_meal_log(log_id)
        if meal is None or meal.person_id != profile.person_id:
            return None

        target_item = next((i for i in meal.items if i.item_id == item_id), None)
        if target_item is None:
            return None

        new_unit = unit or target_item.input_unit
        new_state = state or target_item.input_state

        if target_item.food_id:
            food = await self._food_repo.get_effective_food(
                target_item.food_id, person_id=profile.person_id
            )
            if food is not None:
                grams, unknown_unit = convert_to_grams(quantity, new_unit, food)
                eff_state = (
                    food.preparation_state
                    if new_state == PreparationState.UNSPECIFIED
                    else new_state
                )
                ref_grams = convert_to_reference_grams(
                    grams, eff_state, food.preparation_state, food.yield_factor
                )
                new_macros = compute_macros_for_grams(food.macros_per_100g, ref_grams)
                return await self._meal_repo.update_meal_item(
                    profile.person_id,
                    log_id,
                    item_id,
                    input_quantity=quantity,
                    input_unit=new_unit,
                    input_state=eff_state,
                    resolved_grams_ref=round(ref_grams, 2),
                    macros=new_macros,
                    confidence=(
                        ConfidenceLevel.MEDIUM if unknown_unit else ConfidenceLevel.HIGH
                    ),
                    warning_code=(
                        WarningCode.UNKNOWN_UNIT if unknown_unit else None
                    ),
                )

        # Unlinked item (e.g. LLM-estimated): scale proportionally from previous quantity
        ratio = quantity / target_item.input_quantity if target_item.input_quantity > 0 else 1.0
        scaled_macros = Macros(
            kcal=round(target_item.macros.kcal * ratio, 1),
            protein_g=round(target_item.macros.protein_g * ratio, 1),
            carbs_g=round(target_item.macros.carbs_g * ratio, 1),
            fat_g=round(target_item.macros.fat_g * ratio, 1),
            fiber_g=(
                round(target_item.macros.fiber_g * ratio, 1)
                if target_item.macros.fiber_g is not None
                else None
            ),
        )
        return await self._meal_repo.update_meal_item(
            profile.person_id,
            log_id,
            item_id,
            input_quantity=quantity,
            input_unit=new_unit,
            input_state=new_state,
            resolved_grams_ref=round(target_item.resolved_grams_ref * ratio, 2),
            macros=scaled_macros,
            confidence=target_item.confidence,
            warning_code=target_item.warning_code,
            warning_detail=target_item.warning_detail,
        )

    async def _persist_resolved_meal(
        self,
        *,
        profile: PersonProfile,
        resolved_items: list[ResolvedMealItem],
        meal_slot: str,
        log_date: date,
        source_channel: str,
        raw_input_text: str | None,
    ) -> LogMealResult:
        log_id = f"log_{uuid.uuid4().hex[:12]}"
        needs_confirmation = any(
            item.confidence != ConfidenceLevel.HIGH or item.warning_code is not None
            for item in resolved_items
        )
        status = (
            MealStatus.PENDING_CONFIRMATION
            if needs_confirmation
            else MealStatus.CONFIRMED
        )
        db_items = [
            MealLogItem(
                item_id=f"item_{uuid.uuid4().hex[:12]}",
                log_id=log_id,
                food_id=item.food_id,
                display_name=item.display_name,
                brand=item.brand,
                input_quantity=item.input_quantity,
                input_unit=item.input_unit,
                input_state=item.effective_state,
                resolved_grams_ref=item.resolved_grams_ref,
                macros=item.macros,
                confidence=item.confidence,
                warning_code=item.warning_code,
                warning_detail=item.warning_detail,
            )
            for item in resolved_items
        ]
        total_macros = sum_macros([i.macros for i in db_items])
        meal_log = MealLog(
            log_id=log_id,
            person_id=profile.person_id,
            log_date=log_date,
            meal_slot=meal_slot,
            logged_at=datetime.now(UTC),
            status=status,
            source_channel=source_channel,
            raw_input_text=raw_input_text,
            total_macros=total_macros,
            items=db_items,
        )
        await self._meal_repo.create_meal_log(meal_log)
        log_event(
            _LOGGER,
            logging.INFO,
            "meal_logged",
            person_id=profile.person_id,
            status=status.value,
            item_count=len(db_items),
        )

        summary_key = (
            "meal_pending_summary" if needs_confirmation else "meal_confirmed_summary"
        )
        summary = format_message(
            profile.language,
            summary_key,
            slot=meal_slot,
            kcal=round(total_macros.kcal),
            p=round(total_macros.protein_g, 1),
            c=round(total_macros.carbs_g, 1),
            f=round(total_macros.fat_g, 1),
            fib=round(total_macros.fiber_g or 0.0, 1),
        )
        warnings = self._build_warning_messages(profile.language, resolved_items)
        return LogMealResult(
            meal_log=meal_log,
            needs_confirmation=needs_confirmation,
            summary_message=summary,
            warnings=warnings,
        )

    @staticmethod
    def _build_warning_messages(
        language: str, resolved_items: list[ResolvedMealItem]
    ) -> list[str]:
        by_code: dict[WarningCode, list[str]] = {}
        for item in resolved_items:
            if item.warning_code is not None:
                by_code.setdefault(item.warning_code, []).append(item.display_name)
        messages: list[str] = []
        for code, names in by_code.items():
            key = WARNING_TO_KEY.get(code)
            if key:
                messages.append(format_message(language, key, items=", ".join(names)))
        return messages
