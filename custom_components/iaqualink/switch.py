"""Support for Aqualink pool feature switches."""

from typing import Any, override

from iaqualink.device import AqualinkSwitch

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AqualinkConfigEntry, refresh_system
from .const import (
    CONF_AQUALINKD_URL,
    CONF_CONNECTION,
    CONNECTION_CLOUD,
    CONNECTION_LOCAL,
    DOMAIN,
)
from .coordinator import AqualinkDataUpdateCoordinator
from .entity import AqualinkEntity
from .local import LocalSystem
from .utils import await_or_reraise

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: AqualinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up discovered switches."""
    async_add_entities(
        HassAqualinkSwitch(
            config_entry.runtime_data.coordinators[dev.system.serial], dev
        )
        for dev in config_entry.runtime_data.switches
    )
    async_add_entities(
        AqualinkConnectionSwitch(config_entry, serial)
        for serial, coordinator in config_entry.runtime_data.coordinators.items()
        if coordinator.system.NAME in ("iaqua", "aqualinkd")
    )


class AqualinkConnectionSwitch(SwitchEntity):
    """On for the iAquaLink cloud, off for Local (AqualinkD).

    It only changes the entry's connection option, so it works while AqualinkD
    is unreachable.
    """

    _attr_has_entity_name = True
    _attr_name = "Cloud connection"
    _attr_should_poll = False

    def __init__(self, entry: AqualinkConfigEntry, serial: str) -> None:
        """Attach the switch to the system's device."""
        self._entry = entry
        self._serial = serial
        self._attr_unique_id = f"{serial}_cloud_connection"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, serial)})

    @property
    @override
    def is_on(self) -> bool:
        """Return whether the cloud is the connection."""
        return self._entry.options.get(CONF_CONNECTION) != CONNECTION_LOCAL

    @property
    def in_use(self) -> str | None:
        """Return which connection is running the equipment right now."""
        if self.is_on:
            return CONNECTION_CLOUD
        runtime = self._entry.runtime_data
        system = runtime.coordinators[self._serial].system
        if isinstance(system, LocalSystem) and system.online is not False:
            return CONNECTION_LOCAL
        cloud = runtime.cloud_coordinators.get(self._serial)
        cloud_system = cloud.system if cloud else system
        # Local is picked but AqualinkD is down: the cloud stands in.
        return CONNECTION_CLOUD if cloud_system.online is True else None

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the connection in use, which differs while AqualinkD is down."""
        return {"in_use": self.in_use}

    @override
    async def async_added_to_hass(self) -> None:
        """Follow AqualinkD and the cloud going up and down."""
        runtime = self._entry.runtime_data
        for coordinator in (
            runtime.coordinators.get(self._serial),
            runtime.cloud_coordinators.get(self._serial),
        ):
            if coordinator is not None:
                self.async_on_remove(
                    coordinator.async_add_listener(self.async_write_ha_state)
                )

    @property
    @override
    def icon(self) -> str:
        """Return a cloud or a network icon."""
        return "mdi:cloud" if self.is_on else "mdi:lan"

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Use the iAquaLink cloud."""
        self._set_connection(CONNECTION_CLOUD)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Use AqualinkD."""
        if not self._entry.options.get(CONF_AQUALINKD_URL):
            raise HomeAssistantError(
                "Set the AqualinkD address in the iAquaLink options first"
            )
        self._set_connection(CONNECTION_LOCAL)

    def _set_connection(self, connection: str) -> None:
        if self._entry.options.get(CONF_CONNECTION, CONNECTION_CLOUD) == connection:
            return
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, CONF_CONNECTION: connection}
        )
        self.async_write_ha_state()
        self.hass.config_entries.async_schedule_reload(self._entry.entry_id)


class HassAqualinkSwitch(AqualinkEntity[AqualinkSwitch], SwitchEntity):
    """Representation of a switch."""

    def __init__(
        self, coordinator: AqualinkDataUpdateCoordinator, dev: AqualinkSwitch
    ) -> None:
        """Initialize AquaLink switch."""
        super().__init__(coordinator, dev)
        name = dev.label
        if name == "Cleaner":
            self._attr_icon = "mdi:robot-vacuum"
        elif name == "Waterfall" or name.endswith("Dscnt"):
            self._attr_icon = "mdi:fountain"
        elif name.endswith(("Pump", "Blower")):
            self._attr_icon = "mdi:fan"
        if name.endswith("Heater"):
            self._attr_icon = "mdi:radiator"

    @property
    @override
    def is_on(self) -> bool:
        """Return whether the switch is on or not."""
        return self.dev.is_on

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return AqualinkD's panel timer for the button, if it has one."""
        data = getattr(self.dev, "data", {})
        if "timer_active" not in data:
            return None
        attributes: dict[str, Any] = {"timer_active": data["timer_active"] == "on"}
        if "timer_duration" in data:
            attributes["timer_remaining"] = int(data["timer_duration"])
        return attributes

    @refresh_system
    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch."""
        await await_or_reraise(
            self.hass, self.coordinator.config_entry, self.dev.turn_on()
        )

    @refresh_system
    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch."""
        await await_or_reraise(
            self.hass, self.coordinator.config_entry, self.dev.turn_off()
        )
