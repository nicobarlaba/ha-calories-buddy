"""LLMClient adapter backed by Home Assistant's native ai_task.generate_data service."""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pydantic import BaseModel

from custom_components.ha_calories_buddy.core.llm.base import (
    LLMClient,
    LLMConnectionError,
)
from custom_components.ha_calories_buddy.core.logging import get_logger, log_event
from custom_components.ha_calories_buddy.core.models import LLMConfig

_LOGGER = get_logger(__name__)
INVALID_ENTITY_STATES = frozenset({"unavailable", "unknown"})


class HAAITaskLLMClient(LLMClient):
    """Execute structured JSON completions via HA's configured ai_task.* entity."""

    def __init__(
        self,
        hass: HomeAssistant,
        config: LLMConfig,
        session: aiohttp.ClientSession,
    ) -> None:
        super().__init__(config, session)
        self._hass = hass
        self._entity_id = (config.ai_task_entity or "").strip() or None

    async def _raw_structured_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
    ) -> str:
        if not self._hass.services.has_service("ai_task", "generate_data"):
            raise LLMConnectionError(
                "Home Assistant ai_task.generate_data service is not available"
            )

        schema_json = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
        instructions = (
            f"{system_prompt}\n\n"
            "Return ONLY a single raw JSON object (no markdown fences, no prose) "
            f"strictly matching this JSON Schema:\n{schema_json}\n\n"
            f"Input:\n{user_prompt}"
        )
        service_data: dict[str, Any] = {
            "task_name": f"ha_calories_buddy_{response_model.__name__}",
            "instructions": instructions,
        }
        if self._entity_id:
            service_data["entity_id"] = self._entity_id

        try:
            response = await self._hass.services.async_call(
                "ai_task",
                "generate_data",
                service_data,
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError as exc:
            log_event(
                _LOGGER,
                logging.WARNING,
                "ha_ai_task_call_failed",
                error_type=type(exc).__name__,
            )
            raise LLMConnectionError(f"HA AI Task failed: {exc}") from exc

        if not isinstance(response, dict) or "data" not in response:
            raise LLMConnectionError("HA AI Task returned empty response")

        data_val = response["data"]
        if isinstance(data_val, (dict, list)):
            return json.dumps(data_val, ensure_ascii=False)
        return str(data_val)

    async def health_check(self) -> bool:
        """Verify ai_task.generate_data exists and optional entity_id is healthy."""
        if not self._hass.services.has_service("ai_task", "generate_data"):
            return False
        if self._entity_id:
            state = self._hass.states.get(self._entity_id)
            if state is None:
                return False
            if str(state.state).strip().lower() in INVALID_ENTITY_STATES:
                return False
        return True
