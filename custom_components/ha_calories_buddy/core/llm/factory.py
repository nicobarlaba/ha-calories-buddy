"""Factory for constructing unified LLM clients from Hub/Person configs."""

from __future__ import annotations

import aiohttp

from custom_components.ha_calories_buddy.core.llm.anthropic_client import AnthropicClient
from custom_components.ha_calories_buddy.core.llm.base import LLMClient
from custom_components.ha_calories_buddy.core.llm.gemini_client import GeminiClient
from custom_components.ha_calories_buddy.core.llm.ollama_client import OllamaClient
from custom_components.ha_calories_buddy.core.llm.openai_compat import OpenAICompatClient
from custom_components.ha_calories_buddy.core.models import LLMConfig


def create_llm_client(
    config: LLMConfig, session: aiohttp.ClientSession
) -> LLMClient | None:
    """Instantiate the matching LLMClient, or None if LLM is disabled."""
    provider = (config.provider or "none").strip().lower()
    if provider in ("", "none", "disabled"):
        return None
    if provider in ("openai", "openai_compatible", "lm_studio", "openrouter"):
        return OpenAICompatClient(config, session)
    if provider == "ollama":
        return OllamaClient(config, session)
    if provider == "anthropic":
        return AnthropicClient(config, session)
    if provider == "gemini":
        return GeminiClient(config, session)
    raise ValueError(f"Unsupported LLM provider: {provider}")
