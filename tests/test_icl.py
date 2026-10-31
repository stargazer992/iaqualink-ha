"""Tests for iCL light zones and the fork's review fixes."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from iaqualink.client import AqualinkClient
from iaqualink.exception import AqualinkServiceException
from iaqualink.systems.iaqua.device import (
    IaquaBinarySensor,
    IaquaColorLightJC,
    IaquaDimmableLight,
)
from iaqualink.systems.iaqua.system import IaquaSystem
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.iaqualink import icl
from custom_components.iaqualink.coordinator import AqualinkDataUpdateCoordinator
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_EFFECT_LIST,
    ATTR_RGB_COLOR,
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant

from .conftest import get_aqualink_device, get_aqualink_system, setup_entry

POOL = "light.pool"
SPA = "light.spa"


def _response(data: dict[str, Any]) -> MagicMock:
    response = MagicMock()
    response.json.return_value = data
    return response


class FakeIclCloud:
    """Answers iCL commands like the iAquaLink cloud does."""

    def __init__(self) -> None:
        self.zones = {
            1: {
                "zoneId": 1,
                "zoneName": "Pool",
                "zoneStatus": "off",
                "zoneColor": 0,
                "dim_level": 50,
            },
            2: {
                "zoneId": 2,
                "zoneName": "Spa",
                "zoneStatus": "off",
                "zoneColor": 0,
                "dim_level": 100,
            },
            3: {"zoneId": 3, "zoneStatus": "absent"},
        }
        self.calls: list[tuple[str, dict[str, str]]] = []

    def info(self) -> dict[str, Any]:
        return {"icl_info_list": list(self.zones.values())}

    async def send(self, command: str, params: dict[str, str]) -> MagicMock:
        self.calls.append((command, params))
        zone = self.zones.get(int(params.get("zone_id", 0)))
        if command == icl.COMMAND_ON_OFF:
            zone["zoneStatus"] = params["on_off_action"]
        elif command == icl.COMMAND_SET_COLOR:
            if "color_id" in params:
                zone["zoneColor"] = int(params["color_id"])
            zone["dim_level"] = int(params["dim_level"])
        elif command == icl.COMMAND_SET_CUSTOM_COLOR:
            zone["zoneColor"] = icl.CUSTOM_COLOR_ID
            return _response(
                {k: params[k] for k in ("red_val", "green_val", "blue_val")}
            )
        return _response(self.info())


async def _setup_icl(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    client: AqualinkClient,
) -> tuple[IaquaSystem, FakeIclCloud]:
    cloud = FakeIclCloud()
    system = get_aqualink_system(client, cls=IaquaSystem)
    system.online = True
    present = get_aqualink_device(
        system, name="is_icl_present", cls=IaquaBinarySensor, data={"state": "1"}
    )
    system.devices = {present.name: present}
    system._parse_devices_response = MagicMock()

    async def update() -> None:
        system._parse_devices_response(_response(cloud.info()))

    system.update = AsyncMock(side_effect=update)
    system.get_devices = AsyncMock(return_value={present.name: present})
    system._send_session_request = AsyncMock(side_effect=cloud.send)
    await setup_entry(hass, config_entry, system)
    return system, cloud


async def test_icl_zones_discovered(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: AqualinkClient
) -> None:
    """Zones come from the get_devices reply; absent zones are skipped."""
    _, cloud = await _setup_icl(hass, config_entry, client)

    assert sorted(hass.states.async_entity_ids(LIGHT_DOMAIN)) == [POOL, SPA]
    state = hass.states.get(POOL)
    assert state.state == STATE_OFF
    assert "Off" not in state.attributes[ATTR_EFFECT_LIST]
    assert len(state.attributes[ATTR_EFFECT_LIST]) == 15
    assert cloud.calls == []


async def test_icl_turn_on_off(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: AqualinkClient
) -> None:
    """The light switch sends onoff_iclzone for the right zone."""
    _, cloud = await _setup_icl(hass, config_entry, client)

    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: SPA}, blocking=True
    )
    assert cloud.calls == [
        (icl.COMMAND_ON_OFF, {"zone_id": "2", "on_off_action": "on"})
    ]
    assert hass.states.get(SPA).state == STATE_ON
    assert hass.states.get(POOL).state == STATE_OFF

    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: SPA}, blocking=True
    )
    assert cloud.calls[-1] == (
        icl.COMMAND_ON_OFF,
        {"zone_id": "2", "on_off_action": "off"},
    )
    assert hass.states.get(SPA).state == STATE_OFF


async def test_icl_brightness_never_zero(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: AqualinkClient
) -> None:
    """A low brightness turns the zone on at the 5% minimum."""
    _, cloud = await _setup_icl(hass, config_entry, client)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: POOL, ATTR_BRIGHTNESS: 1},
        blocking=True,
    )
    assert cloud.calls == [
        (icl.COMMAND_ON_OFF, {"zone_id": "1", "on_off_action": "on"}),
        (icl.COMMAND_SET_COLOR, {"zone_id": "1", "dim_level": "5"}),
    ]
    assert hass.states.get(POOL).attributes[ATTR_BRIGHTNESS] == 13


async def test_icl_effect(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: AqualinkClient
) -> None:
    """A Jandy colour keeps the current dim level."""
    _, cloud = await _setup_icl(hass, config_entry, client)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: POOL, ATTR_EFFECT: "Emerald Green"},
        blocking=True,
    )
    assert cloud.calls[-1] == (
        icl.COMMAND_SET_COLOR,
        {"zone_id": "1", "color_id": "6", "dim_level": "50"},
    )
    assert hass.states.get(POOL).attributes[ATTR_EFFECT] == "Emerald Green"


async def test_icl_custom_rgb(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: AqualinkClient
) -> None:
    """A custom colour cycles the zone off and on first, like the app."""
    _, cloud = await _setup_icl(hass, config_entry, client)

    with (
        patch.object(icl, "CUSTOM_COLOR_OFF_DELAY", 0),
        patch.object(icl, "CUSTOM_COLOR_ON_DELAY", 0),
    ):
        await hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: POOL, ATTR_RGB_COLOR: (0, 100, 255), ATTR_BRIGHTNESS: 255},
            blocking=True,
        )
    assert [c[0] for c in cloud.calls] == [
        icl.COMMAND_ON_OFF,
        icl.COMMAND_ON_OFF,
        icl.COMMAND_SET_CUSTOM_COLOR,
        icl.COMMAND_SET_COLOR,
    ]
    assert cloud.calls[0][1]["on_off_action"] == "off"
    assert cloud.calls[2][1] == {
        "zone_id": "1",
        "red_val": "0",
        "green_val": "100",
        "blue_val": "255",
        "white_val": "0",
    }
    state = hass.states.get(POOL)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_RGB_COLOR] == (0, 100, 255)
    assert state.attributes[ATTR_EFFECT] is None


async def test_dimmable_low_brightness_stays_on(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: AqualinkClient
) -> None:
    """Brightness 1 maps to 25%, not 0% (which is off)."""
    system = get_aqualink_system(client, cls=IaquaSystem)
    system.online = True
    system.update = AsyncMock()
    light = get_aqualink_device(
        system,
        name="aux_1",
        cls=IaquaDimmableLight,
        data={"state": "1", "aux": "1", "subtype": "50", "label": "pool light"},
    )
    system.get_devices = AsyncMock(return_value={light.name: light})
    light.set_brightness = AsyncMock()
    await setup_entry(hass, config_entry, system)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "light.pool_light", ATTR_BRIGHTNESS: 1},
        blocking=True,
    )
    light.set_brightness.assert_awaited_once_with(25)


async def test_color_light_reports_no_raw_effect(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: AqualinkClient
) -> None:
    """The on/off state of a colour light isn't shown as its effect."""
    system = get_aqualink_system(client, cls=IaquaSystem)
    system.online = True
    system.update = AsyncMock()
    light = get_aqualink_device(
        system,
        name="aux_1",
        cls=IaquaColorLightJC,
        data={"state": "1", "aux": "1", "subtype": "1", "label": "pool light"},
    )
    system.get_devices = AsyncMock(return_value={light.name: light})
    await setup_entry(hass, config_entry, system)

    assert hass.states.get("light.pool_light").attributes[ATTR_EFFECT] is None


async def test_exo_command_fetches_new_state(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """EXO replies carry no state, so a command triggers a real poll."""
    config_entry.add_to_hass(hass)
    system = MagicMock(NAME="exo", online=True, last_refresh=12345)
    system.update = AsyncMock()
    coordinator = AqualinkDataUpdateCoordinator(hass, config_entry, system)

    await coordinator.async_refresh_after_command()

    assert system.last_refresh == 0
    system.update.assert_awaited_once()
    await coordinator.async_shutdown()


async def test_offline_system_does_not_block_others(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: AqualinkClient
) -> None:
    """One unreachable system is skipped; the others still load."""
    good = get_aqualink_system(client, cls=IaquaSystem)
    good.online = True
    good.update = AsyncMock()
    light = get_aqualink_device(
        good,
        name="aux_1",
        cls=IaquaDimmableLight,
        data={"state": "1", "aux": "1", "subtype": "50", "label": "pool light"},
    )
    good.get_devices = AsyncMock(return_value={light.name: light})
    bad = IaquaSystem(aqualink=client, data={"name": "Lake", "serial_number": "SN2"})
    bad.update = AsyncMock(side_effect=AqualinkServiceException("offline"))

    config_entry.add_to_hass(hass)
    with (
        patch("custom_components.iaqualink.AqualinkClient.login", return_value=None),
        patch(
            "custom_components.iaqualink.AqualinkClient.get_systems",
            return_value={good.serial: good, bad.serial: bad},
        ),
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert list(config_entry.runtime_data.coordinators) == [good.serial]
    assert hass.states.get("light.pool_light") is not None
