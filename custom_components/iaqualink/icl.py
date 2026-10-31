"""iCL (Jandy Infinite WaterColors) light zones on iAqua systems.

iaqualink 0.7.0 doesn't know about iCL zones, so they are read and controlled
here, over the library's session request. Zones are read from the
``icl_info_list`` of the get_devices reply when the controller includes it,
otherwise from get_icl_info.

The controller only lights a zone reliably with the sequence the iAquaLink app
sends (captured from the app): off, wait 2 s, on, wait 1 s, then define the
custom colour. Turning on therefore always runs that sequence, with the last
colour when no new one is given. Brightness alone is a set_iclzone_color with
only a dim level, which keeps the colour.

The cloud reports zone state late (and some zones, often the spa, only as
"absent"), so the zone keeps the state it was last commanded to for a while
instead of letting a stale report flip it back.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

import httpx
from iaqualink.exception import AqualinkServiceException

from .local import AqualinkdError, LocalInfiniteLight

if TYPE_CHECKING:
    from iaqualink.systems.iaqua.system import IaquaSystem

_LOGGER = logging.getLogger(__name__)

COMMAND_GET_INFO = "get_icl_info"
COMMAND_ON_OFF = "onoff_iclzone"
COMMAND_SET_COLOR = "set_iclzone_color"
COMMAND_SET_CUSTOM_COLOR = "define_iclzone_customcolor"

STATUS_ON = "on"
STATUS_OFF = "off"
STATUS_ABSENT = "absent"

# Same colours as the pool light picker and the PyScript presets.
PRESETS: dict[str, tuple[int, int, int]] = {
    "Turquoise": (27, 205, 255),
    "White": (255, 255, 255),
    "Blue": (0, 100, 255),
    "Green": (0, 255, 100),
    "Purple": (148, 0, 211),
    "Pink": (255, 105, 180),
    "Red": (255, 0, 0),
    "Orange": (255, 165, 0),
    "Yellow": (255, 255, 0),
    "Cyan": (0, 255, 255),
    "Magenta": (255, 0, 255),
    "Warm White": (255, 244, 229),
    "Cool White": (240, 255, 255),
}
DEFAULT_RGB = PRESETS["Turquoise"]

TURN_ON_OFF_DELAY = 2.0
TURN_ON_ON_DELAY = 1.0
BRIGHTNESS_SETTLE_DELAY = 1.5
COMMAND_HOLD = 120.0
GET_INFO_TIMEOUT = 20.0
GET_INFO_INTERVAL = 60.0

# home_screen status values; anything but Online means the controller can't act.
SYSTEM_ONLINE = "Online"
SYSTEM_SERVICE = "Service"


class IclZone:
    """One iCL light zone; quacks like an iaqualink device for AqualinkEntity."""

    manufacturer = "Jandy"
    model = "Infinite WaterColors light"

    def __init__(self, manager: IclManager, data: dict[str, str]) -> None:
        """Initialize the zone."""
        self.manager = manager
        self.system = manager.system
        self.data = data
        self.is_on: bool | None = None
        self.reported_on: bool | None = None
        self.desired_on: bool | None = None
        self.brightness_percentage: int | None = None
        self.rgb: tuple[int, int, int] | None = None
        self._hold_until = 0.0
        # Set in Local mode when AqualinkD has an Infinite WaterColors button
        # with this zone's name; commands then go to AqualinkD first.
        self.local: LocalInfiniteLight | None = None
        self.apply_report(data)

    @property
    def zone_id(self) -> int:
        """Return the iCL zone number."""
        return int(self.data["zoneId"])

    @property
    def name(self) -> str:
        """Return the zone key, used in the unique ID."""
        return f"icl_zone_{self.zone_id}"

    @property
    def label(self) -> str:
        """Return the zone name set in the iAquaLink app."""
        return self.data.get("zoneName") or f"Light Zone {self.zone_id}"

    @property
    def reports_state(self) -> bool:
        """Return whether the controller reports this zone's on/off state."""
        return self.data.get("zoneStatus") in (STATUS_ON, STATUS_OFF)

    @property
    def settling(self) -> bool:
        """Return whether a recent command may not show in reports yet."""
        return time.monotonic() < self._hold_until

    def sync_local(self) -> None:
        """Take AqualinkD's on/off state; it reports reliably, unlike the cloud."""
        if self.local is None:
            return
        self.is_on = self.reported_on = self.local.is_on

    async def _try_local(self, action: str, command: Any) -> bool:
        """Run a command on AqualinkD; False means fall back to the cloud."""
        try:
            await command
        except AqualinkdError as err:
            _LOGGER.warning(
                "%s: AqualinkD %s failed, using the cloud: %s", self.label, action, err
            )
            return False
        return True

    def apply_report(self, data: dict[str, str]) -> None:
        """Take the reported state, unless a recent command is still settling."""
        self.data.update(data)
        status = data.get("zoneStatus")
        if status in (STATUS_ON, STATUS_OFF):
            self.reported_on = status == STATUS_ON
        if self.settling:
            return
        if status in (STATUS_ON, STATUS_OFF):
            self.is_on = status == STATUS_ON
        # While off the cloud repeats the last lit values: only use them to fill
        # gaps, so the colour/brightness kept for the next turn-on stays.
        if (dim := _int(data.get("dim_level"))) is not None and (
            self.is_on or self.brightness_percentage is None
        ):
            self.brightness_percentage = dim
        rgb = tuple(_int(data.get(k)) for k in ("red_val", "green_val", "blue_val"))
        if None not in rgb and (self.is_on or self.rgb is None):
            self.rgb = rgb  # type: ignore[assignment]

    def restore(
        self, is_on: bool | None, pct: int | None, rgb: tuple[int, int, int] | None
    ) -> None:
        """Seed the state HA had before a restart, for what isn't reported."""
        if self.is_on is None:
            self.is_on = is_on
        if self.desired_on is None:
            self.desired_on = is_on
        if self.brightness_percentage is None:
            self.brightness_percentage = pct
        if self.rgb is None:
            self.rgb = rgb

    def remember(self, rgb: tuple[int, int, int] | None, pct: int | None) -> None:
        """Set the colour and brightness the next turn-on uses, without sending."""
        if rgb is not None:
            self.rgb = tuple(rgb)  # type: ignore[assignment]
        if pct is not None:
            self.brightness_percentage = pct

    async def turn_on(
        self, rgb: tuple[int, int, int] | None = None, percentage: int | None = None
    ) -> None:
        """Light the zone with the app's sequence, then set the brightness."""
        rgb = tuple(rgb) if rgb is not None else self.rgb or DEFAULT_RGB  # type: ignore[assignment]
        if percentage is None:
            percentage = self.brightness_percentage
        self.desired_on = True
        if self.local is not None and await self._try_local(
            "turn on", self.local.turn_on(rgb, percentage)
        ):
            self.is_on = True
            self.rgb = rgb
            if percentage is not None:
                self.brightness_percentage = percentage
            return
        self._hold()
        try:
            await self._on_off(STATUS_OFF)
        except (AqualinkServiceException, TimeoutError, httpx.HTTPError) as err:
            _LOGGER.warning(
                "iCL zone %s: turning off first failed: %s", self.zone_id, err
            )
        await asyncio.sleep(TURN_ON_OFF_DELAY)
        await self._on_off(STATUS_ON)
        await asyncio.sleep(TURN_ON_ON_DELAY)
        red, green, blue = rgb
        await self._send(
            COMMAND_SET_CUSTOM_COLOR,
            red_val=red,
            green_val=green,
            blue_val=blue,
            white_val=0,
        )
        self.is_on = True
        self.rgb = (red, green, blue)
        if percentage is not None:
            await asyncio.sleep(BRIGHTNESS_SETTLE_DELAY)
            await self.set_brightness(percentage)
        self._hold()

    async def turn_off(self) -> None:
        """Turn the zone off."""
        self.desired_on = False
        if self.local is not None and await self._try_local(
            "turn off", self.local.turn_off()
        ):
            self.is_on = False
            return
        self._hold()
        await self._on_off(STATUS_OFF)
        self.is_on = False
        self._hold()

    async def set_brightness(self, percentage: int) -> None:
        """Set the dim level without changing the colour."""
        if self.local is not None and await self._try_local(
            "brightness", self.local.set_brightness(percentage)
        ):
            self.brightness_percentage = percentage
            return
        self._hold()
        await self._send(COMMAND_SET_COLOR, dim_level=percentage)
        self.brightness_percentage = percentage
        self._hold()

    async def _on_off(self, action: str) -> None:
        await self._send(COMMAND_ON_OFF, on_off_action=action)

    async def _send(self, command: str, **params: Any) -> None:
        await self.manager.send(
            command,
            {"zone_id": str(self.zone_id), **{k: str(v) for k, v in params.items()}},
        )

    def _hold(self) -> None:
        self._hold_until = time.monotonic() + COMMAND_HOLD


class IclManager:
    """Keeps the iCL zones of one iAqua system up to date."""

    def __init__(self, system: IaquaSystem) -> None:
        """Hook into the system's device-screen parsing."""
        self.system = system
        self.zones: dict[int, IclZone] = {}
        self.in_devices = False
        self.use_get_info = False
        self.system_status: str | None = None
        self._last_get_info = 0.0
        original_home = system._parse_home_response  # noqa: SLF001

        def parse_home(response: Any) -> None:
            try:
                self.system_status = response.json()["home_screen"][0]["status"]
            except ValueError, KeyError, IndexError, TypeError:
                self.system_status = None
            original_home(response)

        system._parse_home_response = parse_home  # type: ignore[method-assign]  # noqa: SLF001
        original = system._parse_devices_response  # noqa: SLF001

        def parse_devices(response: Any) -> None:
            original(response)
            try:
                data = response.json()
            except ValueError:
                return
            if isinstance(data, dict) and "icl_info_list" in data:
                self.in_devices = True
                self.update(data["icl_info_list"])

        system._parse_devices_response = parse_devices  # type: ignore[method-assign]  # noqa: SLF001

    async def async_discover(self) -> None:
        """Fall back to get_icl_info when get_devices doesn't list the zones."""
        present = self.system.devices.get("is_icl_present")
        if self.in_devices or not getattr(present, "is_on", False):
            return
        await self._get_info()
        self.use_get_info = bool(self.zones)

    async def async_poll(self) -> None:
        """Refresh the zones that get_devices doesn't report."""
        if self.use_get_info and (
            time.monotonic() - self._last_get_info >= GET_INFO_INTERVAL
        ):
            await self._get_info()

    async def async_read_zones(self) -> bool:
        """Read the zones now; return whether the cloud answered."""
        return await self._get_info()

    async def _get_info(self) -> bool:
        self._last_get_info = time.monotonic()
        try:
            async with asyncio.timeout(GET_INFO_TIMEOUT):
                await self.send(COMMAND_GET_INFO, {})
        except (AqualinkServiceException, TimeoutError, httpx.HTTPError) as err:
            _LOGGER.debug("get_icl_info failed: %s", err)
            return False
        return True

    def update(self, zones: Any) -> None:
        """Merge an ``icl_info_list`` into the known zones."""
        if not isinstance(zones, list):
            return
        for zone in zones:
            if not isinstance(zone, dict) or zone.get("zoneId") is None:
                continue
            zone_id = _int(zone["zoneId"])
            if zone_id is None:
                continue
            data = {k: "" if v is None else str(v) for k, v in zone.items()}
            if zone_id in self.zones:
                self.zones[zone_id].apply_report(data)
            else:
                self.zones[zone_id] = IclZone(self, data)

    async def send(self, command: str, params: dict[str, str]) -> dict[str, Any]:
        """Send an iCL command and merge any zone state in the reply."""
        response = await self.system._send_session_request(command, params)  # noqa: SLF001
        try:
            data = response.json()
        except ValueError:
            return {}
        if not isinstance(data, dict):
            return {}
        _LOGGER.debug("iCL %s reply: %s", command, data)
        self.update(data.get("icl_info_list"))
        return data


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except TypeError, ValueError:
        return None
