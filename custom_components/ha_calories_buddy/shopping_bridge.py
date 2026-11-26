"""Bridge pushing checked ShoppingDraft items to Home Assistant todo.* with dedup."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.ha_calories_buddy.core.logging import get_logger, log_event
from custom_components.ha_calories_buddy.core.models import PersonProfile
from custom_components.ha_calories_buddy.core.planner.shopping import (
    filter_deduplicated_items,
    format_todo_item_summary,
)
from custom_components.ha_calories_buddy.runtime import DomainRuntime

_LOGGER = get_logger(__name__)


async def async_commit_draft_to_todo(
    hass: HomeAssistant,
    runtime: DomainRuntime,
    profile: PersonProfile,
    *,
    draft_id: str | None = None,
    todo_entity_override: str | None = None,
) -> dict[str, Any]:
    """Push checked items from a ShoppingDraft into HA todo.*, skipping existing items."""
    todo_entity = (
        (todo_entity_override or "").strip()
        or (profile.entity_mappings.todo_entity or "").strip()
    )
    if not todo_entity:
        raise HomeAssistantError(
            "No todo.* shopping list entity configured for this person."
        )
    if not hass.services.has_service("todo", "add_item"):
        raise HomeAssistantError("Home Assistant todo service is unavailable.")

    draft = (
        await runtime.shopping_repo.get_draft(draft_id)
        if draft_id
        else await runtime.shopping_repo.get_latest_draft(profile.person_id)
    )
    if draft is None:
        raise HomeAssistantError("No active shopping draft found.")

    existing_summaries: list[str] = []
    if hass.services.has_service("todo", "get_items"):
        try:
            resp = await hass.services.async_call(
                "todo",
                "get_items",
                {"entity_id": todo_entity, "status": ["needs_action"]},
                blocking=True,
                return_response=True,
            )
            if isinstance(resp, dict):
                entity_data = resp.get(todo_entity)
                if entity_data is None and len(resp) == 1:
                    entity_data = next(iter(resp.values()))
                if isinstance(entity_data, dict):
                    raw_items = entity_data.get("items")
                    if isinstance(raw_items, list):
                        for raw_it in raw_items:
                            if isinstance(raw_it, dict) and raw_it.get("summary"):
                                existing_summaries.append(str(raw_it["summary"]))
        except Exception as exc:  # noqa: BLE001
            log_event(
                _LOGGER,
                logging.WARNING,
                "todo_get_items_fallback",
                entity_id=todo_entity,
                error_type=type(exc).__name__,
            )

    to_add, skipped_dups = filter_deduplicated_items(draft.items, existing_summaries)

    added_labels: list[str] = []
    for item in to_add:
        summary = format_todo_item_summary(item, language=profile.language)
        await hass.services.async_call(
            "todo",
            "add_item",
            {"entity_id": todo_entity, "item": summary},
            blocking=True,
        )
        added_labels.append(summary)

    committed = await runtime.shopping_repo.mark_draft_committed(draft.draft_id)
    await runtime.async_notify_person_updated(
        profile.person_id, "shopping_draft_committed"
    )
    log_event(
        _LOGGER,
        logging.INFO,
        "shopping_draft_committed",
        person_id=profile.person_id,
        todo_entity=todo_entity,
        added_count=len(added_labels),
        skipped_duplicates=len(skipped_dups),
    )
    return {
        "draft": (committed or draft).model_dump(mode="json"),
        "todo_entity": todo_entity,
        "added_count": len(added_labels),
        "skipped_duplicates_count": len(skipped_dups),
        "added_items": added_labels,
        "skipped_duplicates": [it.label for it in skipped_dups],
    }
