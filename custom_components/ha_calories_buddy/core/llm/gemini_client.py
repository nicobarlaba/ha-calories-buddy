"""Google Gemini REST API async client with responseSchema sanitization."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import aiohttp
from pydantic import BaseModel

from custom_components.ha_calories_buddy.core.llm.base import (
    LLMClient,
    LLMConnectionError,
)
from custom_components.ha_calories_buddy.core.models import LLMConfig

DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"

_UNSUPPORTED_GEMINI_KEYS = frozenset(
    {
        "$defs",
        "$ref",
        "$schema",
        "additionalProperties",
        "title",
        "default",
        "examples",
        "exclusiveMinimum",
        "exclusiveMaximum",
    }
)

_TYPE_MAP = {
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}


def sanitize_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert Pydantic JSON Schema into Gemini-compatible OpenAPI schema subset.

    Resolves `$defs`/`$ref`, converts `anyOf` nullables into `nullable: true`,
    maps JSON Schema types to Gemini uppercase enum strings, and removes
    unsupported keys such as `additionalProperties` and `title`.
    """
    defs: dict[str, Any] = deepcopy(schema.get("$defs", {}))

    def _resolve(node: Any) -> Any:
        if not isinstance(node, dict):
            return node

        if "$ref" in node:
            ref = str(node["$ref"])
            ref_name = ref.rsplit("/", maxsplit=1)[-1]
            if ref_name in defs:
                merged = deepcopy(defs[ref_name])
                for k, v in node.items():
                    if k != "$ref":
                        merged[k] = v
                return _resolve(merged)

        if "allOf" in node and isinstance(node["allOf"], list) and node["allOf"]:
            merged_all: dict[str, Any] = {}
            for sub in node["allOf"]:
                resolved_sub = _resolve(deepcopy(sub))
                if isinstance(resolved_sub, dict):
                    merged_all.update(resolved_sub)
            for k, v in node.items():
                if k not in ("allOf", *_UNSUPPORTED_GEMINI_KEYS):
                    merged_all[k] = v
            return merged_all

        if "anyOf" in node and isinstance(node["anyOf"], list):
            non_null = [
                opt
                for opt in node["anyOf"]
                if not (isinstance(opt, dict) and opt.get("type") == "null")
            ]
            has_null = len(non_null) < len(node["anyOf"])
            if non_null:
                resolved = _resolve(deepcopy(non_null[0]))
                if isinstance(resolved, dict) and has_null:
                    resolved["nullable"] = True
                if "description" in node and isinstance(resolved, dict):
                    resolved.setdefault("description", node["description"])
                return resolved

        cleaned: dict[str, Any] = {}
        for key, value in node.items():
            if key in _UNSUPPORTED_GEMINI_KEYS:
                continue
            if key == "type" and isinstance(value, str):
                cleaned["type"] = _TYPE_MAP.get(value.lower(), value.upper())
            elif key == "properties" and isinstance(value, dict):
                cleaned["properties"] = {
                    prop_k: _resolve(prop_v) for prop_k, prop_v in value.items()
                }
            elif key == "items":
                cleaned["items"] = _resolve(value)
            else:
                cleaned[key] = _resolve(value) if isinstance(value, dict) else value
        return cleaned

    resolved_root = _resolve(deepcopy(schema))
    return resolved_root if isinstance(resolved_root, dict) else {}


class GeminiClient(LLMClient):
    """Async client for Google Gemini generateContent API."""

    def __init__(self, config: LLMConfig, session: aiohttp.ClientSession) -> None:
        super().__init__(config, session)
        self._base_url = (config.base_url or DEFAULT_GEMINI_BASE_URL).rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-goog-api-key": self._config.api_key or "",
        }

    async def _raw_structured_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
    ) -> str:
        model = self._config.model or "gemini-2.5-flash"
        url = f"{self._base_url}/v1beta/models/{model}:generateContent"
        response_schema = sanitize_gemini_schema(response_model.model_json_schema())

        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": self._config.temperature,
                "responseMimeType": "application/json",
                "responseSchema": response_schema,
            },
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
                        f"Gemini endpoint returned HTTP {resp.status}: {body[:200]}"
                    )
                data = await resp.json()
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise LLMConnectionError(
                f"Connection failed to Gemini endpoint: {type(exc).__name__}"
            ) from exc

        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMConnectionError("Gemini returned empty candidates")
        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            raise LLMConnectionError("Gemini returned empty content parts")
        return str(parts[0].get("text", ""))

    async def health_check(self) -> bool:
        """Verify Gemini API key via GET /v1beta/models."""
        url = f"{self._base_url}/v1beta/models"
        try:
            async with self._session.get(
                url,
                headers=self._headers(),
                timeout=self._timeout,
            ) as resp:
                return bool(resp.status == 200)
        except (TimeoutError, aiohttp.ClientError):
            return False
