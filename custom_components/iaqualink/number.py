"""AqualinkD settable values: SWG %, setpoints, pump speed, dimmers."""

from typing import override

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AqualinkConfigEntry, refresh_system
from .entity import AqualinkEntity
from .local import LocalNumber
from .utils import await_or_reraise

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: AqualinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up AqualinkD numbers."""
    async_add_entities(
        HassAqualinkNumber(
            config_entry.runtime_data.coordinators[dev.system.serial], dev
        )
        for dev in config_entry.runtime_data.numbers
    )


class HassAqualinkNumber(AqualinkEntity[LocalNumber], NumberEntity):
    """A value set through AqualinkD."""

    _attr_mode = NumberMode.BOX

    @property
    @override
    def native_value(self) -> float | None:
        """Return the current value."""
        return self.dev.value

    @property
    @override
    def native_min_value(self) -> float:
        """Return the lowest allowed value."""
        return float(self.dev.data["min"])

    @property
    @override
    def native_max_value(self) -> float:
        """Return the highest allowed value."""
        return float(self.dev.data["max"])

    @property
    @override
    def native_step(self) -> float:
        """Return the step."""
        return float(self.dev.data["step"])

    @property
    @override
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit."""
        return self.dev.data.get("unit")

    @refresh_system
    @override
    async def async_set_native_value(self, value: float) -> None:
        """Send the new value to AqualinkD."""
        await await_or_reraise(
            self.hass, self.coordinator.config_entry, self.dev.set_value(value)
        )
