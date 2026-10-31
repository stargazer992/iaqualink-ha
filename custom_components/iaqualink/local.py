"""Local connection to an AqualinkD daemon (RS-485) instead of the iAquaLink cloud.

The devices mimic the iaqualink library's classes and use the cloud's device
names, so entities keep their unique IDs when the connection is switched.
"""

from __future__ import annotations

import re
from typing import Any, override

import httpx
from iaqualink.device import (
    AqualinkBinarySensor,
    AqualinkDevice,
    AqualinkSensor,
    AqualinkSwitch,
    AqualinkThermostat,
)
from iaqualink.exception import AqualinkServiceException
from iaqualink.system import AqualinkSystem

STATUS_SERVICE_TEXT = "Service Mode"
OFFLINE_PREFIXES = ("ERROR", "Not Connected", "Connecting")

CLOUD_NAMES = {
    "Filter_Pump": "pool_pump",
    "Spa": "spa_pump",
    "Pool_Heater": "pool_heater",
    "Spa_Heater": "spa_heater",
    "Freeze_Protect": "freeze_protection",
    "Temperature/Air": "air_temp",
    "Temperature/Pool": "pool_temp",
    "Temperature/Spa": "spa_temp",
    "SWG/Percent": "swg_percent",
    "SWG/PPM": "pool_salinity",
    "CHEM/pH": "ph",
    "CHEM/ORP": "orp",
}
SETPOINT_NAMES = {"Pool_Heater": "pool_set_point", "Spa_Heater": "spa_set_point"}


def cloud_name(aqd_id: str) -> str:
    """Return the cloud's name for an AqualinkD device id."""
    if aqd_id in CLOUD_NAMES:
        return CLOUD_NAMES[aqd_id]
    return re.sub(r"[^a-z0-9]+", "_", aqd_id.lower()).strip("_")


class AqualinkdError(AqualinkServiceException):
    """AqualinkD couldn't be reached or refused a request."""


class AqualinkdClient:
    """Talks to AqualinkD's HTTP API."""

    def __init__(self, base_url: str, httpx_client: httpx.AsyncClient) -> None:
        """Remember where AqualinkD runs."""
        self.base_url = base_url.rstrip("/")
        self._http = httpx_client

    async def _get(self, path: str, **params: Any) -> httpx.Response:
        try:
            response = await self._http.get(
                f"{self.base_url}/api/{path}", params=params or None, timeout=10
            )
        except httpx.HTTPError as err:
            raise AqualinkdError(f"AqualinkD unreachable: {err}") from err
        if response.status_code != 200:
            raise AqualinkdError(f"AqualinkD {path}: HTTP {response.status_code}")
        return response

    async def devices(self) -> dict[str, Any]:
        """Return /api/devices."""
        return (await self._get("devices")).json()

    async def status(self) -> dict[str, Any]:
        """Return /api/status."""
        return (await self._get("status")).json()

    async def command(self, path: str, value: float) -> None:
        """Send e.g. Aux_1/set, Pool_Heater/setpoint/set or SWG/Percent/set."""
        await self._get(path, value=value)


class LocalSystem(AqualinkSystem):
    """A pool system reached through AqualinkD."""

    NAME = "aqualinkd"

    def __init__(self, client: AqualinkdClient, serial: str, name: str) -> None:
        """Use the cloud system's serial so unique IDs stay the same."""
        super().__init__(None, {"serial_number": serial, "name": name})  # type: ignore[arg-type]
        self.client = client
        self.devices: dict[str, AqualinkDevice] = {}
        self.online: bool | None = None
        self.temp_unit = "F"
        self.status_text: str | None = None
        self.last_refresh = 0

    @override
    async def get_devices(self) -> dict[str, AqualinkDevice]:
        if not self.devices:
            await self.update()
        return self.devices

    @override
    async def update(self) -> None:
        try:
            devices = await self.client.devices()
            status = await self.client.status()
        except AqualinkdError:
            self.online = False
            raise
        self.status_text = str(status.get("status") or "")
        self.online = not self.status_text.startswith(OFFLINE_PREFIXES)
        if devices.get("temp_units") in ("F", "C"):
            self.temp_unit = devices["temp_units"]
        self._parse(devices.get("devices") or [])

    def _add(self, cls: type[LocalDevice], name: str, row: dict[str, Any]) -> None:
        if name in self.devices:
            self.devices[name].data.update(row)
        else:
            self.devices[name] = cls(self, {**row, "cloud_name": name})

    def _parse(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            aqd_id = str(row.get("id", ""))
            kind = row.get("type")
            if kind == "setpoint_thermo" and aqd_id in SETPOINT_NAMES:
                self._add(LocalSwitch, cloud_name(aqd_id), row)
                self._add(LocalThermostat, SETPOINT_NAMES[aqd_id], row)
            elif kind == "setpoint_freeze":
                self._add(LocalBinarySensor, cloud_name(aqd_id), row)
            elif kind == "switch":
                self._add(LocalSwitch, cloud_name(aqd_id), row)
            elif kind in ("temperature", "value"):
                self._add(LocalSensor, cloud_name(aqd_id), row)


class LocalDevice(AqualinkDevice):
    """A device row from AqualinkD's /api/devices."""

    system: LocalSystem

    @property
    def aqd_id(self) -> str:
        """Return AqualinkD's id, used in its URLs."""
        return str(self.data["id"])

    @property
    @override
    def name(self) -> str:
        return str(self.data["cloud_name"])

    @property
    @override
    def label(self) -> str:
        return str(self.data.get("name") or self.aqd_id)

    @property
    @override
    def state(self) -> str:
        return str(self.data.get("state", ""))

    @property
    @override
    def manufacturer(self) -> str:
        return "Jandy"

    @property
    @override
    def model(self) -> str:
        return "AqualinkD"


class LocalSensor(LocalDevice, AqualinkSensor):
    """A temperature or value reading."""

    @property
    @override
    def state(self) -> str:
        value = str(self.data.get("value", "")).strip()
        return "" if value in ("", "-999", "-999.0") else value


class LocalBinarySensor(LocalDevice, AqualinkBinarySensor):
    """An on/off reading (freeze protection)."""

    @property
    @override
    def is_on(self) -> bool:
        return self.data.get("state") == "on"


class LocalSwitch(LocalDevice, AqualinkSwitch):
    """A button: pump, spa, heater or aux."""

    @property
    @override
    def is_on(self) -> bool:
        return self.data.get("state") == "on"

    async def _set(self, on: bool) -> None:
        await self.system.client.command(f"{self.aqd_id}/set", 1 if on else 0)
        self.data["state"] = "on" if on else "off"

    @override
    async def turn_on(self) -> None:
        if not self.is_on:
            await self._set(True)

    @override
    async def turn_off(self) -> None:
        if self.is_on:
            await self._set(False)


class LocalThermostat(LocalSwitch, AqualinkThermostat):
    """A heater setpoint; on/off is the heater button."""

    @property
    @override
    def state(self) -> str:
        return str(self.data.get("spvalue", ""))

    @property
    @override
    def unit(self) -> str:
        return self.system.temp_unit

    @property
    @override
    def current_temperature(self) -> str:
        return str(self.data.get("value", ""))

    @property
    @override
    def target_temperature(self) -> str:
        return self.state

    @property
    @override
    def min_temperature(self) -> int:
        return 34 if self.unit == "F" else 1

    @property
    @override
    def max_temperature(self) -> int:
        return 104 if self.unit == "F" else 40

    @override
    async def set_temperature(self, temperature: int) -> None:
        await self.system.client.command(
            f"{self.aqd_id}/setpoint/set", int(temperature)
        )
        self.data["spvalue"] = str(int(temperature))
