"""Native Ollama async client (/api/chat with JSON schema format)."""

from __future__ import annotations

from typing import Any

import aiohttp
from pydantic import BaseModel

from custom_components.ha_calories_buddy.core.llm.base import (
    LLMClient,
    LLMConnectionError,
)
from custom_components.ha_calories_buddy.core.models import LLMConfig

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"


class OllamaClient(LLMClient):
    """Async client for local/LAN Ollama instances."""

    def __init__(self, config: LLMConfig, session: aiohttp.ClientSession) -> None:
        super().__init__(config, session)
        self._base_url = (config.base_url or DEFAULT_OLLAMA_BASE_URL).rstrip("/")

    async def _raw_structured_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
    ) -> str:
        url = f"{self._base_url}/api/chat"
        payload: dict[str, Any] = {
            "model": self._config.model,
            "stream": False,
            "format": response_model.model_json_schema(),
            "options": {"temperature": self._config.temperature},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        try:
            async with self._session.post(
                url,
                json=payload,
                timeout=self._timeout,
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise LLMConnectionError(
                        f"Ollama endpoint returned HTTP {resp.status}: {body[:200]}"
                    )
                data = await resp.json()
                message = data.get("message") or {}
                return str(message.get("content", ""))
        except TimeoutError as exc:
            raise LLMConnectionError(
                f"Ollama request timed out after {int(self._config.timeout_s)}s; "
                "increase LLM timeout or use a faster model"
            ) from exc
        except aiohttp.ClientError as exc:
            raise LLMConnectionError(
                f"Connection failed to Ollama endpoint: {type(exc).__name__}"
            ) from exc

    async def health_check(self) -> bool:
        """Verify Ollama server availability via GET /api/tags."""
        url = f"{self._base_url}/api/tags"
        probe_timeout = aiohttp.ClientTimeout(total=min(10.0, self._config.timeout_s))
        try:
            async with self._session.get(url, timeout=probe_timeout) as resp:
                if resp.status != 200:
                    return False
                if not self._config.model:
                    return True
                data = await resp.json()
                models = [str(m.get("name", "")) for m in data.get("models", [])]
                if not models:
                    return True
                target = self._config.model
                return any(m == target or m.startswith(f"{target}:") for m in models)
        except (TimeoutError, aiohttp.ClientError, ValueError):
            return False
