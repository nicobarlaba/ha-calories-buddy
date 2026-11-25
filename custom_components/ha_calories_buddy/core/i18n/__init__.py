"""Core localization helpers for user-facing summaries and warnings."""

from __future__ import annotations

from custom_components.ha_calories_buddy.core.models import WarningCode

_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "meal_confirmed_summary": "{slot} logged: {kcal} kcal (P {p}g / C {c}g / F {f}g)",
        "meal_pending_summary": "{slot} saved as pending confirmation: {kcal} kcal (P {p}g / C {c}g / F {f}g)",
        "warn_ambiguous_state": "Please confirm raw vs. cooked weight for: {items}",
        "warn_llm_estimated": "Estimated by AI (not verified in DB): {items}",
        "warn_atwater": "Product label calories look inconsistent for: {items}",
        "warn_unknown_unit": "Unrecognized portion unit for: {items}",
    },
    "it": {
        "meal_confirmed_summary": "{slot} registrato: {kcal} kcal (P {p}g / C {c}g / G {f}g)",
        "meal_pending_summary": "{slot} salvato da confermare: {kcal} kcal (P {p}g / C {c}g / G {f}g)",
        "warn_ambiguous_state": "Conferma se il peso è crudo o cotto per: {items}",
        "warn_llm_estimated": "Valori stimati dall'AI (non presenti nel DB): {items}",
        "warn_atwater": "Calorie in etichetta potenzialmente incoerenti per: {items}",
        "warn_unknown_unit": "Unità di misura non riconosciuta per: {items}",
    },
}

WARNING_TO_KEY: dict[WarningCode, str] = {
    WarningCode.AMBIGUOUS_STATE: "warn_ambiguous_state",
    WarningCode.LLM_ESTIMATED: "warn_llm_estimated",
    WarningCode.ATWATER_WARNING: "warn_atwater",
    WarningCode.UNKNOWN_UNIT: "warn_unknown_unit",
}


def format_message(language: str, key: str, **kwargs: object) -> str:
    """Format a localized core message safely falling back to English."""
    lang = language if language in _MESSAGES else "en"
    template = _MESSAGES[lang].get(key) or _MESSAGES["en"].get(key, key)
    return template.format(**kwargs)
