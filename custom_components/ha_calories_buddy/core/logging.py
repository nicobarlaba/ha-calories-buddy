"""Low-cardinality structured logging helpers for ha-calories-buddy core."""

from __future__ import annotations

import logging
from typing import Any


def get_logger(name: str) -> logging.Logger:
    """Return a standard logger namespaced under ha_calories_buddy."""
    return logging.getLogger(name)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: Any,
) -> None:
    """Emit a low-cardinality structured log event using `extra` fields.

    Dynamic values must always be passed via keyword arguments (stored in `extra`)
    and never interpolated into the message string. Secrets such as `api_key`
    are stripped defensively if ever passed.
    """
    safe_fields = {
        k: v
        for k, v in fields.items()
        if "api_key" not in k.lower() and "secret" not in k.lower() and "token" not in k.lower()
    }
    logger.log(level, event, extra={"structured": safe_fields, **safe_fields})
