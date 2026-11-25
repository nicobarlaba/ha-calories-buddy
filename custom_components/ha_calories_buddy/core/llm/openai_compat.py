"""OpenAI and OpenAI-compatible (LM Studio, OpenRouter, vLLM, Groq) client."""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp
from pydantic import BaseModel

from custom_components.ha_calories_buddy.core.llm.base import (
    LLMClient,
    LLMConnectionError,
)
from custom_components.ha_calories_buddy.core.logging import get_logger, log_event
from custom_components.ha_calories_buddy.core.models import LLMConfig

_LOGGER = get_logger(__name__)
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


class OpenAICompatClient(LLMClient):
    """Async client for OpenAI and OpenAI-compatible HTTP endpoints."""

    def __init__(self, config: LLMConfig, session: aiohttp.ClientSession) -> None:
        super().__init__(config, session)
        base = (config.base_url or DEFAULT_OPENAI_BASE_URL).rstrip("/")
        if not base.endswith("/v1") and "openai.com" in base:
            base = f"{base}/v1"
        self._base_url = base

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        return headers

    async def _raw_structured_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
    ) -> str:
        schema = response_model.model_json_schema()
        schema_prompt = (
            f"{system_prompt}\n\n"
            f"Respond ONLY with valid JSON conforming to this JSON Schema:\n"
            f"{json.dumps(schema)}"
        )

        # Try json_schema first, then fallback to json_object, then plain text if local endpoint rejects both
        response_formats: list[dict[str, Any] | None] = [
            {
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__,
                    "strict": False,
                    "schema": schema,
                },
            },
            {"type": "json_object"},
            None,
        ]

        url = f"{self._base_url}/chat/completions"
        last_status = 0
        for fmt in response_formats:
            payload: dict[str, Any] = {
                "model": self._config.model,
                "temperature": self._config.temperature,
                "messages": [
                    {"role": "system", "content": schema_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            if fmt is not None:
                payload["response_format"] = fmt

            try:
                async with self._session.post(
                    url,
                    json=payload,
                    headers=self._headers(),
                    timeout=self._timeout,
                ) as resp:
                    last_status = resp.status
                    if resp.status in (400, 422) and fmt is not None:
                        log_event(
                            _LOGGER,
                            logging.DEBUG,
                            "openai_compat_fallback_response_format",
                            status=resp.status,
                        )
                        continue
                    if resp.status >= 400:
                        body = await resp.text()
                        raise LLMConnectionError(
                            f"OpenAI-compatible endpoint returned HTTP {resp.status}: {body[:200]}"
                        )
                    data = await resp.json()
                    choices = data.get("choices") or []
                    if not choices:
                        raise LLMConnectionError("Empty choices returned by LLM endpoint")
                    content = choices[0].get("message", {}).get("content", "")
                    return str(content)
            except (TimeoutError, aiohttp.ClientError) as exc:
                raise LLMConnectionError(
                    f"Connection failed to OpenAI-compatible endpoint: {type(exc).__name__}"
                ) from exc

        raise LLMConnectionError(
            f"OpenAI-compatible endpoint rejected all response formats (HTTP {last_status})"
        )

    async def health_check(self) -> bool:
        """Verify reachability via GET /models."""
        url = f"{self._base_url}/models"
        try:
            async with self._session.get(
                url,
                headers=self._headers(),
                timeout=self._timeout,
            ) as resp:
                return bool(resp.status == 200)
        except (TimeoutError, aiohttp.ClientError):
            return False
