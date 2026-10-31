# Jandy iAquaLink for Home Assistant (with iCL lights)

A custom version of Home Assistant's built-in [iAquaLink integration](https://www.home-assistant.io/integrations/iaqualink/), taken from Home Assistant 2026.9.1. It keeps the `iaqualink` domain, so when it's installed it replaces the built-in integration and your existing config entry and entities stay as they are.

## What's different from the built-in integration

- **iCL light zones.** Each Jandy Infinite WaterColors (iCL) zone becomes a `light` entity with on/off, brightness (5% steps), the 15 Jandy colours and shows as effects, and custom RGB colours. The built-in integration only shows `binary_sensor.is_icl_present`.
- **Pool & Spa light card.** `custom:iaqualink-pool-light-card` is served and loaded by the integration, so you don't need a Lovelace resource for it.
- **Fixes:**
  - EXO systems show the new state right after a command instead of after the next poll (up to about a minute).
  - A low brightness no longer switches a dimmable light off; it goes to 25%.
  - Colour lights no longer show "0"/"1" as the current effect.
  - Thermostats round the target temperature instead of truncating it, and use whole-degree steps.
  - A heater state the integration doesn't know no longer breaks the thermostat entity.
  - An unreachable system no longer stops the other systems on the account from loading; it is retried after 5 minutes.
  - Reauthentication and reconfiguration reject credentials for a different account.
  - Diagnostics also redact account details (IDs, owner, address, email, location).
  - Commands are sent one at a time, so a scene doesn't hit the rate-limited cloud with all of its commands at once.

## Install

1. In HACS, open **Custom repositories** and add `https://github.com/stargazer992/iaqualink-ha` with type **Integration**.
2. Install **Jandy iAquaLink (iCL lights)** and restart Home Assistant.
3. If iAquaLink is already set up, nothing else is needed. Otherwise, add it under **Settings > Devices & services**.

## iCL lights

Zones are named after the zone names in the iAquaLink app; with zones called "Pool" and "Spa" you get `light.pool` and `light.spa`.

| Service call | What is sent |
| --- | --- |
| `light.turn_on` | `onoff_iclzone` (only if the zone is off) |
| `light.turn_off` | `onoff_iclzone` |
| `light.turn_on` with `brightness`/`brightness_pct` | `set_iclzone_color` with `dim_level` |
| `light.turn_on` with `effect` (e.g. `Caribbean Blue`) | `set_iclzone_color` with `color_id` and the current dim level |
| `light.turn_on` with `rgb_color` | off, wait 2 s, on, wait 1 s, `define_iclzone_customcolor` (the iAquaLink app's sequence) |

The Aux relay that powers the lights (for example `switch.pool_light`) is still a separate switch.

## Pool & Spa light card

```yaml
type: custom:iaqualink-pool-light-card
pool_entity: light.pool   # optional, this is the default
spa_entity: light.spa     # optional, this is the default
```

It has the same look and controls as `fleur-pool-light-card` v1.2.0, but it drives the light entities above instead of PyScript services:
- Presets are sent as RGB colours.
- **Sync from iAqualink** refreshes the light entities.
- The "Stays on" timer in the power pop-up still needs `fleur-popup.js` and an auto-off automation that knows the light entity.

## Moving from the PyScript light control

1. Install this integration and restart.
2. Check that `light.pool` and `light.spa` show the right state. Turn one on and off from the entity dialog.
3. In the dashboard, change `type: custom:fleur-pool-light-card` to `type: custom:iaqualink-pool-light-card`. Add `pool_entity`/`spa_entity` if your zones have other names.
4. Once you're happy with it, turn off the PyScript light automations and remove the old card resource.

You can go back at any time by removing this integration from HACS and restarting; the built-in integration takes over again.

## Development

```bash
python3.14 -m venv .venv && .venv/bin/pip install pytest-homeassistant-custom-component ruff
.venv/bin/pip install "iaqualink @ git+https://github.com/flz/iaqualink-py@v0.7.0"
.venv/bin/python -m pytest
.venv/bin/ruff check custom_components tests
```

## License

Apache License 2.0, like Home Assistant, which this code comes from (see `LICENSE.md`). Not affiliated with Jandy, Zodiac or Fluidra.
