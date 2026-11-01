"""Data update coordinator for iaqualink."""

import logging
from typing import TYPE_CHECKING, Any, override

import httpx
from iaqualink.exception import (
    AqualinkServiceException,
    AqualinkServiceThrottledException,
    AqualinkServiceUnauthorizedException,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    AQUALINKD_ISSUE_FAILURES,
    DOMAIN,
    UPDATE_INTERVAL_BY_SYSTEM_TYPE,
    UPDATE_INTERVAL_CLOUD_IDLE,
    UPDATE_INTERVAL_DEFAULT,
)
from .icl import IclManager
from .utils import error_detail

if TYPE_CHECKING:
    from .watchdog import IclWatchdog

_LOGGER = logging.getLogger(__name__)


class AqualinkDataUpdateCoordinator(DataUpdateCoordinator[None]):
    """Data coordinator for Aqualink systems."""

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry, system: Any
    ) -> None:
        """Initialize the coordinator."""
        update_interval = UPDATE_INTERVAL_BY_SYSTEM_TYPE.get(
            system.NAME, UPDATE_INTERVAL_DEFAULT
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_{system.serial}",
            update_interval=update_interval,
        )
        self.system = system
        self.icl: IclManager | None = None
        self.watchdog: IclWatchdog | None = None
        # Local mode: the cloud coordinator kept for the lights and as fallback.
        self.cloud: AqualinkDataUpdateCoordinator | None = None
        self.cloud_idle = False
        self.local_failures = 0

    async def async_refresh_after_command(self) -> None:
        """Show the result of a command.

        iAqua replies carry the new state, so redrawing is enough. Other systems
        (EXO) only acknowledge, so fetch the state, skipping the library's
        refresh throttle.
        """
        if self.system.NAME == "iaqua":
            self.async_update_listeners()
            return
        self.async_update_listeners()
        self.system.last_refresh = 0
        await self.async_request_refresh()

    @override
    async def _async_update_data(self) -> None:
        """Refresh internal state for a system, then let the watchdog look."""
        reachable = False
        try:
            await self._async_update_system()
            reachable = True
        finally:
            if self.system.NAME == "aqualinkd":
                self._local_result(reachable)
            if self.watchdog is not None:
                self.watchdog.reachable = reachable
                self.watchdog.async_check()

    @property
    def local_issue_id(self) -> str:
        """Return the repair issue raised while AqualinkD doesn't answer."""
        return f"aqualinkd_unreachable_{self.system.serial}"

    def _local_result(self, reachable: bool) -> None:
        """Raise or clear the repair issue and pace the cloud fallback."""
        if reachable:
            if self.local_failures >= AQUALINKD_ISSUE_FAILURES:
                ir.async_delete_issue(self.hass, DOMAIN, self.local_issue_id)
                _LOGGER.info("AqualinkD answers again; leaving the cloud fallback")
            self.local_failures = 0
            if self.cloud is not None and self.cloud_idle:
                self.cloud.update_interval = UPDATE_INTERVAL_CLOUD_IDLE
            return
        self.local_failures += 1
        if self.cloud is not None and self.local_failures == 1:
            # Keep the stand-in values fresh while AqualinkD is away.
            self.cloud.update_interval = UPDATE_INTERVAL_BY_SYSTEM_TYPE["iaqua"]
            self.hass.async_create_task(self.cloud.async_request_refresh())
        if self.local_failures == AQUALINKD_ISSUE_FAILURES:
            _LOGGER.warning("AqualinkD is not answering; using the iAquaLink cloud")
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self.local_issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="aqualinkd_unreachable",
                translation_placeholders={"url": self.system.client.base_url},
            )

    async def _async_update_system(self) -> None:
        try:
            await self.system.update()
        except AqualinkServiceUnauthorizedException as err:
            raise ConfigEntryAuthFailed("Invalid credentials for iAquaLink") from err
        except AqualinkServiceThrottledException:
            _LOGGER.warning(
                "Rate limited by iAquaLink system %s, will retry later",
                self.system.serial,
            )
            return
        except (AqualinkServiceException, TimeoutError, httpx.HTTPError) as err:
            raise UpdateFailed(
                "Unable to update iAquaLink system "
                f"{self.system.serial}: {error_detail(err)}"
            ) from err
        if self.system.online is not True:
            raise UpdateFailed(f"iAquaLink system {self.system.serial} is offline")
        if self.icl is not None:
            await self.icl.async_poll()
