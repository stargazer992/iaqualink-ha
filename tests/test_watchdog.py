"""Tests for the iCL watchdog: resend lost commands, but never while the system is down."""

from __future__ import annotations

from collections.abc import Generator
import time
from typing import Any
from unittest.mock import MagicMock, patch

from iaqualink.client import AqualinkClient
from iaqualink.exception import AqualinkSystemOfflineException
from iaqualink.systems.iaqua.system import IaquaSystem
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.iaqualink import icl, watchdog
from custom_components.iaqualink.watchdog import IclWatchdog
from homeassistant.const import SERVICE_TURN_ON
from homeassistant.core import HomeAssistant

from .test_icl import (
    POOL,
    SERIAL,
    SPA,
    TURQUOISE,
    FakeIclCloud,
    _call,
    _response,
    _sequence,
    _setup_icl,
)

SET_DIM_50 = (icl.COMMAND_SET_COLOR, {"zone_id": "1", "dim_level": "50"})
GET_INFO = (icl.COMMAND_GET_INFO, {})


@pytest.fixture(autouse=True)
def no_delays() -> Generator[None]:
    """Skip the controller's settle delays and the watchdog's verify delay."""
    with (
        patch.object(icl, "TURN_ON_OFF_DELAY", 0),
        patch.object(icl, "TURN_ON_ON_DELAY", 0),
        patch.object(icl, "BRIGHTNESS_SETTLE_DELAY", 0),
        patch.object(watchdog, "VERIFY_DELAY", 0),
    ):
        yield


@pytest.fixture
def notifications() -> Generator[tuple[MagicMock, MagicMock]]:
    """Capture the watchdog's repair issues."""
    with (
        patch.object(watchdog.ir, "async_create_issue") as create,
        patch.object(watchdog.ir, "async_delete_issue") as dismiss,
    ):
        yield create, dismiss


async def _pool_light_lost(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: AqualinkClient
) -> tuple[IaquaSystem, FakeIclCloud, IclWatchdog]:
    """Turn the pool light on, then have the controller report it off."""
    system, cloud = await _setup_icl(hass, config_entry, client)
    coordinator = config_entry.runtime_data.coordinators[SERIAL]
    await _call(hass, SERVICE_TURN_ON, entity_id=POOL)
    cloud.zones[1]["zoneStatus"] = "off"
    coordinator.icl.zones[1]._hold_until = 0
    await coordinator.async_refresh()
    cloud.calls.clear()
    assert coordinator.watchdog is not None
    return system, cloud, coordinator.watchdog


def _ignore_on_off(system: IaquaSystem, cloud: FakeIclCloud, **on_command: Any) -> None:
    """Make the controller ack on/off commands without acting on them."""
    send = cloud.send

    async def stuck(command: str, params: dict[str, str]) -> MagicMock:
        if command != icl.COMMAND_ON_OFF:
            return await send(command, params)
        cloud.calls.append((command, params))
        for attr, value in on_command.items():
            setattr(system, attr, value)
        return _response(cloud.info())

    system._send_session_request.side_effect = stuck


async def test_watchdog_resends_lost_command(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: AqualinkClient
) -> None:
    """A zone that lost its on command gets the full app sequence again."""
    _, cloud, wd = await _pool_light_lost(hass, config_entry, client)
    events = async_capture_events(hass, watchdog.EVENT_WATCHDOG)

    await wd.async_run_pass()

    assert cloud.calls == [*_sequence("1", TURQUOISE), SET_DIM_50, GET_INFO]
    assert wd.failures[1] == 0
    assert [e.data["result"] for e in events] == ["fixed"]
    assert events[0].data["zone"] == "Pool"


async def test_watchdog_skips_in_sync_and_absent_zones(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: AqualinkClient
) -> None:
    """Nothing is sent for a zone in sync, or one the controller reports absent."""
    _, cloud = await _setup_icl(hass, config_entry, client)
    coordinator = config_entry.runtime_data.coordinators[SERIAL]
    await _call(hass, SERVICE_TURN_ON, entity_id=SPA)
    for zone in coordinator.icl.zones.values():
        zone._hold_until = 0
    cloud.calls.clear()

    await coordinator.watchdog.async_run_pass()

    assert cloud.calls == []


@pytest.mark.parametrize(
    ("online", "reachable", "reason"),
    [
        (False, True, "offline"),
        (None, True, "unavailable"),
        (True, False, "unavailable"),
    ],
)
async def test_watchdog_waits_while_system_down(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    client: AqualinkClient,
    online: bool | None,
    reachable: bool,
    reason: str,
) -> None:
    """An offline or unreachable system gets no retries."""
    system, cloud, wd = await _pool_light_lost(hass, config_entry, client)
    system.online = online
    wd.reachable = reachable
    wd._next_pass = 0

    assert not wd.async_check()
    assert wd.paused_reason == reason
    await wd.async_run_pass()

    assert cloud.calls == []


async def test_watchdog_waits_for_service_mode_to_end(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: AqualinkClient
) -> None:
    """No retries in Service mode; they start a minute after it's back online."""
    _, cloud, wd = await _pool_light_lost(hass, config_entry, client)
    wd.manager.system_status = icl.SYSTEM_SERVICE
    wd._next_pass = 0

    assert not wd.async_check()
    assert wd.paused_reason == "in Service mode"
    await wd.async_run_pass()
    assert cloud.calls == []

    wd.manager.system_status = icl.SYSTEM_ONLINE
    wd._next_pass = time.monotonic() + watchdog.WATCHDOG_INTERVAL
    assert not wd.async_check()
    assert wd.paused_reason is None
    assert wd._next_pass <= time.monotonic() + watchdog.RESUME_DELAY

    wd._next_pass = 0
    assert wd.async_check()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert cloud.calls == [*_sequence("1", TURQUOISE), SET_DIM_50, GET_INFO]


async def test_home_screen_status_is_read(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: AqualinkClient
) -> None:
    """Service mode and Offline come from the home screen's status."""
    system, _, wd = await _pool_light_lost(hass, config_entry, client)

    system._parse_home_response(
        _response(
            {
                "home_screen": [
                    {"status": "Service"},
                    {"response": ""},
                    {"system_type": ""},
                ]
            }
        )
    )
    assert wd.pause_reason() == "in Service mode"

    with pytest.raises(AqualinkSystemOfflineException):
        system._parse_home_response(_response({"home_screen": [{"status": "Offline"}]}))
    assert wd.manager.system_status == "Offline"


async def test_watchdog_alerts_after_two_failed_passes(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    client: AqualinkClient,
    notifications: tuple[MagicMock, MagicMock],
) -> None:
    """Two failed passes raise a notification; it clears once the light takes."""
    create, dismiss = notifications
    system, cloud, wd = await _pool_light_lost(hass, config_entry, client)
    send = cloud.send
    _ignore_on_off(system, cloud)
    zone = wd.manager.zones[1]

    await wd.async_run_pass()
    assert wd.failures[1] == 1
    create.assert_not_called()

    zone._hold_until = 0
    await wd.async_run_pass()
    assert wd.failures[1] == 2
    create.assert_called_once()
    placeholders = create.call_args.kwargs["translation_placeholders"]
    assert (placeholders["zone"], placeholders["desired"]) == ("Pool", "on")

    system._send_session_request.side_effect = send
    zone._hold_until = 0
    await wd.async_run_pass()
    assert wd.failures[1] == 0
    dismiss.assert_called_once()


async def test_watchdog_does_not_count_a_system_that_went_down(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: AqualinkClient
) -> None:
    """If the system drops out while retrying, the try doesn't count as failed."""
    system, cloud, wd = await _pool_light_lost(hass, config_entry, client)
    _ignore_on_off(system, cloud, online=False)

    await wd.async_run_pass()

    assert GET_INFO not in cloud.calls
    assert wd.failures.get(1, 0) == 0
