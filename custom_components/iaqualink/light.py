"""Support for Aqualink pool lights."""

from typing import Any, override

from iaqualink.device import AqualinkLight
import voluptuous as vol

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_BRIGHTNESS_PCT,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoredExtraData, RestoreEntity

from . import AqualinkConfigEntry, refresh_system
from .coordinator import AqualinkDataUpdateCoordinator
from .entity import AqualinkEntity
from .icl import PRESETS, IclZone
from .utils import await_or_reraise

PARALLEL_UPDATES = 1

SERVICE_SET_NEXT_ON = "set_next_on"
ATTR_SAVED_RGB_COLOR = "saved_rgb_color"
ATTR_SAVED_BRIGHTNESS_PCT = "saved_brightness_pct"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: AqualinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up discovered lights."""
    runtime_data = config_entry.runtime_data
    entities: list[LightEntity] = [
        HassAqualinkLight(runtime_data.coordinators[dev.system.serial], dev)
        for dev in runtime_data.lights
    ]
    entities.extend(
        HassAqualinkIclLight(runtime_data.coordinators[zone.system.serial], zone)
        for zone in runtime_data.icl_lights
    )
    async_add_entities(entities)
    entity_platform.async_get_current_platform().async_register_entity_service(
        SERVICE_SET_NEXT_ON,
        {
            vol.Optional(ATTR_RGB_COLOR): vol.All(
                vol.Coerce(tuple), vol.ExactSequence((cv.byte,) * 3)
            ),
            vol.Optional(ATTR_BRIGHTNESS_PCT): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=100)
            ),
        },
        _async_set_next_on,
    )


async def _async_set_next_on(entity: LightEntity, call: ServiceCall) -> None:
    if not isinstance(entity, HassAqualinkIclLight):
        raise ServiceValidationError(f"{entity.entity_id} is not an iCL light zone")
    await entity.async_set_next_on(
        call.data.get(ATTR_RGB_COLOR), call.data.get(ATTR_BRIGHTNESS_PCT)
    )


def _to_percentage(brightness: int, step: int, minimum: int) -> int:
    """Convert 1-255 to a 0-100 percentage in steps, never below minimum."""
    pct = round(brightness * 100 / 255 / step) * step
    return max(minimum, min(100, pct))


class HassAqualinkLight(AqualinkEntity[AqualinkLight], LightEntity):
    """Representation of a light."""

    def __init__(
        self, coordinator: AqualinkDataUpdateCoordinator, dev: AqualinkLight
    ) -> None:
        """Initialize AquaLink light."""
        super().__init__(coordinator, dev)
        if dev.supports_effect:
            self._attr_effect_list = list(dev.supported_effects)
            self._attr_supported_features = LightEntityFeature.EFFECT
        color_mode = ColorMode.ONOFF
        if dev.supports_brightness:
            color_mode = ColorMode.BRIGHTNESS
        self._attr_color_mode = color_mode
        self._attr_supported_color_modes = {color_mode}

    @property
    @override
    def is_on(self) -> bool:
        """Return whether the light is on or off."""
        return self.dev.is_on

    @refresh_system
    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light.

        This handles brightness and light effects for lights that do support
        them.
        """
        # For now I'm assuming lights support either effects or brightness.
        if effect_name := kwargs.get(ATTR_EFFECT):
            await await_or_reraise(
                self.hass,
                self.coordinator.config_entry,
                self.dev.set_effect_by_name(effect_name),
            )
        elif brightness := kwargs.get(ATTR_BRIGHTNESS):
            # Aqualink supports percentages in 25% increments; 0 would mean off.
            pct = _to_percentage(brightness, 25, 25)
            await await_or_reraise(
                self.hass, self.coordinator.config_entry, self.dev.set_brightness(pct)
            )
        else:
            await await_or_reraise(
                self.hass, self.coordinator.config_entry, self.dev.turn_on()
            )

    @refresh_system
    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        await await_or_reraise(
            self.hass, self.coordinator.config_entry, self.dev.turn_off()
        )

    @property
    @override
    def brightness(self) -> int:
        """Return current brightness of the light.

        The scale needs converting between 0-100 and 0-255.
        """
        return round(self.dev.brightness * 255 / 100)

    @property
    @override
    def effect(self) -> str | None:
        """Return the current light effect if the system reports one.

        Colour lights report their on/off state ("0"/"1") here, not an effect.
        """
        effect = self.dev.effect
        if self._attr_effect_list and effect in self._attr_effect_list:
            return effect
        return None


class HassAqualinkIclLight(AqualinkEntity[Any], LightEntity, RestoreEntity):
    """An iCL light zone: on/off, dim level, colour presets and custom RGB."""

    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = list(PRESETS)

    dev: IclZone

    @override
    async def async_added_to_hass(self) -> None:
        """Restore what the controller doesn't report."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        extra = await self.async_get_last_extra_data()
        saved = extra.as_dict() if extra is not None else {}
        if last is None and not saved:
            return
        attrs = last.attributes if last is not None else {}
        pct = saved.get(ATTR_SAVED_BRIGHTNESS_PCT)
        if pct is None and (brightness := attrs.get(ATTR_BRIGHTNESS)) is not None:
            pct = round(brightness * 100 / 255)
        rgb = saved.get(ATTR_SAVED_RGB_COLOR) or attrs.get(ATTR_RGB_COLOR)
        if saved and not self.dev.is_on:
            # The first poll fills in the cloud's last lit values; ours win.
            self.dev.remember(None if rgb is None else tuple(rgb), pct)
        self.dev.restore(
            None
            if last is None
            else {STATE_ON: True, STATE_OFF: False}.get(last.state),
            pct,
            None if rgb is None else tuple(rgb),
        )

    @property
    @override
    def extra_restore_state_data(self) -> RestoredExtraData:
        """Keep the colour and brightness across restarts, even while off."""
        return RestoredExtraData(self._saved())

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Show the colour and brightness the next turn-on uses."""
        return self._saved()

    def _saved(self) -> dict[str, Any]:
        rgb = self.dev.rgb
        return {
            ATTR_SAVED_RGB_COLOR: None if rgb is None else list(rgb),
            ATTR_SAVED_BRIGHTNESS_PCT: self.dev.brightness_percentage,
        }

    async def async_set_next_on(
        self, rgb: tuple[int, int, int] | None, pct: int | None
    ) -> None:
        """Change the colour/brightness now if on, else keep it for next turn-on."""
        if rgb is None and pct is None:
            return
        if self.dev.is_on:
            kwargs: dict[str, Any] = {}
            if rgb is not None:
                kwargs[ATTR_RGB_COLOR] = tuple(rgb)
            if pct is not None:
                kwargs[ATTR_BRIGHTNESS] = round(pct * 255 / 100)
            await self.async_turn_on(**kwargs)
            return
        self.dev.remember(None if rgb is None else tuple(rgb), pct)
        self.async_write_ha_state()

    @property
    @override
    def assumed_state(self) -> bool:
        """Return True when the controller doesn't report the zone's state."""
        return super().assumed_state or not self.dev.reports_state

    @property
    @override
    def is_on(self) -> bool | None:
        """Return whether the zone is on."""
        return self.dev.is_on

    @property
    @override
    def brightness(self) -> int | None:
        """Return the dim level on HA's 0-255 scale."""
        pct = self.dev.brightness_percentage
        return None if pct is None else round(pct * 255 / 100)

    @property
    @override
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the zone's colour."""
        return self.dev.rgb

    @property
    @override
    def effect(self) -> str | None:
        """Return the preset matching the current colour."""
        rgb = self.dev.rgb
        return next((name for name, c in PRESETS.items() if c == rgb), None)

    @refresh_system
    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the zone on (app sequence) or change its brightness."""
        pct = None
        if (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
            pct = _to_percentage(brightness, 5, 5)
        rgb = kwargs.get(ATTR_RGB_COLOR)
        if rgb is None and (effect := kwargs.get(ATTR_EFFECT)) in PRESETS:
            rgb = PRESETS[effect]
        if rgb is None and pct is not None and self.dev.is_on:
            await self._run(self.dev.set_brightness(pct))
            return
        await self._run(self.dev.turn_on(rgb, pct))

    @refresh_system
    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the zone off."""
        await self._run(self.dev.turn_off())

    async def _run(self, command: Any) -> None:
        await await_or_reraise(self.hass, self.coordinator.config_entry, command)
