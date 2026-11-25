"""Abstract protocol for external food data providers."""

from __future__ import annotations

from typing import Protocol

from custom_components.ha_calories_buddy.core.models import FoodItem


class FoodProvider(Protocol):
    """Protocol implemented by external structured nutrition databases."""

    async def lookup_barcode(self, ean: str) -> FoodItem | None:
        """Look up a product by EAN/UPC barcode."""

    async def search(
        self,
        query: str,
        brand: str | None = None,
        language: str = "en",
        limit: int = 5,
    ) -> list[FoodItem]:
        """Search products by name and optional brand."""
