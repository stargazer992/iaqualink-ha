"""Diagnostics platform for iAquaLink."""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import AqualinkConfigEntry

TO_REDACT = {
    "address",
    "address_1",
    "address_2",
    "city",
    "email",
    "first_name",
    "id",
    "last_name",
    "latitude",
    "longitude",
    "owner_id",
    "phone",
    "postal_code",
    "serial",
    "serial_number",
    "username",
    "zip",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AqualinkConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime_data = entry.runtime_data
    coordinators = [
        *runtime_data.coordinators.values(),
        *runtime_data.cloud_coordinators.values(),
    ]
    systems = [
        {
            "type": coordinator.system.NAME,
            "online": coordinator.system.online,
            "raw": coordinator.icl.raw
            if coordinator.icl is not None
            else getattr(coordinator.system, "raw", {}),
            "data": {k: v for k, v in coordinator.system.data.items() if k != "name"},
            "devices": {
                name: {"class": obj.__class__.__name__, "data": obj.data}
                for name, obj in (
                    getattr(coordinator.system, "devices", None) or {}
                ).items()
            },
        }
        for coordinator in coordinators
    ]

    return {"systems": async_redact_data(systems, TO_REDACT)}
