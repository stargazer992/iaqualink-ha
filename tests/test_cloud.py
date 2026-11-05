"""Tests for the pump readings worked out from the cloud's speed presets."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from iaqualink.device import AqualinkSwitch
import pytest

from custom_components.iaqualink.cloud import (
    CloudPumpRpmSetpoint,
    cloud_extras,
)
from homeassistant.exceptions import HomeAssistantError


class Aux(AqualinkSwitch):
    """A cloud aux button."""

    def __init__(self, system, name: str, label: str, on: bool = False) -> None:
        super().__init__(system, {"name": name, "label": label})
        self.on = on
        self.turn_on = AsyncMock(side_effect=lambda: setattr(self, "on", True))
        self.turn_off = AsyncMock(side_effect=lambda: setattr(self, "on", False))

    @property
    def name(self) -> str:
        return self.data["name"]

    @property
    def label(self) -> str:
        return self.data["label"]

    @property
    def is_on(self) -> bool:
        return self.on


def _system(filter_on: bool = True) -> SimpleNamespace:
    system = SimpleNamespace(serial="SN1", devices={})
    for name, label, on in (
        ("aux_B3", "Filter 1250", False),
        ("aux_B6", "Filter 2250", True),
        ("aux_C3", "Pool 3450", False),
        ("aux_B7", "Spa 1750", False),
        ("aux_D4", "Spa 2250", False),
        ("aux_B8", "Spa 3450", False),
        ("aux_2", "Jets", False),
    ):
        system.devices[name] = Aux(system, name, label, on)
    system.devices["pool_pump"] = Aux(system, "pool_pump", "Pool Pump", filter_on)
    return system


RAW = {
    "home_screen": {
        "home_screen": [
            {"status": "Online"},
            {"swc_info": {"isswcPresent": True, "swcPoolValue": 50}},
        ]
    }
}


def test_readings_follow_presets() -> None:
    """Speed, RPM and the spa pump switch come from the preset that is on."""
    system = _system()
    extras = cloud_extras(system, RAW)
    assert set(extras) == {
        "filter_pump_rpm",
        "bridge_pump1_speed",
        "pool_pump_rpm_set",
        "pump_2_rpm",
        "bridge_pump2_speed",
        "pump_2_rpm_set",
        "pump_2",
        "swg_percent",
    }
    assert extras["filter_pump_rpm"].state == "2250"
    assert extras["bridge_pump1_speed"].state == "Filter 2250"
    assert extras["pool_pump_rpm_set"].value == 2250
    assert extras["pump_2"].is_on is False
    assert extras["pump_2_rpm"].state == "0"
    assert extras["bridge_pump2_speed"].state == "Off"
    assert extras["swg_percent"].state == "50"

    system.devices["aux_D4"].on = True
    assert extras["pump_2"].is_on is True
    assert extras["bridge_pump2_speed"].state == "Spa 2250"

    # Filtration on at a speed without a preset: unknown, not 0.
    system.devices["aux_B6"].on = False
    assert extras["filter_pump_rpm"].state == ""
    system.devices["pool_pump"].on = False
    assert extras["filter_pump_rpm"].state == "0"
    assert extras["bridge_pump1_speed"].state == "Off"


async def test_spa_pump_switch() -> None:
    """On picks the last preset (middle at first); off turns its presets off."""
    system = _system()
    pump = cloud_extras(system, None)["pump_2"]
    await pump.turn_on()
    system.devices["aux_D4"].turn_on.assert_awaited_once()
    await pump.turn_off()
    assert not system.devices["aux_D4"].on
    system.devices["aux_B8"].on = True
    assert pump.is_on
    await pump.turn_off()
    await pump.turn_on()
    assert system.devices["aux_B8"].on


async def test_rpm_setpoint_picks_presets_only() -> None:
    """A preset's RPM switches to it; other speeds can't go over the cloud."""
    system = _system()
    number = cloud_extras(system, None)["pool_pump_rpm_set"]
    assert isinstance(number, CloudPumpRpmSetpoint)
    await number.set_value(3450)
    assert system.devices["aux_C3"].on
    assert not system.devices["aux_B6"].on
    with pytest.raises(HomeAssistantError, match="1250, 2250, 3450"):
        await number.set_value(1650)


def test_no_presets_no_extras() -> None:
    """Without speed presets or a salt cell nothing is added."""
    system = SimpleNamespace(serial="SN1", devices={})
    assert cloud_extras(system, {"home_screen": {"home_screen": []}}) == {}


def test_cloud_devices_win() -> None:
    """A device the cloud does list is left alone."""
    system = _system()
    system.devices["pump_2"] = Aux(system, "pump_2", "Spa Pump")
    assert "pump_2" not in cloud_extras(system, None)
