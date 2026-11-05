"""Pump readings the iAquaLink cloud doesn't list, worked out from its speed presets.

The cloud reports the pump speed presets (e.g. "Spa 2250") only as aux buttons,
so the spa pump's on/off and each pump's speed and RPM come from the preset
that is on. These use the same device names as Jandy Bridge's, so the entities
keep working on the cloud and stand in for the bridge while it is offline.
"""

import re
from typing import Any, override

from iaqualink.device import AqualinkDevice, AqualinkSensor, AqualinkSwitch

from homeassistant.exceptions import HomeAssistantError

PRESET = re.compile(r"(\w+)\s+(\d{3,4})")
# First word of a preset label -> pump (1 = filtration, 2 = spa pump).
PRESET_PUMPS = {"filter": 1, "pool": 1, "spa": 2}
PUMP_SWITCHES = {1: "pool_pump", 2: "pump_2"}
PUMP_LABELS = {1: "Filtration", 2: "SPA Pump"}
RPM_SENSORS = {1: "filter_pump_rpm", 2: "pump_2_rpm"}


def presets(system: Any, pump: int) -> list[tuple[int, Any]]:
    """Return (rpm, aux switch) for each of the pump's speed presets, slowest first."""
    found = []
    for dev in getattr(system, "devices", {}).values():
        if not dev.name.startswith("aux_") or not isinstance(dev, AqualinkSwitch):
            continue
        match = PRESET.fullmatch(dev.label.strip())
        if match and PRESET_PUMPS.get(match[1].casefold()) == pump:
            found.append((int(match[2]), dev))
    return sorted(found, key=lambda item: item[0])


class CloudPumpDevice(AqualinkDevice):
    """Base for a reading of one pump, derived from the cloud's presets."""

    def __init__(self, system: Any, pump: int, name: str, label: str) -> None:
        """Remember the pump."""
        super().__init__(system, {"name": name, "label": label})
        self.pump = pump

    @property
    @override
    def name(self) -> str:
        return self.data["name"]

    @property
    @override
    def label(self) -> str:
        return self.data["label"]

    @property
    @override
    def manufacturer(self) -> str:
        return "Jandy"

    @property
    @override
    def model(self) -> str:
        return "Pump"

    @property
    def active(self) -> tuple[int, Any] | None:
        """Return the preset that is on, if any."""
        return next(
            (item for item in presets(self.system, self.pump) if item[1].is_on), None
        )

    @property
    def pump_on(self) -> bool:
        """Return whether the pump runs."""
        switch = getattr(self.system, "devices", {}).get(PUMP_SWITCHES[self.pump])
        if self.pump == 1 and switch is not None:
            return bool(switch.is_on)
        return self.active is not None


class CloudPumpSwitch(CloudPumpDevice, AqualinkSwitch):
    """The spa pump: on while one of its presets is on."""

    def __init__(self, system: Any, pump: int) -> None:
        """Name it like Jandy Bridge's pump switch."""
        super().__init__(system, pump, PUMP_SWITCHES[pump], PUMP_LABELS[pump])
        self._last: int | None = None

    @property
    @override
    def state(self) -> str:
        return "on" if self.is_on else "off"

    @property
    @override
    def is_on(self) -> bool:
        if (active := self.active) is not None:
            self._last = active[0]
        return self.pump_on

    @override
    async def turn_on(self) -> None:
        if self.is_on:
            return
        items = presets(self.system, self.pump)
        if not items:
            raise HomeAssistantError(f"{self.label} has no speed presets on the cloud")
        chosen = next((d for rpm, d in items if rpm == self._last), None)
        await (chosen or items[len(items) // 2][1]).turn_on()

    @override
    async def turn_off(self) -> None:
        for _rpm, dev in presets(self.system, self.pump):
            if dev.is_on:
                await dev.turn_off()


class CloudPumpRpm(CloudPumpDevice, AqualinkSensor):
    """The pump's RPM: its preset's, 0 when off, unknown at a speed without one."""

    ha_unit = "rpm"

    def __init__(self, system: Any, pump: int) -> None:
        """Name it like Jandy Bridge's RPM sensor."""
        super().__init__(system, pump, RPM_SENSORS[pump], f"{PUMP_LABELS[pump]} RPM")

    @property
    @override
    def state(self) -> str:
        if (active := self.active) is not None:
            return str(active[0])
        return "" if self.pump_on else "0"


class CloudPumpSpeed(CloudPumpDevice, AqualinkSensor):
    """The pump's speed: the preset that is on, e.g. "Filter 2250", or Off."""

    def __init__(self, system: Any, pump: int) -> None:
        """Name it like Jandy Bridge's speed sensor."""
        super().__init__(
            system, pump, f"bridge_pump{pump}_speed", f"{PUMP_LABELS[pump]} speed"
        )

    @property
    @override
    def state(self) -> str:
        if (active := self.active) is not None:
            return str(active[1].label)
        return "" if self.pump_on else "Off"


class CloudPumpRpmSetpoint(CloudPumpDevice):
    """The pump's RPM setpoint; over the cloud only its presets can be picked."""

    def __init__(self, system: Any, pump: int) -> None:
        """Name it like Jandy Bridge's RPM setpoint."""
        super().__init__(
            system,
            pump,
            f"{PUMP_SWITCHES[pump]}_rpm_set",
            f"{PUMP_LABELS[pump]} RPM setpoint",
        )
        self.data.update({"min": 600, "max": 3450, "step": 10, "unit": "rpm"})

    @property
    @override
    def state(self) -> str:
        value = self.value
        return "" if value is None else str(int(value))

    @property
    def value(self) -> float | None:
        """Return the RPM of the preset that is on."""
        active = self.active
        return float(active[0]) if active is not None else None

    async def set_value(self, value: float) -> None:
        """Turn on the preset with this RPM, then the pump's other presets off."""
        items = presets(self.system, self.pump)
        chosen = next((d for rpm, d in items if rpm == int(value)), None)
        if chosen is None:
            speeds = ", ".join(str(rpm) for rpm, _d in items) or "none"
            raise HomeAssistantError(
                f"Over the iAquaLink cloud {self.label.removesuffix(' RPM setpoint')} "
                f"runs only at its preset speeds ({speeds} RPM)"
            )
        await chosen.turn_on()
        for _rpm, dev in items:
            if dev is not chosen and dev.is_on:
                await dev.turn_off()


class CloudSaltOutput(AqualinkSensor):
    """The salt cell's output %, from the home screen's swc_info."""

    ha_unit = "%"

    def __init__(self, system: Any, raw: dict[str, Any]) -> None:
        """Read from the last raw home screen."""
        super().__init__(system, {"name": "swg_percent", "label": "SWG Percent"})
        self._raw = raw

    @property
    @override
    def name(self) -> str:
        return "swg_percent"

    @property
    @override
    def label(self) -> str:
        return "SWG Percent"

    @property
    @override
    def manufacturer(self) -> str:
        return "Jandy"

    @property
    @override
    def model(self) -> str:
        return "AquaPure"

    @property
    @override
    def state(self) -> str:
        info = swc_info(self._raw)
        value = info.get("swcPoolValue") if info else None
        return "" if value is None else str(value)


def swc_info(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Return the home screen's swc_info, if the system has a salt cell."""
    items = (raw.get("home_screen") or {}).get("home_screen") or []
    for item in items:
        if isinstance(item, dict) and isinstance(info := item.get("swc_info"), dict):
            return info if info.get("isswcPresent") else None
    return None


def cloud_extras(system: Any, raw: dict[str, Any] | None) -> dict[str, Any]:
    """Return the derived pump (and salt) devices for a cloud system."""
    extras: dict[str, Any] = {}
    for pump in (1, 2):
        if not presets(system, pump):
            continue
        devices: list[Any] = [
            CloudPumpRpm(system, pump),
            CloudPumpSpeed(system, pump),
            CloudPumpRpmSetpoint(system, pump),
        ]
        if pump == 2:
            devices.append(CloudPumpSwitch(system, pump))
        extras.update({dev.name: dev for dev in devices})
    if raw is not None and swc_info(raw) is not None:
        extras["swg_percent"] = CloudSaltOutput(system, raw)
    return {k: v for k, v in extras.items() if k not in system.devices}
