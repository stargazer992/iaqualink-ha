"""Support for Aqualink pool lights."""

from typing import Any, override

from iaqualink.device import AqualinkLight

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AqualinkConfigEntry, refresh_system
from .coordinator import AqualinkDataUpdateCoordinator
from .entity import AqualinkEntity
from .icl import CUSTOM_COLOR_ID, EFFECTS, IclZone
from .utils import await_or_reraise

PARALLEL_UPDATES = 1


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


class HassAqualinkIclLight(AqualinkEntity[Any], LightEntity):
    """An iCL light zone: on/off, dim level, Jandy colours and custom RGB."""

    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = list(EFFECTS)

    dev: IclZone

    @property
    @override
    def is_on(self) -> bool:
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
    def effect(self) -> str | None:
        """Return the active Jandy colour/show."""
        return self.dev.effect

    @property
    @override
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the custom colour while it is active."""
        return self.dev.rgb if self.dev.color_id == CUSTOM_COLOR_ID else None

    @refresh_system
    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the zone on and apply colour, show and brightness."""
        pct = None
        if (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
            pct = _to_percentage(brightness, 5, 5)
        if (rgb := kwargs.get(ATTR_RGB_COLOR)) is not None:
            await self._run(self.dev.set_rgb(*rgb))
            if pct is not None:
                await self._run(self.dev.set_brightness(pct))
            return
        if not self.dev.is_on:
            await self._run(self.dev.turn_on())
        if effect := kwargs.get(ATTR_EFFECT):
            await self._run(self.dev.set_effect(effect, pct))
        elif pct is not None:
            await self._run(self.dev.set_brightness(pct))

    @refresh_system
    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the zone off."""
        await self._run(self.dev.turn_off())

    async def _run(self, command: Any) -> None:
        await await_or_reraise(self.hass, self.coordinator.config_entry, command)
