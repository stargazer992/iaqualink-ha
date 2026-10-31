"""iCL (Jandy Infinite WaterColors) light zones on iAqua systems.

iaqualink 0.7.0 ignores the ``icl_info_list`` in the get_devices reply, so the
zones are read and controlled here, over the library's session request. The
zones are only read from get_devices and command replies: get_icl_info times
out on real controllers.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from iaqualink.systems.iaqua.system import IaquaSystem

_LOGGER = logging.getLogger(__name__)

COMMAND_ON_OFF = "onoff_iclzone"
COMMAND_SET_COLOR = "set_iclzone_color"
COMMAND_SET_CUSTOM_COLOR = "define_iclzone_customcolor"

STATUS_ON = "on"
STATUS_ABSENT = "absent"

CUSTOM_COLOR_ID = 16
EFFECTS: dict[str, int] = {
    "Alpine White": 1,
    "Sky Blue": 2,
    "Cobalt Blue": 3,
    "Caribbean Blue": 4,
    "Spring Green": 5,
    "Emerald Green": 6,
    "Emerald Rose": 7,
    "Ruby Red": 8,
    "Magenta": 9,
    "Violet": 10,
    "Slow Color Splash": 11,
    "Fast Color Splash": 12,
    "America The Beautiful": 13,
    "Fat Tuesday": 14,
    "Disco Tech": 15,
}
EFFECT_NAMES = {v: k for k, v in EFFECTS.items()}

# The iAquaLink app turns a zone off and on again before a custom colour,
# otherwise the controller can keep showing the previous colour.
CUSTOM_COLOR_OFF_DELAY = 2.0
CUSTOM_COLOR_ON_DELAY = 1.0


class IclZone:
    """One iCL light zone; quacks like an iaqualink device for AqualinkEntity."""

    manufacturer = "Jandy"
    model = "Infinite WaterColors light"

    def __init__(self, manager: IclManager, data: dict[str, str]) -> None:
        """Initialize the zone."""
        self.manager = manager
        self.system = manager.system
        self.data = data

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
    def is_on(self) -> bool:
        """Return whether the zone is on."""
        return self.data.get("zoneStatus") == STATUS_ON

    @property
    def brightness_percentage(self) -> int | None:
        """Return the dim level (0-100)."""
        try:
            return int(self.data["dim_level"])
        except KeyError, ValueError:
            return None

    @property
    def color_id(self) -> int | None:
        """Return the colour/show number (1-15 named, 16 custom)."""
        try:
            return int(self.data["zoneColor"])
        except KeyError, ValueError:
            return None

    @property
    def effect(self) -> str | None:
        """Return the named colour/show, if one is active."""
        color_id = self.color_id
        return EFFECT_NAMES.get(color_id) if color_id is not None else None

    @property
    def rgb(self) -> tuple[int, int, int] | None:
        """Return the custom colour, if the API reported one."""
        try:
            return (
                int(self.data["red_val"]),
                int(self.data["green_val"]),
                int(self.data["blue_val"]),
            )
        except KeyError, ValueError:
            return None

    async def turn_on(self) -> None:
        """Turn the zone on."""
        await self.manager.send(COMMAND_ON_OFF, self._params(on_off_action="on"))

    async def turn_off(self) -> None:
        """Turn the zone off."""
        await self.manager.send(COMMAND_ON_OFF, self._params(on_off_action="off"))

    async def set_brightness(self, percentage: int) -> None:
        """Set the dim level without changing the colour."""
        await self.manager.send(COMMAND_SET_COLOR, self._params(dim_level=percentage))

    async def set_effect(self, effect: str, percentage: int | None = None) -> None:
        """Select one of the 15 Jandy colours/shows."""
        if percentage is None:
            percentage = self.brightness_percentage or 100
        await self.manager.send(
            COMMAND_SET_COLOR,
            self._params(color_id=EFFECTS[effect], dim_level=percentage),
        )

    async def set_rgb(self, red: int, green: int, blue: int) -> None:
        """Show a custom colour, following the iAquaLink app's sequence."""
        await self.turn_off()
        await asyncio.sleep(CUSTOM_COLOR_OFF_DELAY)
        await self.turn_on()
        await asyncio.sleep(CUSTOM_COLOR_ON_DELAY)
        response = await self.manager.send(
            COMMAND_SET_CUSTOM_COLOR,
            self._params(red_val=red, green_val=green, blue_val=blue, white_val=0),
        )
        self.data.update(
            {
                "zoneStatus": STATUS_ON,
                "zoneColor": str(CUSTOM_COLOR_ID),
                "red_val": str(response.get("red_val", red)),
                "green_val": str(response.get("green_val", green)),
                "blue_val": str(response.get("blue_val", blue)),
            }
        )

    def _params(self, **params: Any) -> dict[str, str]:
        return {"zone_id": str(self.zone_id), **{k: str(v) for k, v in params.items()}}


class IclManager:
    """Keeps the iCL zones of one iAqua system up to date."""

    def __init__(self, system: IaquaSystem) -> None:
        """Hook into the system's device-screen parsing."""
        self.system = system
        self.zones: dict[int, IclZone] = {}
        original = system._parse_devices_response  # noqa: SLF001

        def parse_devices(response: Any) -> None:
            original(response)
            try:
                data = response.json()
            except ValueError:
                return
            if isinstance(data, dict) and "icl_info_list" in data:
                self.update(data["icl_info_list"])

        system._parse_devices_response = parse_devices  # type: ignore[method-assign]  # noqa: SLF001

    def update(self, zones: Any) -> None:
        """Merge an ``icl_info_list`` into the known zones."""
        if not isinstance(zones, list):
            return
        for zone in zones:
            if not isinstance(zone, dict) or zone.get("zoneId") is None:
                continue
            try:
                zone_id = int(zone["zoneId"])
            except TypeError, ValueError:
                continue
            if zone.get("zoneStatus") == STATUS_ABSENT:
                self.zones.pop(zone_id, None)
                continue
            data = {k: "" if v is None else str(v) for k, v in zone.items()}
            if zone_id in self.zones:
                self.zones[zone_id].data.update(data)
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
