"""AqualinkD colour light programs."""

from typing import override

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AqualinkConfigEntry, refresh_system
from .entity import AqualinkEntity
from .local import LocalSelect
from .utils import await_or_reraise

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: AqualinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up AqualinkD light programs."""
    async_add_entities(
        HassAqualinkSelect(
            config_entry.runtime_data.coordinators[dev.system.serial], dev
        )
        for dev in config_entry.runtime_data.selects
    )


class HassAqualinkSelect(AqualinkEntity[LocalSelect], SelectEntity):
    """A colour light's program, chosen through AqualinkD."""

    _attr_icon = "mdi:palette"

    @property
    @override
    def options(self) -> list[str]:
        """Return the program names."""
        return self.dev.options

    @property
    @override
    def current_option(self) -> str | None:
        """Return the running program."""
        return self.dev.state if self.dev.state in self.dev.options else None

    @refresh_system
    @override
    async def async_select_option(self, option: str) -> None:
        """Start the program."""
        await await_or_reraise(
            self.hass, self.coordinator.config_entry, self.dev.select(option)
        )
