"""Support for Aqualink temperature sensors."""

import time
from typing import override

from iaqualink.device import AqualinkSensor

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AqualinkConfigEntry
from .coordinator import AqualinkDataUpdateCoordinator
from .entity import AqualinkEntity
from .status import STATUS_OPTIONS, AqualinkSystemEntity, system_status

PARALLEL_UPDATES = 0

# Numeric readings that get long-term statistics.
MEASUREMENTS = {"pool_salinity", "spa_salinity", "ph", "orp", "swg_percent"}


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
        HassAqualinkPumpEnergySensor(
            config_entry.runtime_data.coordinators[dev.system.serial], dev
        )
        for dev in config_entry.runtime_data.sensors
        if getattr(dev, "ha_unit", None) == UnitOfPower.WATT
    )
    entities.extend(
        HassAqualinkSystemStatusSensor(coordinator)
        for coordinator in config_entry.runtime_data.coordinators.values()
        if coordinator.icl is not None or coordinator.system.NAME == "aqualinkd"
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
        unit = getattr(dev, "ha_unit", None)
        if unit:
            self._attr_native_unit_of_measurement = unit
        if unit == UnitOfPower.WATT:
            self._attr_device_class = SensorDeviceClass.POWER
        if unit or dev.name.endswith("_temp") or dev.name in MEASUREMENTS:
            self._attr_state_class = SensorStateClass.MEASUREMENT
        if not dev.name.endswith("_temp"):
            return
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        if dev.system.temp_unit == "F":
            self._attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
            return
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    @property
    @override
    def native_value(self) -> int | float | str | None:
        """Return the state of the sensor."""
        if self.dev.state == "":
            return None

        try:
            return int(self.dev.state)
        except ValueError:
            pass
        try:
            return float(self.dev.state)
        except ValueError:
            return self.dev.state


class HassAqualinkPumpEnergySensor(AqualinkEntity[AqualinkSensor], RestoreSensor):
    """Pump energy in kWh, summed from AqualinkD's watts for the Energy dashboard."""

    _attr_name = "Energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2

    def __init__(
        self, coordinator: AqualinkDataUpdateCoordinator, dev: AqualinkSensor
    ) -> None:
        """Initialize from the pump's watts sensor."""
        super().__init__(coordinator, dev)
        self._attr_unique_id = f"{dev.system.serial}_{dev.name}_energy"
        self._kwh = 0.0
        self._last: tuple[float, float] | None = None

    async def async_added_to_hass(self) -> None:
        """Carry the total across restarts."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_sensor_data()) is not None:
            try:
                self._kwh = float(last.native_value or 0)
            except TypeError, ValueError:
                self._kwh = 0.0
        self._sample()

    def _sample(self) -> None:
        try:
            watts = float(self.dev.state)
        except TypeError, ValueError:
            self._last = None
            return
        now = time.monotonic()
        if self._last is not None:
            previous, then = self._last
            self._kwh += previous * (now - then) / 3_600_000
        self._last = (watts, now)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._sample()
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float:
        """Return the total energy."""
        return round(self._kwh, 3)
