"""Support for Aqualink temperature sensors."""

from typing import Any, override

from iaqualink.device import AqualinkBinarySensor

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AqualinkConfigEntry
from .coordinator import AqualinkDataUpdateCoordinator
from .entity import AqualinkEntity
from .local import LocalSystem
from .mismatch import MismatchTracker
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
    runtime_data = config_entry.runtime_data
    entities.extend(
        HassAqualinkCloudMismatchSensor(
            coordinator, runtime_data.cloud_coordinators[serial]
        )
        for serial, coordinator in runtime_data.coordinators.items()
        if isinstance(coordinator.system, LocalSystem)
        and serial in runtime_data.cloud_coordinators
    )
    async_add_entities(entities)


class HassAqualinkCloudMismatchSensor(AqualinkSystemEntity, BinarySensorEntity):
    """On while Jandy Bridge and the cloud have disagreed for MISMATCH_AFTER."""

    _attr_name = "Cloud mismatch"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:compare-horizontal"

    def __init__(
        self,
        coordinator: AqualinkDataUpdateCoordinator,
        cloud: AqualinkDataUpdateCoordinator,
    ) -> None:
        """Compare the local devices with the cloud's on every update of either."""
        super().__init__(coordinator, "cloud_mismatch")
        self._cloud = cloud
        self._tracker = MismatchTracker()
        self._lasting: list[str] = []

    @override
    async def async_added_to_hass(self) -> None:
        """Also follow the cloud's polls."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._cloud.async_add_listener(self._handle_coordinator_update)
        )
        self._compare()

    def _compare(self) -> None:
        self._lasting = self._tracker.update(self.coordinator.system.devices)

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        self._compare()
        super()._handle_coordinator_update()

    @property
    @override
    def is_on(self) -> bool:
        """Return whether a difference has lasted."""
        return bool(self._lasting)

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """List the lasting differences."""
        return {"mismatches": self._lasting}


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
        elif dev.name == "battery_low":
            self._attr_device_class = BinarySensorDeviceClass.BATTERY

    @property
    @override
    def is_on(self) -> bool:
        """Return whether the binary sensor is on or not."""
        return self.dev.is_on
