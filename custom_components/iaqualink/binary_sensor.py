"""Support for Aqualink temperature sensors."""

from typing import override

from iaqualink.device import AqualinkBinarySensor

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AqualinkConfigEntry
from .coordinator import AqualinkDataUpdateCoordinator
from .entity import AqualinkEntity
from .status import STATUS_SERVICE, AqualinkSystemEntity, system_status

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: AqualinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up discovered binary sensors."""
    entities: list[BinarySensorEntity] = [
        HassAqualinkBinarySensor(
            config_entry.runtime_data.coordinators[dev.system.serial], dev
        )
        for dev in config_entry.runtime_data.binary_sensors
    ]
    entities.extend(
        HassAqualinkServiceModeSensor(coordinator)
        for coordinator in config_entry.runtime_data.coordinators.values()
        if coordinator.icl is not None or coordinator.system.NAME == "aqualinkd"
    )
    async_add_entities(entities)


class HassAqualinkServiceModeSensor(AqualinkSystemEntity, BinarySensorEntity):
    """On while the controller is in Service mode."""

    _attr_name = "Service mode"
    _attr_icon = "mdi:account-wrench"

    def __init__(self, coordinator: AqualinkDataUpdateCoordinator) -> None:
        """Initialize the Service mode sensor."""
        super().__init__(coordinator, "service_mode")

    @property
    @override
    def is_on(self) -> bool:
        """Return whether the controller is in Service mode."""
        return system_status(self.coordinator) == STATUS_SERVICE


class HassAqualinkBinarySensor(
    AqualinkEntity[AqualinkBinarySensor], BinarySensorEntity
):
    """Representation of a binary sensor."""

    def __init__(
        self, coordinator: AqualinkDataUpdateCoordinator, dev: AqualinkBinarySensor
    ) -> None:
        """Initialize AquaLink binary sensor."""
        super().__init__(coordinator, dev)
        if dev.label == "Freeze Protection":
            self._attr_device_class = BinarySensorDeviceClass.COLD

    @property
    @override
    def is_on(self) -> bool:
        """Return whether the binary sensor is on or not."""
        return self.dev.is_on
