"""Tests for iCL light zones and the fork's review fixes."""

from __future__ import annotations

from collections.abc import Generator
import time
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
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

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
    ATTR_ASSUMED_STATE,
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant, State

from .conftest import get_aqualink_device, get_aqualink_system, setup_entry

POOL = "light.pool"
SPA = "light.spa"
SERIAL = "SN00001"
TURQUOISE = {"red_val": "27", "green_val": "205", "blue_val": "255", "white_val": "0"}


def _response(data: dict[str, Any]) -> MagicMock:
    response = MagicMock()
    response.json.return_value = data
    return response


@pytest.fixture(autouse=True)
def no_delays() -> Generator[None]:
    """Skip the controller's settle delays."""
    with (
        patch.object(icl, "TURN_ON_OFF_DELAY", 0),
        patch.object(icl, "TURN_ON_ON_DELAY", 0),
        patch.object(icl, "BRIGHTNESS_SETTLE_DELAY", 0),
    ):
        yield


class FakeIclCloud:
    """Answers iCL commands like the iAquaLink cloud does.

    The spa zone is reported as "absent", as Robert's controller does.
    """

    def __init__(self) -> None:
        self.zones = {
            1: {
                "zoneId": 1,
                "zoneName": "Pool",
                "zoneStatus": "off",
                "zoneColor": 0,
                "dim_level": 50,
            },
            2: {"zoneId": 2, "zoneName": "Spa", "zoneStatus": "absent"},
        }
        self.calls: list[tuple[str, dict[str, str]]] = []

    def info(self) -> dict[str, Any]:
        return {"icl_info_list": [dict(z) for z in self.zones.values()]}

    async def send(self, command: str, params: dict[str, str]) -> MagicMock:
        self.calls.append((command, params))
        zone = self.zones.get(int(params.get("zone_id", 0)), {})
        if zone.get("zoneStatus") == "absent":
            return _response(self.info())
        if command == icl.COMMAND_ON_OFF:
            zone["zoneStatus"] = params["on_off_action"]
        elif command == icl.COMMAND_SET_COLOR:
            zone["dim_level"] = int(params["dim_level"])
        elif command == icl.COMMAND_SET_CUSTOM_COLOR:
            zone["zoneColor"] = 16
            zone.update({k: params[k] for k in ("red_val", "green_val", "blue_val")})
        return _response(self.info())


def _sequence(zone: str, rgb: dict[str, str]) -> list[tuple[str, dict[str, str]]]:
    return [
        (icl.COMMAND_ON_OFF, {"zone_id": zone, "on_off_action": "off"}),
        (icl.COMMAND_ON_OFF, {"zone_id": zone, "on_off_action": "on"}),
        (icl.COMMAND_SET_CUSTOM_COLOR, {"zone_id": zone, **rgb}),
    ]


async def _setup_icl(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    client: AqualinkClient,
    *,
    in_devices: bool = True,
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
        if in_devices:
            system._parse_devices_response(_response(cloud.info()))

    system.update = AsyncMock(side_effect=update)
    system.get_devices = AsyncMock(return_value={present.name: present})
    system._send_session_request = AsyncMock(side_effect=cloud.send)
    await setup_entry(hass, config_entry, system)
    return system, cloud


async def _call(hass: HomeAssistant, service: str, **data: Any) -> None:
    await hass.services.async_call(LIGHT_DOMAIN, service, data, blocking=True)


async def test_icl_zones_discovered(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: AqualinkClient
) -> None:
    """Zones come from get_devices; an "absent" zone is kept, as assumed state."""
    _, cloud = await _setup_icl(hass, config_entry, client)

    assert sorted(hass.states.async_entity_ids(LIGHT_DOMAIN)) == [POOL, SPA]
    pool = hass.states.get(POOL)
    assert pool.state == STATE_OFF
    assert not pool.attributes.get(ATTR_ASSUMED_STATE)
    assert pool.attributes[ATTR_EFFECT_LIST] == list(icl.PRESETS)
    spa = hass.states.get(SPA)
    assert spa.state == STATE_UNKNOWN
    assert spa.attributes[ATTR_ASSUMED_STATE]
    assert cloud.calls == []


async def test_icl_zones_from_get_icl_info(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: AqualinkClient
) -> None:
    """Without icl_info_list in get_devices, get_icl_info is used instead."""
    _, cloud = await _setup_icl(hass, config_entry, client, in_devices=False)

    assert cloud.calls == [(icl.COMMAND_GET_INFO, {})]
    assert sorted(hass.states.async_entity_ids(LIGHT_DOMAIN)) == [POOL, SPA]


async def test_icl_turn_on_runs_app_sequence(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: AqualinkClient
) -> None:
    """Turning on sends off, on, then the custom colour (last one, or turquoise)."""
    _, cloud = await _setup_icl(hass, config_entry, client)

    await _call(hass, SERVICE_TURN_ON, entity_id=POOL)
    assert cloud.calls == _sequence("1", TURQUOISE)
    state = hass.states.get(POOL)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_RGB_COLOR] == (27, 205, 255)
    assert state.attributes[ATTR_EFFECT] == "Turquoise"

    await _call(hass, SERVICE_TURN_OFF, entity_id=POOL)
    assert cloud.calls[-1] == (
        icl.COMMAND_ON_OFF,
        {"zone_id": "1", "on_off_action": "off"},
    )
    assert hass.states.get(POOL).state == STATE_OFF


async def test_icl_absent_zone_is_commanded(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: AqualinkClient
) -> None:
    """A zone reported "absent" (the spa) still gets the commands and shows them."""
    _, cloud = await _setup_icl(hass, config_entry, client)

    await _call(hass, SERVICE_TURN_ON, entity_id=SPA, brightness_pct=5)
    assert cloud.calls == [
        *_sequence("2", TURQUOISE),
        (icl.COMMAND_SET_COLOR, {"zone_id": "2", "dim_level": "5"}),
    ]
    state = hass.states.get(SPA)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_BRIGHTNESS] == 13

    await config_entry.runtime_data.coordinators[SERIAL].async_refresh()
    assert hass.states.get(SPA).state == STATE_ON


async def test_icl_brightness(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: AqualinkClient
) -> None:
    """When on, brightness only sends a dim level; low values stay at 5%."""
    _, cloud = await _setup_icl(hass, config_entry, client)
    await _call(hass, SERVICE_TURN_ON, entity_id=POOL)
    cloud.calls.clear()

    await _call(hass, SERVICE_TURN_ON, entity_id=POOL, brightness=1)
    assert cloud.calls == [(icl.COMMAND_SET_COLOR, {"zone_id": "1", "dim_level": "5"})]
    assert hass.states.get(POOL).attributes[ATTR_BRIGHTNESS] == 13


async def test_icl_preset_and_rgb(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: AqualinkClient
) -> None:
    """Presets and custom colours both run the app sequence with their colour."""
    _, cloud = await _setup_icl(hass, config_entry, client)
    blue = {"red_val": "0", "green_val": "100", "blue_val": "255", "white_val": "0"}

    await _call(hass, SERVICE_TURN_ON, entity_id=POOL, effect="Blue")
    assert cloud.calls == _sequence("1", blue)
    cloud.calls.clear()

    await _call(hass, SERVICE_TURN_ON, entity_id=POOL, rgb_color=(10, 20, 30))
    assert cloud.calls == _sequence(
        "1", {"red_val": "10", "green_val": "20", "blue_val": "30", "white_val": "0"}
    )
    state = hass.states.get(POOL)
    assert state.attributes[ATTR_RGB_COLOR] == (10, 20, 30)
    assert state.attributes[ATTR_EFFECT] is None
    cloud.calls.clear()

    await _call(hass, SERVICE_TURN_OFF, entity_id=POOL)
    await _call(hass, SERVICE_TURN_ON, entity_id=POOL)
    assert cloud.calls[1:] == _sequence(
        "1", {"red_val": "10", "green_val": "20", "blue_val": "30", "white_val": "0"}
    )


async def test_icl_stale_report_ignored_after_command(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: AqualinkClient
) -> None:
    """A lagging cloud report doesn't flip a just-commanded zone back."""
    _, cloud = await _setup_icl(hass, config_entry, client)
    coordinator = config_entry.runtime_data.coordinators[SERIAL]
    await _call(hass, SERVICE_TURN_ON, entity_id=POOL)

    cloud.zones[1]["zoneStatus"] = "off"
    await coordinator.async_refresh()
    assert hass.states.get(POOL).state == STATE_ON

    with patch.object(icl.time, "monotonic", return_value=time.monotonic() + 1e6):
        await coordinator.async_refresh()
    assert hass.states.get(POOL).state == STATE_OFF


async def test_icl_restores_unreported_state(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: AqualinkClient
) -> None:
    """After a restart the spa shows its last state and colour."""
    mock_restore_cache(
        hass,
        [State(SPA, STATE_ON, {ATTR_BRIGHTNESS: 13, ATTR_RGB_COLOR: [0, 100, 255]})],
    )
    _, cloud = await _setup_icl(hass, config_entry, client)

    state = hass.states.get(SPA)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_EFFECT] == "Blue"
    await _call(hass, SERVICE_TURN_ON, entity_id=SPA)
    assert cloud.calls[-1] == (
        icl.COMMAND_SET_CUSTOM_COLOR,
        {
            "zone_id": "2",
            "red_val": "0",
            "green_val": "100",
            "blue_val": "255",
            "white_val": "0",
        },
    )


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
