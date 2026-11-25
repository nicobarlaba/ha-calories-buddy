"""Runtime state containers for ha-calories-buddy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import aiohttp

from custom_components.ha_calories_buddy.const import LLM_PROVIDER_NONE
from custom_components.ha_calories_buddy.core.llm.base import LLMClient
from custom_components.ha_calories_buddy.core.llm.factory import create_llm_client
from custom_components.ha_calories_buddy.core.models import LLMConfig, PersonProfile
from custom_components.ha_calories_buddy.core.nutrition.providers.open_food_facts import (
    OpenFoodFactsProvider,
)
from custom_components.ha_calories_buddy.core.nutrition.providers.usda import USDAProvider
from custom_components.ha_calories_buddy.core.nutrition.resolver import FoodResolver
from custom_components.ha_calories_buddy.core.planner.meal_parser import (
    MealLoggingService,
)
from custom_components.ha_calories_buddy.core.storage.database import Database
from custom_components.ha_calories_buddy.core.storage.food_repo import FoodRepository
from custom_components.ha_calories_buddy.core.storage.meal_repo import MealRepository
from custom_components.ha_calories_buddy.core.storage.profile_repo import ProfileRepository

if TYPE_CHECKING:
    from custom_components.ha_calories_buddy.coordinator import (
        PersonNutritionCoordinator,
    )


@dataclass
class HubRuntimeData:
    """Global household hub runtime configuration."""

    entry_id: str
    llm_config: LLMConfig
    language: str = "en"
    usda_api_key: str | None = None
    global_baseline_notes: str = ""


@dataclass
class PersonRuntimeData:
    """Per-person runtime container linking ConfigEntry, Profile, and Coordinator."""

    entry_id: str
    person_id: str
    profile: PersonProfile
    llm_config: LLMConfig | None
    coordinator: PersonNutritionCoordinator


@dataclass
class DomainRuntime:
    """Shared integration runtime state stored in hass.data[DOMAIN]."""

    db: Database
    food_repo: FoodRepository
    meal_repo: MealRepository
    profile_repo: ProfileRepository
    session: aiohttp.ClientSession
    off_provider: OpenFoodFactsProvider
    usda_provider: USDAProvider
    resolver: FoodResolver
    logging_service: MealLoggingService
    hub: HubRuntimeData | None = None
    persons: dict[str, PersonRuntimeData] = field(default_factory=dict)
    frontend_registered: bool = False
    services_registered: bool = False
    ws_registered: bool = False
    llm_api_unsub: Callable[[], None] | None = None
    stop_unsub: Callable[[], None] | None = None

    def get_effective_llm_config(self, profile: PersonProfile) -> LLMConfig:
        """Resolve per-person LLM override or fall back to Hub default."""
        person_rt = self.persons.get(profile.person_id)
        if (
            profile.llm_override_enabled
            and person_rt is not None
            and person_rt.llm_config is not None
        ):
            return person_rt.llm_config
        if self.hub is not None:
            return self.hub.llm_config
        return LLMConfig(provider=LLM_PROVIDER_NONE)

    def get_effective_llm_client(self, profile: PersonProfile) -> LLMClient | None:
        """Instantiate an async LLMClient for the given person, or None if disabled."""
        cfg = self.get_effective_llm_config(profile)
        return create_llm_client(cfg, self.session)

    def is_llm_enabled(self, profile: PersonProfile) -> bool:
        """Return True when an active LLM provider is configured for this person."""
        cfg = self.get_effective_llm_config(profile)
        return cfg.provider not in ("", LLM_PROVIDER_NONE, "disabled")

    def update_usda_api_key(self, api_key: str | None) -> None:
        """Refresh the USDA provider when Hub options change."""
        self.usda_provider = USDAProvider(self.session, api_key=api_key)
        self.resolver = FoodResolver(
            food_repo=self.food_repo,
            off_provider=self.off_provider,
            usda_provider=self.usda_provider,
        )
        self.logging_service = MealLoggingService(
            meal_repo=self.meal_repo,
            food_repo=self.food_repo,
            resolver=self.resolver,
            llm_client_getter=self.get_effective_llm_client,
        )
