# Jandy iAquaLink for Home Assistant (with iCL lights)

A custom version of Home Assistant's built-in [iAquaLink integration](https://www.home-assistant.io/integrations/iaqualink/), taken from Home Assistant 2026.9.1. It keeps the `iaqualink` domain, so when it's installed it replaces the built-in integration and your existing config entry and entities stay as they are.

## What's different from the built-in integration

- **iCL light zones.** Each Jandy Infinite WaterColors (iCL) zone becomes a `light` entity with on/off, brightness (5% steps), colour presets as effects, and custom RGB colours. Turning a zone on uses the command sequence captured from the iAquaLink app. The built-in integration only shows `binary_sensor.is_icl_present`.
- **Pool & Spa light card.** `custom:iaqualink-pool-light-card` is served by the integration, which adds and updates its Lovelace resource itself (storage-mode resources; with YAML resources it is loaded as extra frontend JS instead).
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

## Cloud or Local (AqualinkD)

By default everything goes through the iAquaLink cloud. If you run [AqualinkD](https://github.com/aqualinkd/AqualinkD) on a Raspberry Pi wired to the panel's RS-485 bus, you can switch to **Local** in **Settings > Devices & services > iAquaLink > Configure**:

- **Connection:** Cloud (iAquaLink) or Local (AqualinkD).
- **AqualinkD address:** e.g. `http://aqualinkd.local` or `http://192.168.1.50`. It is checked before saving.

In Local mode, pumps, aux buttons, heaters, setpoints, temperatures, freeze protection, pH/ORP and the salt cell (`swg_percent`, and salt ppm in `pool_salinity`) come from AqualinkD's HTTP API, polled every 5 seconds. Entities keep the cloud's names and unique IDs, so dashboards and automations keep working when you switch. System status and Service mode read AqualinkD's panel status.

Local mode also adds what AqualinkD's own Home Assistant discovery offers:

- **Salt cell:** settable `swg_set_point` (%), `swg_boost` switch, salt ppm, and SWG status / boost message sensors.
- **Pumps (variable speed):** RPM, GPM, watts, speed % and status sensors per pump, plus an RPM setpoint (vsPump) or GPM setpoint (vfPump).
- **Setpoints:** freeze protection and chiller (if present) as number entities.
- **Lights:** a program select for Jandy colour lights (Off plus the light's shows) and a brightness number for dimmers.
- **Panel:** display message sensor, panel battery-low binary sensor, and a "Sync panel clock" button.
- **Timers:** buttons running on a panel timer carry `timer_active` and `timer_remaining` (seconds) attributes.
- **Extra sensors** configured in AqualinkD appear as sensors.

**iCL light zones in Local mode:** AqualinkD 3.1.1 (dev) and later can drive Jandy Infinite WaterColors lights. Add one virtual button per zone in `aqualinkd.conf` with `light_mode=7`, the zone's `lightID`, and a label matching the zone name in the iAquaLink app (e.g. `Pool Lights`). AqualinkD needs `enable_iaqualink=yes` and an `extended_device_id` of 0x30 to 0x33. When a button's label matches a zone, `light.<zone>` sends on/off, RGB and brightness to AqualinkD, follows AqualinkD's on/off state, and pauses the cloud watchdog for that zone. If AqualinkD refuses or is down, the command falls back to the cloud's captured iCL sequence. Zones with no matching button stay on the cloud, so keep the iAquaLink credentials in place.

**Local mode behaviour:**

- **Instant updates:** the integration listens to AqualinkD's websocket and refreshes as soon as anything changes. It polls every 60 s as a safety net, or every 5 s while the websocket is down.
- **Cloud fallback:** while AqualinkD doesn't answer, each equipment entity uses the matching cloud device, so pumps and heaters stay available and controllable. After 3 failed polls a repair issue appears; it clears when AqualinkD is back. If AqualinkD is down when HA starts, the entry starts on the cloud and reloads itself once AqualinkD answers.
- **Less cloud traffic:** when AqualinkD drives every light zone, the cloud is polled every 5 minutes instead of every 15 s. It goes back to 15 s during a fallback.
- **Energy:** each variable-speed pump gets an `..._energy` kWh sensor, summed from its watts, for the Energy dashboard. Numeric readings have `state_class: measurement` for long-term statistics.

Turn off AqualinkD's own MQTT discovery for Home Assistant, otherwise HA gets a second set of entities for the same equipment.

Local mode has only been tested against a simulated AqualinkD. Device ids that differ on a real panel (for example `Extra_Aux`, `Aux_V*`) may need mapping.

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

## What this fork adds (release history)

| Release | What changed |
|---|---|
| v1.0.0 | Fork of HA 2026.9.1's `iaqualink`. Native iCL light zones (`light.pool_lights`, `light.spa_lights`) using the captured turn-on sequence (off, 2 s, on, 1 s, custom colour), colour presets and brightness. Bundled Pool & Spa light card. Fixes from a review of the built-in integration. |
| v1.1.0 | iCL watchdog: re-sends a zone that didn't follow, and pauses while the cloud or controller is offline or in Service mode. |
| v1.1.1 | Each zone keeps its colour and brightness across restarts. The `iaqualink.set_next_on` service sets the next turn-on colour without lighting the zone. |
| v1.2.0 | `System status` sensor (Online / Offline / Service) and `Service mode` binary sensor. |
| v2.0.0 | Cloud / Local (AqualinkD) connection option. Local mode keeps the same entity IDs and adds salt ppm and SWG %. |
| v2.1.0 | Local parity with AqualinkD's HA discovery: SWG setpoint and Boost, pump RPM/GPM/watts with setpoints, freeze and chiller setpoints, light programs, dimmers, display message, panel battery, clock sync, timers. |
| v2.2.0 | Local mode can drive the iCL zones through AqualinkD Infinite WaterColors buttons, falling back to the cloud. |
| v2.2.1 | The light card registers itself as a Lovelace resource, fixing "Custom element doesn't exist". |
| v2.3.0 | AqualinkD websocket push, automatic cloud fallback, repair issues, long-term statistics and pump energy (kWh), slower cloud polling when the lights are local, raw cloud data in diagnostics, CI. |

**Waiting on hardware (AqualinkD Radxa kit not yet wired):** everything in Local mode has only been tested against a simulated AqualinkD. Still to do once it's connected:

- map the panel's real device ids;
- read each iCL zone's `lightID` with `rs485mon`;
- check that `enable_iaqualink` works alongside the iAquaLink 3.0 adapter;
- test pumps, heaters and lights on the real equipment.

**Known next step:** the cloud sends the salt cell output (`swc_info`: `swcPoolValue`, `swcPoolStatus`) and the iaqualink library drops it. A cloud SWG % sensor can read it from there.

## Development

```bash
python3.14 -m venv .venv && .venv/bin/pip install pytest-homeassistant-custom-component ruff
.venv/bin/pip install "iaqualink @ git+https://github.com/flz/iaqualink-py@v0.7.0"
.venv/bin/python -m pytest
.venv/bin/ruff check custom_components tests
```

## License

Apache License 2.0, like Home Assistant, which this code comes from (see `LICENSE.md`). Not affiliated with Jandy, Zodiac or Fluidra.
