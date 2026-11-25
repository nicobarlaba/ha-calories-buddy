"""Home Assistant user and person identity resolution."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.util import slugify

from custom_components.ha_calories_buddy.const import DOMAIN
from custom_components.ha_calories_buddy.core.models import PersonProfile
from custom_components.ha_calories_buddy.runtime import DomainRuntime


class PersonNotFoundError(Exception):
    """Raised when no matching person profile is registered."""


class PersonAmbiguousError(Exception):
    """Raised when multiple profiles exist and caller identity cannot disambiguate."""


def resolve_person(
    hass: HomeAssistant,
    explicit_person: str | None = None,
    context_user_id: str | None = None,
) -> PersonProfile:
    """Resolve the active PersonProfile using explicit name, HA user_id, or single-person default."""
    runtime: DomainRuntime | None = hass.data.get(DOMAIN)
    if runtime is None or not runtime.persons:
        raise PersonNotFoundError("No ha-calories-buddy person profiles are active")

    profiles = [prt.profile for prt in runtime.persons.values()]

    if explicit_person:
        target = explicit_person.strip().lower()
        target_slug = slugify(explicit_person)
        for profile in profiles:
            if (
                profile.person_id.lower() == target
                or profile.display_name.lower() == target
                or slugify(profile.display_name) == target_slug
                or (
                    profile.ha_person_entity
                    and profile.ha_person_entity.lower() == target
                )
            ):
                return profile
        raise PersonNotFoundError(f"Unknown person profile: {explicit_person}")

    if context_user_id:
        for profile in profiles:
            if profile.ha_user_id == context_user_id:
                return profile

    if len(profiles) == 1:
        return profiles[0]

    names = ", ".join(p.display_name for p in profiles)
    raise PersonAmbiguousError(
        f"Multiple person profiles configured ({names}); please specify which person"
    )
