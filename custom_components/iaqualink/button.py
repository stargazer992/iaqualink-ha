"""AqualinkD one-shot actions."""

from typing import override

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AqualinkConfigEntry
from .entity import AqualinkEntity
from .local import LocalButton
from .utils import await_or_reraise

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: AqualinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up AqualinkD buttons."""
    async_add_entities(
        HassAqualinkButton(
            config_entry.runtime_data.coordinators[dev.system.serial], dev
        )
        for dev in config_entry.runtime_data.buttons
    )


class HassAqualinkButton(AqualinkEntity[LocalButton], ButtonEntity):
    """Sync the panel's clock to AqualinkD's."""

    _attr_icon = "mdi:clock-check-outline"

    @override
    async def async_press(self) -> None:
        """Send the action."""
        await await_or_reraise(
            self.hass, self.coordinator.config_entry, self.dev.press()
        )
