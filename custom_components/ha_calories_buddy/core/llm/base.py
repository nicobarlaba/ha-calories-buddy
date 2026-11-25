"""Abstract LLM client with structured JSON output enforcement and repair retries."""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import TypeVar

import aiohttp
from pydantic import BaseModel, ValidationError

from custom_components.ha_calories_buddy.core.logging import get_logger, log_event
from custom_components.ha_calories_buddy.core.models import LLMConfig

_LOGGER = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Base error for LLM client failures."""


class LLMConnectionError(LLMError):
    """Raised when the LLM endpoint is unreachable or returns a server/auth error."""


class LLMSchemaValidationError(LLMError):
    """Raised when the LLM fails to produce valid JSON matching the schema after retries."""


def strip_markdown_json_fences(raw: str) -> str:
    """Extract clean JSON text even if wrapped in markdown fences or prose."""
    text = raw.strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()

    if not text.startswith("{") and not text.startswith("["):
        start_obj = text.find("{")
        end_obj = text.rfind("}")
        if start_obj != -1 and end_obj > start_obj:
            text = text[start_obj : end_obj + 1]
    return text


class LLMClient(ABC):
    """Base async LLM client with deterministic schema validation & repair loop."""

    def __init__(self, config: LLMConfig, session: aiohttp.ClientSession) -> None:
        self._config = config
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=config.timeout_s)

    @property
    def config(self) -> LLMConfig:
        """Return active LLM configuration."""
        return self._config

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> T:
        """Request structured JSON output and validate against a Pydantic model.

        Automatically retries with exact validation error feedback if a local
        or cloud model emits malformed JSON.
        """
        current_user_prompt = user_prompt
        last_error: Exception | None = None

        for attempt in range(self._config.max_retries + 1):
            raw_text = await self._raw_structured_completion(
                system_prompt=system_prompt,
                user_prompt=current_user_prompt,
                response_model=response_model,
            )
            cleaned = strip_markdown_json_fences(raw_text)
            try:
                return response_model.model_validate_json(cleaned)
            except (ValidationError, ValueError) as exc:
                last_error = exc
                log_event(
                    _LOGGER,
                    logging.WARNING,
                    "llm_schema_validation_retry",
                    provider=self._config.provider,
                    attempt=attempt + 1,
                )
                current_user_prompt = (
                    f"{user_prompt}\n\n"
                    f"Your previous JSON output failed validation with error:\n{exc}\n"
                    "Return ONLY valid JSON strictly matching the required schema."
                )

        raise LLMSchemaValidationError(
            f"Model failed schema validation after {self._config.max_retries + 1} attempts: {last_error}"
        )

    @abstractmethod
    async def _raw_structured_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
    ) -> str:
        """Execute provider-specific structured completion returning raw JSON text."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Check whether the provider endpoint and credentials are reachable."""
