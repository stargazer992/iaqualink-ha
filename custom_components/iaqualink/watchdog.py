"""Re-send iCL light commands that didn't take, but only while the controller can act."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import httpx
from iaqualink.exception import AqualinkServiceException

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .icl import SYSTEM_ONLINE, SYSTEM_SERVICE, IclManager, IclZone

if TYPE_CHECKING:
    from .coordinator import AqualinkDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

EVENT_WATCHDOG = f"{DOMAIN}_icl_watchdog"

WATCHDOG_INTERVAL = 900.0
RESUME_DELAY = 60.0
VERIFY_DELAY = 15.0
MAX_FAILURES = 2


class IclWatchdog:
    """Every 15 minutes, put iCL zones back in the state they were last told to be in.

    Nothing is sent while the system is unavailable, offline or in Service mode;
    passes start again once it has been back online for a minute.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: AqualinkDataUpdateCoordinator,
        manager: IclManager,
    ) -> None:
        """Initialize the watchdog."""
        self.hass = hass
        self.coordinator = coordinator
        self.manager = manager
        self.reachable = True
        self.paused_reason: str | None = None
        self.failures: dict[int, int] = {}
        self._next_pass = time.monotonic() + WATCHDOG_INTERVAL
        self._task: asyncio.Task[None] | None = None

    def pause_reason(self) -> str | None:
        """Return why commands can't reach the lights now, or None."""
        system = self.manager.system
        if not self.reachable or system.online is None:
            return "unavailable"
        if system.online is False:
            return "offline"
        status = self.manager.system_status
        if status == SYSTEM_SERVICE:
            return "in Service mode"
        if status not in (None, SYSTEM_ONLINE):
            return f"reporting status {status or 'unknown'!r}"
        return None

    @callback
    def async_check(self) -> bool:
        """Run after every poll; start a pass when one is due. Return if started."""
        now = time.monotonic()
        if reason := self.pause_reason():
            if reason != self.paused_reason:
                _LOGGER.info(
                    "iCL watchdog paused: system %s is %s",
                    self.manager.system.serial,
                    reason,
                )
            self.paused_reason = reason
            return False
        if self.paused_reason is not None:
            _LOGGER.info(
                "iCL watchdog: system %s is back, checking the lights in %ss",
                self.manager.system.serial,
                int(RESUME_DELAY),
            )
            self.paused_reason = None
            self._next_pass = min(self._next_pass, now + RESUME_DELAY)
            return False
        if now < self._next_pass or (self._task is not None and not self._task.done()):
            return False
        self._next_pass = now + WATCHDOG_INTERVAL
        self._task = self.coordinator.config_entry.async_create_background_task(
            self.hass, self.async_run_pass(), f"{DOMAIN} iCL watchdog"
        )
        return True

    async def async_run_pass(self) -> None:
        """Check every zone once."""
        for zone in list(self.manager.zones.values()):
            if self.pause_reason():
                return
            await self._async_check_zone(zone)

    async def _async_check_zone(self, zone: IclZone) -> None:
        desired = zone.desired_on
        if zone.local is not None:
            return
        if desired is None or not zone.reports_state or zone.settling:
            return
        if zone.reported_on == desired:
            self._record(zone, ok=True)
            return
        _LOGGER.warning(
            "iCL watchdog: %s should be %s but reports %s; sending it again",
            zone.label,
            _word(desired),
            _word(zone.reported_on),
        )
        try:
            if desired:
                await zone.turn_on(None, zone.brightness_percentage)
            else:
                await zone.turn_off()
        except (AqualinkServiceException, TimeoutError, httpx.HTTPError) as err:
            _LOGGER.warning("iCL watchdog: resending to %s failed: %s", zone.label, err)
        self.coordinator.async_update_listeners()
        await asyncio.sleep(VERIFY_DELAY)
        if self.pause_reason():
            _LOGGER.info(
                "iCL watchdog: system went %s; not counting %s as failed",
                self.pause_reason(),
                zone.label,
            )
            return
        answered = await self.manager.async_read_zones()
        if self.pause_reason():
            return
        self._record(zone, ok=answered and zone.reported_on == desired, resent=True)

    def _record(self, zone: IclZone, *, ok: bool, resent: bool = False) -> None:
        previous = self.failures.get(zone.zone_id, 0)
        issue_id = f"icl_watchdog_{self.manager.system.serial}_{zone.zone_id}"
        if ok:
            self.failures[zone.zone_id] = 0
            if previous >= MAX_FAILURES:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            if resent:
                _LOGGER.info("iCL watchdog: %s is back in sync", zone.label)
                self._fire(zone, "fixed", 0)
            return
        count = previous + 1
        self.failures[zone.zone_id] = count
        _LOGGER.warning(
            "iCL watchdog: %s is still not %s (%s failed checks)",
            zone.label,
            _word(zone.desired_on),
            count,
        )
        self._fire(zone, "failed", count)
        if count == MAX_FAILURES:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="icl_watchdog",
                translation_placeholders={
                    "zone": zone.label,
                    "desired": _word(zone.desired_on),
                    "count": str(count),
                    "minutes": str(count * 15),
                },
            )

    def _fire(self, zone: IclZone, result: str, failures: int) -> None:
        self.hass.bus.async_fire(
            EVENT_WATCHDOG,
            {
                "serial": self.manager.system.serial,
                "zone_id": zone.zone_id,
                "zone": zone.label,
                "desired": _word(zone.desired_on),
                "result": result,
                "failures": failures,
            },
        )


def _word(on: bool | None) -> str:
    return {True: "on", False: "off"}.get(on, "unknown")  # type: ignore[arg-type]
