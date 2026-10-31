# Jandy iAquaLink for Home Assistant (with iCL lights)

A custom version of Home Assistant's built-in [iAquaLink integration](https://www.home-assistant.io/integrations/iaqualink/), taken from Home Assistant 2026.9.1. It keeps the `iaqualink` domain, so when it's installed it replaces the built-in integration and your existing config entry and entities stay as they are.

## What's different from the built-in integration

- **iCL light zones.** Each Jandy Infinite WaterColors (iCL) zone becomes a `light` entity with on/off, brightness (5% steps), colour presets as effects, and custom RGB colours. Turning a zone on uses the command sequence captured from the iAquaLink app. The built-in integration only shows `binary_sensor.is_icl_present`.
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

## System status

Each iAqua system gets a **System status** sensor (`Online`, `Offline` or `Service`, as the iAquaLink app's home screen reports it; also `Offline` when the cloud can't be reached) and a **Service mode** binary sensor. Both stay available while the system is down, so dashboards can show the outage.

## iCL lights

Zones are named after the zone names in the iAquaLink app; with zones called "Pool" and "Spa" you get `light.pool` and `light.spa`.

The controller only lights a zone reliably with the sequence the iAquaLink app sends, so every turn-on uses it:

| Service call | What is sent |
| --- | --- |
| `light.turn_on` | `onoff_iclzone` off, wait 2 s, `onoff_iclzone` on, wait 1 s, `define_iclzone_customcolor` with the zone's last colour (turquoise if none) |
| `light.turn_on` with `rgb_color`, or `effect` (a preset such as `Blue`) | the same sequence with that colour |
| `light.turn_on` with `brightness`/`brightness_pct` while on | `set_iclzone_color` with only `dim_level` (keeps the colour) |
| `light.turn_on` with `brightness` while off | the sequence, wait 1.5 s, then the dim level |
| `light.turn_off` | `onoff_iclzone` off |

Zone state comes from the `icl_info_list` in get_devices, or from `get_icl_info` (at most once a minute) when the controller doesn't include it there. For 2 minutes after a command the zone keeps the commanded state, because the cloud reports changes late. A zone the controller reports as `absent` (often the spa) is still controlled; it shows its commanded state as an assumed state and keeps it across restarts.

Each zone keeps its colour and brightness across restarts, also while it's off, and shows them as the `saved_rgb_color` and `saved_brightness_pct` attributes. A plain `light.turn_on` (voice, Apple Home, scenes) comes on in them and sends the saved brightness after the sequence. While a zone is off, the cloud's colour and dim level reports are ignored, because they repeat the last lit values.

To pick the colour or brightness a zone comes on in without turning it on, use the `iaqualink.set_next_on` action (`rgb_color`, `brightness_pct`). If the zone is on, it changes straight away instead.

The Aux relay that powers the lights (for example `switch.pool_light`) is still a separate switch.

### Watchdog

Every 15 minutes the integration checks each zone that reports its state. If a zone isn't in the state it was last told to be in (or restored to after a restart), the command is sent again: the full turn-on sequence plus the zone's brightness, or off. 15 s later the zone is read back.

- **No retries while the system is down.** Nothing is sent while the system is unavailable or offline (for example when the pool's Wi-Fi is shaky), or while the home screen reports a status other than `Online`, such as `Service` (Service mode). If the system drops out while a retry is running, the retry isn't counted as failed.
- **Resuming:** once the system is back online, the next check runs after one minute.
- **Alerts:** after two failed checks in a row a persistent notification is raised; it is dismissed once the zone is back in sync. Each resend fires an `iaqualink_icl_watchdog` event (`zone`, `desired`, `result`: `fixed` or `failed`, `failures`) that automations can use, for example to send a phone notification.
- Zones reported `absent` (often the spa) can't be checked, so the watchdog leaves them alone.

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
4. Once you're happy with it, turn off the PyScript light automations and watchdog (the integration has its own), and remove the old card resource. Running both watchdogs would send every retry twice.

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
