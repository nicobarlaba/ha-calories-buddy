"""Static asset serving and automatic Lovelace resource registration."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.http.server import StaticPathConfig
from homeassistant.components.lovelace import LovelaceData
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.ha_calories_buddy.const import (
    FRONTEND_CARD_FILENAME,
    FRONTEND_CARD_URL,
    FRONTEND_URL_BASE,
)
from custom_components.ha_calories_buddy.core.logging import get_logger, log_event

_LOGGER = get_logger(__name__)
FRONTEND_DIR = Path(__file__).resolve().parent / "frontend_dist"


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the Custom Card ES bundle and auto-register Lovelace storage resource."""
    if getattr(hass, "http", None) is not None:
        try:
            await hass.http.async_register_static_paths(
                [
                    StaticPathConfig(
                        url_path=FRONTEND_URL_BASE,
                        path=str(FRONTEND_DIR),
                        cache_headers=False,
                    )
                ]
            )
            log_event(_LOGGER, logging.INFO, "frontend_static_path_registered")
        except RuntimeError:
            # Already registered during reload
            pass

    lovelace_data: LovelaceData | None = hass.data.get(LOVELACE_DATA)
    if lovelace_data is None:
        return

    resources = getattr(lovelace_data, "resources", None)
    if resources is None:
        return

    if not (
        hasattr(resources, "async_get_info")
        and hasattr(resources, "async_items")
        and hasattr(resources, "async_create_item")
    ):
        log_event(_LOGGER, logging.DEBUG, "frontend_lovelace_yaml_mode_skipped")
        return

    try:
        await resources.async_get_info()
        prefix = f"{FRONTEND_URL_BASE}/{FRONTEND_CARD_FILENAME}"
        for item in resources.async_items():
            url = str(item.get("url", ""))
            if url == FRONTEND_CARD_URL:
                return
            if url.startswith(prefix) and "id" in item and hasattr(resources, "async_update_item"):
                await resources.async_update_item(
                    item["id"],
                    {"res_type": "module", "url": FRONTEND_CARD_URL},
                )
                log_event(_LOGGER, logging.INFO, "frontend_lovelace_resource_updated")
                return

        await resources.async_create_item(
            {"res_type": "module", "url": FRONTEND_CARD_URL}
        )
        log_event(_LOGGER, logging.INFO, "frontend_lovelace_resource_created")
    except (HomeAssistantError, AttributeError, KeyError, TypeError) as exc:
        log_event(
            _LOGGER,
            logging.WARNING,
            "frontend_lovelace_registration_skipped",
            error_type=type(exc).__name__,
        )
