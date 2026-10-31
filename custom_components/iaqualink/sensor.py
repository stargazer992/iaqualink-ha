"""Support for Aqualink temperature sensors."""

from typing import override

from iaqualink.device import AqualinkSensor

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AqualinkConfigEntry
from .coordinator import AqualinkDataUpdateCoordinator
from .entity import AqualinkEntity
from .status import STATUS_OPTIONS, AqualinkSystemEntity, system_status

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: AqualinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up discovered sensors."""
    entities: list[SensorEntity] = [
        HassAqualinkSensor(
            config_entry.runtime_data.coordinators[dev.system.serial], dev
        )
        for dev in config_entry.runtime_data.sensors
    ]
    entities.extend(
        HassAqualinkSystemStatusSensor(coordinator)
        for coordinator in config_entry.runtime_data.coordinators.values()
        if coordinator.icl is not None
    )
    async_add_entities(entities)


class HassAqualinkSystemStatusSensor(AqualinkSystemEntity, SensorEntity):
    """Online, Offline or Service, as the iAquaLink app shows it."""

    _attr_name = "System status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATUS_OPTIONS
    _attr_icon = "mdi:pool"

    def __init__(self, coordinator: AqualinkDataUpdateCoordinator) -> None:
        """Initialize the status sensor."""
        super().__init__(coordinator, "system_status")

    @property
    @override
    def native_value(self) -> str:
        """Return the system status."""
        return system_status(self.coordinator)


class HassAqualinkSensor(AqualinkEntity[AqualinkSensor], SensorEntity):
    """Representation of a sensor."""

    def __init__(
        self, coordinator: AqualinkDataUpdateCoordinator, dev: AqualinkSensor
    ) -> None:
        """Initialize AquaLink sensor."""
        super().__init__(coordinator, dev)
        if not dev.name.endswith("_temp"):
            return
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        if dev.system.temp_unit == "F":
            self._attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
            return
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    @property
    @override
    def native_value(self) -> int | float | None:
        """Return the state of the sensor."""
        if self.dev.state == "":
            return None

        try:
            return int(self.dev.state)
        except ValueError:
            return float(self.dev.state)
