"""Tests for Custom Card frontend assets and Lovelace auto-registration."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.core import HomeAssistant

from custom_components.ha_calories_buddy.const import FRONTEND_CARD_URL
from custom_components.ha_calories_buddy.frontend import (
    FRONTEND_DIR,
    async_register_frontend,
)


def test_frontend_bundle_files_and_exports_exist() -> None:
    """Verify modular ES frontend files exist with expected custom element hooks."""
    expected_files = [
        "ha-calories-buddy-card.js",
        "api.js",
        "styles.js",
        "utils.js",
        "i18n/index.js",
        "i18n/en.js",
        "i18n/it.js",
        "components/quick-log-bar.js",
        "components/barcode-decoder.js",
        "components/barcode-scanner.js",
        "views/today-view.js",
        "views/plan-view.js",
        "views/shopping-view.js",
        "views/workouts-subview.js",
        "views/trends-view.js",
        "views/vault-view.js",
        "views/recipes-subview.js",
        "views/preferences-view.js",
    ]
    for rel in expected_files:
        path = Path(FRONTEND_DIR) / rel
        assert path.is_file(), f"Missing frontend module: {path}"
        content = path.read_text(encoding="utf-8")
        assert len(content.strip()) > 0

    brand_dir = Path(FRONTEND_DIR).parent / "brand"
    for png in (
        "icon.png",
        "icon@2x.png",
        "logo.png",
        "logo@2x.png",
        "dark_logo.png",
        "dark_logo@2x.png",
    ):
        png_path = brand_dir / png
        assert png_path.is_file(), f"Missing brand asset: {png_path}"
        assert png_path.stat().st_size > 100

    entry_js = (Path(FRONTEND_DIR) / "ha-calories-buddy-card.js").read_text(
        encoding="utf-8"
    )
    assert "ha-calories-buddy-card" in entry_js
    assert "ha-calories-buddy-card-editor" in entry_js
    assert "window.customCards" in entry_js

    decoder_js = (Path(FRONTEND_DIR) / "components/barcode-decoder.js").read_text(
        encoding="utf-8"
    )
    assert "export async function decodeBarcodeFromImageSource" in decoder_js
    assert "export function validateEanCheckDigit" in decoder_js


async def test_async_register_frontend_registers_static_and_lovelace_resource(
    hass: HomeAssistant,
) -> None:
    """Verify static path registration and Lovelace storage resource creation/update."""
    mock_http = MagicMock()
    mock_http.async_register_static_paths = AsyncMock()
    hass.http = mock_http  # type: ignore[assignment]

    mock_resources = MagicMock()
    mock_resources.async_get_info = AsyncMock(return_value={"resources": 0})
    mock_resources.async_items = MagicMock(return_value=[])
    mock_resources.async_create_item = AsyncMock()
    mock_resources.async_update_item = AsyncMock()

    hass.data[LOVELACE_DATA] = SimpleNamespace(resources=mock_resources)  # type: ignore[assignment]

    await async_register_frontend(hass)

    mock_http.async_register_static_paths.assert_awaited_once()
    mock_resources.async_create_item.assert_awaited_once_with(
        {"res_type": "module", "url": FRONTEND_CARD_URL}
    )

    # Idempotent if exact version already registered
    mock_resources.async_items = MagicMock(
        return_value=[{"id": "res_1", "url": FRONTEND_CARD_URL}]
    )
    await async_register_frontend(hass)
    assert mock_resources.async_create_item.await_count == 1
