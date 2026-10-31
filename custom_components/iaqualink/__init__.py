"""Component to embed Aqualink devices."""

from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from functools import wraps
import logging
from pathlib import Path
from typing import Any, Concatenate

import httpx
from iaqualink.client import AqualinkClient
from iaqualink.device import (
    AqualinkBinarySensor,
    AqualinkLight,
    AqualinkSensor,
    AqualinkSwitch,
    AqualinkThermostat,
)
from iaqualink.exception import (
    AqualinkServiceException,
    AqualinkServiceUnauthorizedException,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration
from homeassistant.util.ssl import SSL_ALPN_HTTP11_HTTP2

from .const import DOMAIN
from .coordinator import AqualinkDataUpdateCoordinator
from .entity import AqualinkEntity
from .icl import IclManager, IclZone
from .utils import error_detail
from .watchdog import IclWatchdog

_LOGGER = logging.getLogger(__name__)

ATTR_CONFIG = "config"
PARALLEL_UPDATES = 0
RETRY_SKIPPED_SYSTEMS_AFTER = 300
CARD_URL = "/iaqualink/iaqualink-pool-light-card.js"
CARD_PATH = Path(__file__).parent / "frontend" / "iaqualink-pool-light-card.js"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.LIGHT,
    Platform.SENSOR,
    Platform.SWITCH,
]

type AqualinkConfigEntry = ConfigEntry[AqualinkRuntimeData]


@dataclass
class AqualinkRuntimeData:
    """Runtime data for Aqualink."""

    client: AqualinkClient
    coordinators: dict[str, AqualinkDataUpdateCoordinator]
    # These will contain the initialized devices
    binary_sensors: list[AqualinkBinarySensor]
    lights: list[AqualinkLight]
    sensors: list[AqualinkSensor]
    switches: list[AqualinkSwitch]
    thermostats: list[AqualinkThermostat]
    icl_lights: list[IclZone] = field(default_factory=list)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the pool light card and load it in the frontend."""
    if getattr(hass, "http", None) is None:
        return True
    from homeassistant.components.frontend import add_extra_js_url  # noqa: PLC0415
    from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415

    integration = await async_get_integration(hass, DOMAIN)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_PATH), True)]
    )
    add_extra_js_url(hass, f"{CARD_URL}?v={integration.version}")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AqualinkConfigEntry) -> bool:
    """Set up Aqualink from a config entry."""
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    if entry.unique_id is None:
        hass.config_entries.async_update_entry(entry, unique_id=username.lower())

    aqualink = AqualinkClient(
        username,
        password,
        httpx_client=get_async_client(hass, alpn_protocols=SSL_ALPN_HTTP11_HTTP2),
    )
    try:
        await aqualink.login()
    except AqualinkServiceUnauthorizedException as auth_exception:
        await aqualink.close()
        raise ConfigEntryAuthFailed(
            "Invalid credentials for iAquaLink"
        ) from auth_exception
    except (AqualinkServiceException, TimeoutError, httpx.HTTPError) as aio_exception:
        await aqualink.close()
        raise ConfigEntryNotReady(
            f"Error while attempting login: {error_detail(aio_exception)}"
        ) from aio_exception

    try:
        systems = await aqualink.get_systems()
    except AqualinkServiceUnauthorizedException as auth_exception:
        await aqualink.close()
        raise ConfigEntryAuthFailed(
            "Invalid credentials for iAquaLink"
        ) from auth_exception
    except (AqualinkServiceException, TimeoutError, httpx.HTTPError) as svc_exception:
        await aqualink.close()
        raise ConfigEntryNotReady(
            "Error while attempting to retrieve systems list: "
            f"{error_detail(svc_exception)}"
        ) from svc_exception

    systems_list = list(systems.values())
    if not systems_list:
        await aqualink.close()
        raise ConfigEntryError("No systems detected or supported")

    runtime_data = AqualinkRuntimeData(
        aqualink,
        coordinators={},
        binary_sensors=[],
        lights=[],
        sensors=[],
        switches=[],
        thermostats=[],
    )
    skipped: list[str] = []
    for system in systems_list:
        try:
            await _async_setup_system(hass, entry, runtime_data, system)
        except ConfigEntryAuthFailed:
            await aqualink.close()
            raise
        except ConfigEntryNotReady as err:
            runtime_data.coordinators.pop(system.serial, None)
            if len(systems_list) == 1:
                await aqualink.close()
                raise
            _LOGGER.warning(
                "Skipping iAquaLink system %s for now: %s", system.serial, err
            )
            skipped.append(system.serial)

    if len(skipped) == len(systems_list):
        await aqualink.close()
        raise ConfigEntryNotReady("No iAquaLink system could be reached")
    if skipped:
        # Reload later so the systems that were offline get added.
        entry.async_on_unload(
            async_call_later(
                hass,
                RETRY_SKIPPED_SYSTEMS_AFTER,
                lambda _now: hass.config_entries.async_schedule_reload(entry.entry_id),
            )
        )

    _LOGGER.debug(
        "Got %s binary sensors: %s",
        len(runtime_data.binary_sensors),
        runtime_data.binary_sensors,
    )
    _LOGGER.debug("Got %s lights: %s", len(runtime_data.lights), runtime_data.lights)
    _LOGGER.debug("Got %s iCL zones", len(runtime_data.icl_lights))
    _LOGGER.debug("Got %s sensors: %s", len(runtime_data.sensors), runtime_data.sensors)
    _LOGGER.debug(
        "Got %s switches: %s", len(runtime_data.switches), runtime_data.switches
    )
    _LOGGER.debug(
        "Got %s thermostats: %s",
        len(runtime_data.thermostats),
        runtime_data.thermostats,
    )

    entry.runtime_data = runtime_data

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def _async_setup_system(
    hass: HomeAssistant,
    entry: AqualinkConfigEntry,
    runtime_data: AqualinkRuntimeData,
    system: Any,
) -> None:
    """Refresh one system and collect its devices."""
    coordinator = AqualinkDataUpdateCoordinator(hass, entry, system)
    runtime_data.coordinators[system.serial] = coordinator
    if system.NAME == "iaqua":
        coordinator.icl = IclManager(system)
    await coordinator.async_config_entry_first_refresh()

    try:
        devices = await system.get_devices()
    except AqualinkServiceUnauthorizedException as auth_exception:
        raise ConfigEntryAuthFailed(
            "Invalid credentials for iAquaLink"
        ) from auth_exception
    except (
        AqualinkServiceException,
        TimeoutError,
        httpx.HTTPError,
    ) as svc_exception:
        raise ConfigEntryNotReady(
            "Error while attempting to retrieve devices list: "
            f"{error_detail(svc_exception)}"
        ) from svc_exception

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        name=system.name,
        identifiers={(DOMAIN, system.serial)},
        manufacturer="Jandy",
        serial_number=system.serial,
    )

    for dev in devices.values():
        if isinstance(dev, AqualinkThermostat):
            runtime_data.thermostats += [dev]
        elif isinstance(dev, AqualinkLight):
            runtime_data.lights += [dev]
        elif isinstance(dev, AqualinkSwitch):
            runtime_data.switches += [dev]
        elif isinstance(dev, AqualinkBinarySensor):
            runtime_data.binary_sensors += [dev]
        elif isinstance(dev, AqualinkSensor):
            runtime_data.sensors += [dev]

    if coordinator.icl is not None:
        await coordinator.icl.async_discover()
        runtime_data.icl_lights += list(coordinator.icl.zones.values())
        if coordinator.icl.zones:
            coordinator.watchdog = IclWatchdog(hass, coordinator, coordinator.icl)


async def async_unload_entry(hass: HomeAssistant, entry: AqualinkConfigEntry) -> bool:
    """Unload a config entry."""
    await entry.runtime_data.client.close()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def refresh_system[_AqualinkEntityT: AqualinkEntity, **_P](
    func: Callable[Concatenate[_AqualinkEntityT, _P], Awaitable[Any]],
) -> Callable[Concatenate[_AqualinkEntityT, _P], Coroutine[Any, Any, None]]:
    """Force update all entities after state change."""

    @wraps(func)
    async def wrapper(
        self: _AqualinkEntityT, *args: _P.args, **kwargs: _P.kwargs
    ) -> None:
        """Call decorated function and send update signal to all entities."""
        await func(self, *args, **kwargs)
        await self.coordinator.async_refresh_after_command()

    return wrapper
