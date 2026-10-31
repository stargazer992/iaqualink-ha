"""Tests for the Local (AqualinkD) connection."""

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
from iaqualink.systems.iaqua.system import IaquaSystem
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.iaqualink.const import (
    CONF_AQUALINKD_URL,
    CONF_CONNECTION,
    CONNECTION_CLOUD,
    CONNECTION_LOCAL,
    DOMAIN,
)
from custom_components.iaqualink.local import (
    AqualinkdClient,
    AqualinkdError,
    LocalSystem,
    cloud_name,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from .conftest import MOCK_DATA, get_aqualink_system, setup_entry

URL = "http://aqualinkd.test"


class FakeAqualinkd:
    """A tiny AqualinkD: /api/devices, /api/status and the set URLs."""

    def __init__(self) -> None:
        self.status = "Ready"
        self.down = False
        self.requests: list[str] = []
        self.rows: dict[str, dict[str, Any]] = {
            row["id"]: row
            for row in (
                {
                    "type": "switch",
                    "id": "Filter_Pump",
                    "name": "Filter Pump",
                    "state": "on",
                },
                {"type": "switch", "id": "Aux_1", "name": "Cleaner", "state": "off"},
                {
                    "type": "setpoint_thermo",
                    "id": "Pool_Heater",
                    "name": "Pool Heater",
                    "state": "off",
                    "spvalue": "80",
                    "value": "78",
                },
                {
                    "type": "setpoint_freeze",
                    "id": "Freeze_Protect",
                    "name": "Freeze Protection",
                    "state": "off",
                    "spvalue": "38",
                    "value": "60",
                },
                {
                    "type": "value",
                    "id": "SWG/Percent",
                    "name": "SWG Percent",
                    "state": "on",
                    "value": "80",
                },
                {
                    "type": "value",
                    "id": "SWG/PPM",
                    "name": "SWG PPM",
                    "state": "on",
                    "value": "3200",
                },
                {
                    "type": "switch",
                    "id": "Aux_2",
                    "name": "Pool Pump",
                    "state": "on",
                    "type_ext": "switch_vsp",
                    "Pump_RPM": "2250",
                    "Pump_GPM": "0",
                    "Pump_Watts": "640",
                    "Pump_Type": "vsPump",
                    "Pump_Status": "0",
                    "Pump_Speed": "65",
                },
                {
                    "type": "switch",
                    "id": "Aux_3",
                    "name": "Spa Light",
                    "state": "off",
                    "type_ext": "switch_program",
                    "Light_Type": "1",
                    "Light_Program": "0",
                    "Program_Name": "Off",
                    "light_programs": ["Off", "Alpine White", "Sky Blue"],
                },
                {
                    "type": "switch",
                    "id": "Aux_4",
                    "name": "Path Lights",
                    "state": "on",
                    "type_ext": "light_dimmer",
                    "Light_Program": "50",
                },
                {
                    "type": "switch",
                    "id": "Aux_5",
                    "name": "Waterfall",
                    "state": "on",
                    "type_ext": "switch_timer",
                    "timer_active": "on",
                    "timer_duration": "1200",
                },
                {
                    "type": "setpoint_swg",
                    "id": "SWG",
                    "name": "Salt Water Generator",
                    "state": "on",
                    "spvalue": "80",
                    "value": "80",
                },
                {
                    "type": "switch",
                    "id": "SWG/Boost",
                    "name": "SWG Boost",
                    "state": "off",
                },
                {
                    "type": "temperature",
                    "id": "Temperature/Pool",
                    "name": "Pool",
                    "state": "on",
                    "value": "78",
                },
            )
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("down", request=request)
        path = request.url.path.removeprefix("/api/")
        if path == "devices":
            return httpx.Response(
                200,
                json={
                    "type": "devices",
                    "temp_units": "F",
                    "devices": list(self.rows.values()),
                },
            )
        if path == "status":
            return httpx.Response(
                200,
                json={
                    "type": "status",
                    "status": self.status,
                    "panel_message": "AquaPure 80%",
                    "battery": "low",
                    "swg_fullstatus": "0",
                },
            )
        self.requests.append(f"{path}={request.url.params.get('value')}")
        device, _, action = path.partition("/")
        value = request.url.params.get("value")
        if action == "set" and device in self.rows:
            self.rows[device]["state"] = "on" if value == "1" else "off"
        elif action == "setpoint/set" and device in self.rows:
            self.rows[device]["spvalue"] = value
        elif path == "SWG/Percent/set":
            self.rows["SWG"]["spvalue"] = value
        elif action == "RPM/set":
            self.rows[device]["Pump_RPM"] = value
        elif action == "program/set":
            programs = self.rows[device]["light_programs"]
            self.rows[device]["Program_Name"] = programs[int(value)]
        return httpx.Response(200, json={"message": "ok"})

    def client(self, *_args: Any, **_kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


@pytest.fixture(name="fake")
def fake_fixture() -> FakeAqualinkd:
    """Return a fake AqualinkD."""
    return FakeAqualinkd()


def _local_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_DATA,
        options={CONF_CONNECTION: CONNECTION_LOCAL, CONF_AQUALINKD_URL: URL},
    )


async def _setup_local(
    hass: HomeAssistant, fake: FakeAqualinkd, client
) -> MockConfigEntry:
    system = get_aqualink_system(
        client, cls=IaquaSystem, data={"home_screen": [{}, {}, {}, {"temp_scale": "F"}]}
    )
    system.online = True
    system.update = AsyncMock()
    system.devices = {}
    system.get_devices = AsyncMock(return_value={})
    entry = _local_entry()
    with patch("custom_components.iaqualink.get_async_client", fake.client):
        await setup_entry(hass, entry, system)
    return entry


def _entity(hass: HomeAssistant, domain: str, name: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        domain, DOMAIN, f"SN00001_{name}"
    )
    assert entity_id is not None, f"{domain} {name} missing"
    return entity_id


def test_cloud_names() -> None:
    """AqualinkD ids map onto the cloud's device names."""
    assert cloud_name("Filter_Pump") == "pool_pump"
    assert cloud_name("Aux_3") == "aux_3"
    assert cloud_name("Aux_V1") == "aux_v1"
    assert cloud_name("SWG/PPM") == "pool_salinity"


async def test_local_system_parses_devices(fake: FakeAqualinkd) -> None:
    """Rows become switches, a thermostat, sensors and a binary sensor."""
    system = LocalSystem(AqualinkdClient(URL, fake.client()), "SN1", "Pool")
    devices = await system.get_devices()
    assert set(devices) >= {
        "pool_pump",
        "aux_1",
        "pool_heater",
        "pool_set_point",
        "freeze_protection",
        "freeze_protect_set_point",
        "swg_percent",
        "swg_set_point",
        "swg_boost",
        "pool_salinity",
        "pool_temp",
        "aux_2_rpm",
        "aux_2_watts",
        "aux_2_rpm_set",
        "aux_3_program",
        "aux_4_brightness",
        "display_message",
        "swg_status",
        "battery_low",
        "panel_clock",
    }
    assert devices["pool_set_point"].target_temperature == "80"
    assert devices["pool_set_point"].current_temperature == "78"
    assert devices["pool_salinity"].state == "3200"
    assert system.online is True

    await devices["aux_1"].turn_on()
    await devices["pool_set_point"].set_temperature(84)
    assert fake.requests == ["Aux_1/set=1", "Pool_Heater/setpoint/set=84"]


async def test_local_system_unreachable(fake: FakeAqualinkd) -> None:
    """An unreachable AqualinkD marks the system offline."""
    fake.down = True
    system = LocalSystem(AqualinkdClient(URL, fake.client()), "SN1", "Pool")
    with pytest.raises(AqualinkdError):
        await system.update()
    assert system.online is False


async def test_local_entities(hass: HomeAssistant, fake: FakeAqualinkd, client) -> None:
    """Local mode creates entities with the cloud's unique IDs and sends commands locally."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    entry = await _setup_local(hass, fake, client)
    assert entry.state is ConfigEntryState.LOADED

    assert hass.states.get(_entity(hass, "switch", "pool_pump")).state == "on"
    assert hass.states.get(_entity(hass, "sensor", "pool_salinity")).state == "3200"
    assert hass.states.get(_entity(hass, "sensor", "swg_percent")).state == "80"
    status = _entity(hass, "sensor", "system_status")
    assert hass.states.get(status).state == "Online"

    cleaner = _entity(hass, "switch", "aux_1")
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": cleaner}, blocking=True
    )
    assert "Aux_1/set=1" in fake.requests
    assert hass.states.get(cleaner).state == "on"

    climate = _entity(hass, "climate", "pool_set_point")
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": climate, "temperature": 85},
        blocking=True,
    )
    assert "Pool_Heater/setpoint/set=85" in fake.requests


async def test_local_parity_entities(
    hass: HomeAssistant, fake: FakeAqualinkd, client
) -> None:
    """Pumps, SWG, light programs, dimmers, timers and panel status come through."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    await _setup_local(hass, fake, client)

    rpm = hass.states.get(_entity(hass, "sensor", "aux_2_rpm"))
    assert rpm.state == "2250"
    assert rpm.attributes["unit_of_measurement"] == "rpm"
    assert hass.states.get(_entity(hass, "sensor", "aux_2_watts")).state == "640"
    message = _entity(hass, "sensor", "display_message")
    assert hass.states.get(message).state == "AquaPure 80%"
    assert hass.states.get(_entity(hass, "binary_sensor", "battery_low")).state == "on"
    waterfall = hass.states.get(_entity(hass, "switch", "aux_5"))
    assert waterfall.attributes["timer_active"] is True
    assert waterfall.attributes["timer_remaining"] == 1200

    for entity, value in (
        ("swg_set_point", 60),
        ("aux_2_rpm_set", 2800),
        ("freeze_protect_set_point", 36),
        ("aux_4_brightness", 75),
    ):
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": _entity(hass, "number", entity), "value": value},
            blocking=True,
        )
    assert {
        "SWG/Percent/set=60",
        "Aux_2/RPM/set=2800",
        "Freeze_Protect/setpoint/set=36",
        "Aux_4/brightness/set=75",
    } <= set(fake.requests)
    assert hass.states.get(_entity(hass, "number", "swg_set_point")).state == "60.0"

    program = _entity(hass, "select", "aux_3_program")
    assert hass.states.get(program).attributes["options"] == [
        "Off",
        "Alpine White",
        "Sky Blue",
    ]
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": program, "option": "Sky Blue"},
        blocking=True,
    )
    assert "Aux_3/program/set=2" in fake.requests
    assert hass.states.get(program).state == "Sky Blue"

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": _entity(hass, "switch", "swg_boost")},
        blocking=True,
    )
    assert "SWG/Boost/set=1" in fake.requests

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": _entity(hass, "button", "panel_clock")},
        blocking=True,
    )
    assert "set_date_time=None" in fake.requests


async def test_local_service_mode(
    hass: HomeAssistant, fake: FakeAqualinkd, client
) -> None:
    """AqualinkD's Service Mode shows as Service."""
    fake.status = "Service Mode"
    await _setup_local(hass, fake, client)
    assert hass.states.get(_entity(hass, "sensor", "system_status")).state == "Service"
    assert hass.states.get(_entity(hass, "binary_sensor", "service_mode")).state == "on"


async def test_local_unreachable_retries(
    hass: HomeAssistant, fake: FakeAqualinkd, client
) -> None:
    """If AqualinkD is down at startup the entry retries setup."""
    fake.down = True
    entry = await _setup_local(hass, fake, client)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_options_flow(hass: HomeAssistant, fake: FakeAqualinkd) -> None:
    """The options flow switches to Local after checking AqualinkD answers."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_DATA)
    entry.add_to_hass(hass)
    with (
        patch("custom_components.iaqualink.config_flow.get_async_client", fake.client),
        patch("custom_components.iaqualink.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.FORM

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_CONNECTION: CONNECTION_LOCAL, CONF_AQUALINKD_URL: "aqualinkd"},
        )
        assert result["errors"] == {CONF_AQUALINKD_URL: "invalid_url"}

        fake.down = True
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_CONNECTION: CONNECTION_LOCAL, CONF_AQUALINKD_URL: URL},
        )
        assert result["errors"] == {"base": "cannot_connect_aqualinkd"}

        fake.down = False
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_CONNECTION: CONNECTION_LOCAL, CONF_AQUALINKD_URL: URL + "/"},
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_CONNECTION: CONNECTION_LOCAL, CONF_AQUALINKD_URL: URL}

    with patch("custom_components.iaqualink.async_setup_entry", return_value=True):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_CONNECTION: CONNECTION_CLOUD, CONF_AQUALINKD_URL: URL},
        )
    assert entry.options[CONF_CONNECTION] == CONNECTION_CLOUD


def _infinite_zone(fake: FakeAqualinkd) -> tuple[Any, LocalSystem, AsyncMock]:
    from custom_components.iaqualink.icl import IclZone  # noqa: PLC0415

    fake.rows["Aux_V1"] = {
        "type": "switch",
        "id": "Aux_V1",
        "name": "Pool Lights",
        "state": "off",
        "type_ext": "switch_program",
        "Light_Type": "7",
        "Light_Program": "0",
        "Program_Name": "Off",
        "light_programs": ["Off", "Alpine White"],
    }
    system = LocalSystem(AqualinkdClient(URL, fake.client()), "SN1", "Pool")
    manager = AsyncMock()
    manager.system = system
    zone = IclZone(manager, {"zoneId": "1", "zoneName": "Pool Lights"})
    return zone, system, manager.send


async def test_infinite_light_drives_zone(fake: FakeAqualinkd) -> None:
    """A matching Infinite WaterColors button takes the zone's commands."""
    zone, system, cloud_send = _infinite_zone(fake)
    devices = await system.get_devices()
    assert "aux_v1" not in devices
    assert "aux_v1_program" not in devices
    zone.local = system.infinite["pool lights"]

    await zone.turn_on((0, 100, 255), 40)
    assert fake.requests[-3:] == [
        "Aux_V1/set=1",
        f"Aux_V1/rgb/set={(0 << 16) | (100 << 8) | 255}",
        "Aux_V1/brightness/set=40",
    ]
    assert zone.is_on is True
    assert zone.rgb == (0, 100, 255)
    await zone.set_brightness(70)
    await zone.turn_off()
    assert fake.requests[-2:] == ["Aux_V1/brightness/set=70", "Aux_V1/set=0"]
    cloud_send.assert_not_called()

    fake.rows["Aux_V1"]["state"] = "on"
    await system.update()
    zone.sync_local()
    assert zone.is_on is True
    assert zone.reported_on is True


async def test_infinite_light_falls_back_to_cloud(fake: FakeAqualinkd) -> None:
    """If AqualinkD is down the zone uses the cloud's captured sequence."""
    zone, system, cloud_send = _infinite_zone(fake)
    await system.get_devices()
    zone.local = system.infinite["pool lights"]
    fake.down = True
    with (
        patch("custom_components.iaqualink.icl.TURN_ON_OFF_DELAY", 0),
        patch("custom_components.iaqualink.icl.TURN_ON_ON_DELAY", 0),
    ):
        await zone.turn_on((0, 100, 255), None)
    commands = [call.args[0] for call in cloud_send.call_args_list]
    assert commands == ["onoff_iclzone", "onoff_iclzone", "define_iclzone_customcolor"]
