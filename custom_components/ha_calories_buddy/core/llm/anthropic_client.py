"""Anthropic Messages API async client using forced tool JSON output."""

from __future__ import annotations

import json
from typing import Any

import aiohttp
from pydantic import BaseModel

from custom_components.ha_calories_buddy.core.llm.base import (
    LLMClient,
    LLMConnectionError,
)
from custom_components.ha_calories_buddy.core.models import LLMConfig

DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicClient(LLMClient):
    """Async client for Anthropic Claude Messages API."""

    def __init__(self, config: LLMConfig, session: aiohttp.ClientSession) -> None:
        super().__init__(config, session)
        self._base_url = (config.base_url or DEFAULT_ANTHROPIC_BASE_URL).rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self._config.api_key or "",
            "anthropic-version": ANTHROPIC_VERSION,
        }

    async def _raw_structured_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
    ) -> str:
        url = f"{self._base_url}/v1/messages"
        schema = response_model.model_json_schema()
        payload: dict[str, Any] = {
            "model": self._config.model or "claude-sonnet-4-5",
            "max_tokens": 2048,
            "temperature": self._config.temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "tools": [
                {
                    "name": "structured_output",
                    "description": "Emit structured JSON matching the required schema.",
                    "input_schema": schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": "structured_output"},
        }
        try:
            async with self._session.post(
                url,
                json=payload,
                headers=self._headers(),
                timeout=self._timeout,
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise LLMConnectionError(
                        f"Anthropic endpoint returned HTTP {resp.status}: {body[:200]}"
                    )
                data = await resp.json()
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise LLMConnectionError(
                f"Connection failed to Anthropic endpoint: {type(exc).__name__}"
            ) from exc

        for block in data.get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                return json.dumps(block.get("input", {}))
            if isinstance(block, dict) and block.get("type") == "text":
                return str(block.get("text", ""))
        raise LLMConnectionError("Anthropic response contained no structured content")

    async def health_check(self) -> bool:
        """Verify Anthropic API key via GET /v1/models."""
        url = f"{self._base_url}/v1/models"
        try:
            async with self._session.get(
                url,
                headers=self._headers(),
                timeout=self._timeout,
            ) as resp:
                return bool(resp.status == 200)
        except (TimeoutError, aiohttp.ClientError):
            return False
