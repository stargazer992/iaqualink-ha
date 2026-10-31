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

# Suffix of a sensor id -> Home Assistant unit.
UNITS = {
    "/RPM": "rpm",
    "/GPM": "gal/min",
    "/Watts": "W",
    "/Speed": "%",
    "/Efficiency": "%",
    "SWG/Percent": "%",
    "SWG/PPM": "ppm",
    "CHEM/ORP": "mV",
}
# AqualinkD's aux row fields for a variable-speed pump -> sensor suffix.
PUMP_FIELDS = {
    "Pump_RPM": "RPM",
    "Pump_GPM": "GPM",
    "Pump_Watts": "Watts",
    "Pump_Speed": "Speed",
    "Pump_Status": "Status",
}


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

    async def command(self, path: str, value: float | None = None) -> None:
        """Send e.g. Aux_1/set, Pool_Heater/setpoint/set or SWG/Percent/set."""
        if value is None:
            await self._get(path)
        else:
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
        self._parse_status(status)

    def _add(self, cls: type[LocalDevice], name: str, row: dict[str, Any]) -> None:
        if name in self.devices:
            self.devices[name].data.update(row)
        else:
            self.devices[name] = cls(self, {**row, "cloud_name": name})

    def _number(
        self,
        name: str,
        label: str,
        path: str,
        value: Any,
        limits: tuple[float, float, float],
        unit: str | None = None,
    ) -> None:
        low, high, step = limits
        row = {
            "id": path,
            "name": label,
            "value": value,
            "min": low,
            "max": high,
            "step": step,
            "unit": unit,
        }
        self._add(LocalNumber, name, row)

    def _setpoint_limits(self, f_range: tuple[int, int]) -> tuple[float, float, float]:
        low, high = f_range
        if self.temp_unit == "C":
            return round((low - 32) / 1.8), round((high - 32) / 1.8), 1
        return low, high, 1

    def _parse(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            aqd_id = str(row.get("id", ""))
            name = cloud_name(aqd_id)
            label = str(row.get("name") or aqd_id)
            kind = row.get("type")
            if kind == "setpoint_thermo" and aqd_id in SETPOINT_NAMES:
                self._add(LocalSwitch, name, row)
                self._add(LocalThermostat, SETPOINT_NAMES[aqd_id], row)
            elif kind == "setpoint_freeze":
                self._add(LocalBinarySensor, name, row)
                self._number(
                    "freeze_protect_set_point",
                    "Freeze protect setpoint",
                    f"{aqd_id}/setpoint/set",
                    row.get("spvalue"),
                    self._setpoint_limits((34, 42)),
                    f"°{self.temp_unit}",
                )
            elif kind == "setpoint_chiller":
                self._add(LocalSwitch, name, row)
                self._number(
                    "chiller_set_point",
                    "Chiller setpoint",
                    f"{aqd_id}/setpoint/set",
                    row.get("spvalue"),
                    self._setpoint_limits((34, 104)),
                    f"°{self.temp_unit}",
                )
            elif kind == "setpoint_swg":
                self._number(
                    "swg_set_point",
                    "SWG setpoint",
                    "SWG/Percent/set",
                    row.get("spvalue"),
                    (0, 100, 1),
                    "%",
                )
            elif kind == "switch":
                self._add(LocalSwitch, name, row)
                self._parse_extras(aqd_id, name, label, row)
            elif kind in ("temperature", "value"):
                self._add(LocalSensor, name, row)

    def _parse_extras(
        self, aqd_id: str, name: str, label: str, row: dict[str, Any]
    ) -> None:
        """Add pump readings, light programs and dimmers carried by a button."""
        extended = row.get("type_ext")
        if extended == "switch_vsp":
            for field, suffix in PUMP_FIELDS.items():
                if field in row:
                    sensor_id = f"{aqd_id}/{suffix}"
                    self._add(
                        LocalSensor,
                        cloud_name(sensor_id),
                        {
                            "id": sensor_id,
                            "name": f"{label} {suffix}",
                            "value": row[field],
                        },
                    )
            pump_type = row.get("Pump_Type")
            if pump_type == "vsPump":
                self._number(
                    f"{name}_rpm_set",
                    f"{label} RPM setpoint",
                    f"{aqd_id}/RPM/set",
                    row.get("Pump_RPM"),
                    (600, 3450, 10),
                    "rpm",
                )
            elif pump_type == "vfPump":
                self._number(
                    f"{name}_gpm_set",
                    f"{label} GPM setpoint",
                    f"{aqd_id}/GPM/set",
                    row.get("Pump_GPM"),
                    (15, 130, 1),
                    "gal/min",
                )
        elif extended == "switch_program" and row.get("light_programs"):
            self._add(
                LocalSelect,
                f"{name}_program",
                {
                    "id": f"{aqd_id}/program/set",
                    "name": f"{label} program",
                    "options": list(row["light_programs"]),
                    "current": row.get("Program_Name"),
                },
            )
        elif extended == "light_dimmer":
            self._number(
                f"{name}_brightness",
                f"{label} brightness",
                f"{aqd_id}/brightness/set",
                row.get("Light_Program"),
                (0, 100, 25),
                "%",
            )

    def _parse_status(self, status: dict[str, Any]) -> None:
        """Add the panel-wide readings from /api/status."""
        texts = {
            "display_message": ("Display message", status.get("panel_message")),
            "swg_status": ("SWG status", status.get("swg_fullstatus")),
            "swg_boost_message": ("SWG boost", status.get("swg_boost_msg")),
        }
        for name, (label, value) in texts.items():
            if value is not None:
                self._add(
                    LocalSensor, name, {"id": name, "name": label, "value": value}
                )
        if status.get("battery") in ("ok", "low"):
            self._add(
                LocalBinarySensor,
                "battery_low",
                {
                    "id": "Battery",
                    "name": "Panel battery",
                    "state": "on" if status["battery"] == "low" else "off",
                },
            )
        if "panel_clock" not in self.devices:
            self._add(
                LocalButton,
                "panel_clock",
                {"id": "set_date_time", "name": "Sync panel clock"},
            )


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
    """A temperature, value or text reading."""

    @property
    def ha_unit(self) -> str | None:
        """Return the Home Assistant unit, if AqualinkD's id implies one."""
        for suffix, unit in UNITS.items():
            if self.aqd_id.endswith(suffix):
                return unit
        return None

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


class LocalNumber(LocalDevice):
    """A settable value: SWG %, freeze/chiller setpoint, pump speed, dimmer."""

    @property
    @override
    def state(self) -> str:
        return str(self.data.get("value", ""))

    @property
    def value(self) -> float | None:
        """Return the current value."""
        try:
            return float(self.data["value"])
        except KeyError, TypeError, ValueError:
            return None

    async def set_value(self, value: float) -> None:
        """Send the new value."""
        await self.system.client.command(self.aqd_id, int(value))
        self.data["value"] = str(int(value))


class LocalSelect(LocalDevice):
    """A colour light's show/program."""

    @property
    @override
    def state(self) -> str:
        return str(self.data.get("current") or "")

    @property
    def options(self) -> list[str]:
        """Return the program names, "Off" first."""
        return [str(option) for option in self.data.get("options", [])]

    async def select(self, option: str) -> None:
        """Send the program's number; 0 is off."""
        await self.system.client.command(self.aqd_id, self.options.index(option))
        self.data["current"] = option


class LocalButton(LocalDevice):
    """A one-shot action on AqualinkD."""

    async def press(self) -> None:
        """Send the action."""
        await self.system.client.command(self.aqd_id)
