"""System status (Online / Offline / Service) for iAqua systems."""

from typing import override

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AqualinkDataUpdateCoordinator
from .icl import SYSTEM_ONLINE, SYSTEM_SERVICE

STATUS_ONLINE = "Online"
STATUS_OFFLINE = "Offline"
STATUS_SERVICE = "Service"
STATUS_OPTIONS = [STATUS_ONLINE, STATUS_OFFLINE, STATUS_SERVICE]


def system_status(coordinator: AqualinkDataUpdateCoordinator) -> str:
    """Return Service, Online, or Offline (also when the cloud can't be reached)."""
    status = coordinator.icl.system_status if coordinator.icl is not None else None
    if status == SYSTEM_SERVICE:
        return STATUS_SERVICE
    if (
        coordinator.last_update_success
        and coordinator.system.online is True
        and status in (None, SYSTEM_ONLINE)
    ):
        return STATUS_ONLINE
    return STATUS_OFFLINE


class AqualinkSystemEntity(CoordinatorEntity[AqualinkDataUpdateCoordinator]):
    """An entity of the system itself; stays available to report Offline."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AqualinkDataUpdateCoordinator, key: str) -> None:
        """Attach the entity to the system's device."""
        super().__init__(coordinator)
        serial = coordinator.system.serial
        self._attr_unique_id = f"{serial}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, serial)})

    @property
    @override
    def available(self) -> bool:
        """Always available, so an unreachable system shows as Offline."""
        return True
