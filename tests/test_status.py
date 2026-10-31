"""Tests for the system status and Service mode sensors."""

from __future__ import annotations

from iaqualink.client import AqualinkClient
from iaqualink.exception import AqualinkServiceException
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .test_icl import SERIAL, _response, _setup_icl


def _ids(hass: HomeAssistant) -> tuple[str, str]:
    registry = er.async_get(hass)
    status = registry.async_get_entity_id(
        "sensor", "iaqualink", f"{SERIAL}_system_status"
    )
    service = registry.async_get_entity_id(
        "binary_sensor", "iaqualink", f"{SERIAL}_service_mode"
    )
    assert status and service
    return status, service


def _home(status: str) -> dict:
    return {"home_screen": [{"status": status}, {"response": ""}, {"system_type": ""}]}


async def test_status_online_service_offline(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: AqualinkClient
) -> None:
    """The sensors follow the home screen status and the cloud's reachability."""
    system, _ = await _setup_icl(hass, config_entry, client)
    coordinator = config_entry.runtime_data.coordinators[SERIAL]
    status, service = _ids(hass)
    assert hass.states.get(status).state == "Online"
    assert hass.states.get(service).state == STATE_OFF

    system._parse_home_response(_response(_home("Service")))
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(status).state == "Service"
    assert hass.states.get(service).state == STATE_ON

    system._parse_home_response(_response(_home("Online")))
    system.update.side_effect = AqualinkServiceException("cloud down")
    await coordinator.async_refresh()
    assert hass.states.get(status).state == "Offline"
    assert hass.states.get(service).state == STATE_OFF
