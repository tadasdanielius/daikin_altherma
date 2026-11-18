# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Home Assistant custom integration (distributed via HACS) for Daikin Altherma heat pumps through the BRP069A61/A62 LAN adapter (also tested with D2CND + DRGATEWAYAA). Local polling only, no cloud. All device communication goes through the external library `pyaltherma` (version pinned in `custom_components/daikin_altherma/manifest.json`); protocol/websocket fixes usually belong there, not here.

## Commands

There is no build step, test suite, linter config, or local dev tooling in this repo. Validation runs only in GitHub Actions (on release or manual dispatch):
- `.github/workflows/hassfest.yml` — Home Assistant `hassfest` manifest/structure validation
- `.github/workflows/validate.yml` — HACS validation

To exercise changes, copy `custom_components/daikin_altherma/` into a Home Assistant config's `custom_components/` and restart HA. Quick syntax check: `python -m py_compile custom_components/daikin_altherma/*.py`.

When releasing, bump `version` in `manifest.json`.

## Architecture

- `__init__.py` — entry setup and the `AlthermaAPI` wrapper around `pyaltherma.controllers.AlthermaController`. `async_setup_entry` connects (`DaikinWSConnection` → `AlthermaController.discover_units()`), builds `AlthermaAPI`, creates one `DataUpdateCoordinator` whose update calls `AlthermaAPI.async_update` (throttled, swallows errors and flips `available` instead of raising), then forwards to `PLATFORMS`.
- `hass.data[DOMAIN][entry.entry_id]` holds the `AlthermaAPI`; the coordinator is stored at `hass.data[DOMAIN]['coordinator']` — a single shared key, not per entry.
- Platform files (`sensor`, `switch`, `select`, `number`, `binary_sensor`, `water_heater`) each define `async_setup_entry` that pulls `api` + `coordinator` from `hass.data`, inspects the discovered device capabilities (e.g. `api.device.climate_control`, `api.device.hot_water_tank`, `unit.operations`, `unit.consumptions`, `altherma_units`) and only creates entities the unit supports. Entities subclass `<Platform>Entity, CoordinatorEntity` and read state from `api.status` (raw dict keyed by `function/SpaceHeating`, `function/DomesticHotWaterTank` or `function/DomesticHotWater`).
- Writes call `pyaltherma` controller methods (e.g. `controller.call_operation(...)`), then `coordinator.async_request_refresh()`. The websocket is explicitly closed after operations (`api.device.ws_connection.close()`) — keep this pattern when adding writes or reads.
- Device registry: `AlthermaAPI` exposes separate `DeviceInfo` for the main unit, the hot water tank (`HWT_device_info`) and space heating (`space_heating_device_info`); entities attach to the relevant one. Unique IDs are derived from `api.info['serial_number']`.
- Hot water may appear as either `DomesticHotWaterTank` or `DomesticHotWater` depending on model, and config keys differ across firmwares (e.g. `DomesticHotWaterTemperatureHeating` vs `TargetTemperature.heating`) — handle both when touching DHW code.
- `config_flow.py` supports manual host entry and zeroconf discovery (`_daikin._tcp.local.`); unique ID is the device serial number. UI strings live in `strings.json` and `translations/en.json` (keep in sync).
- Tunables (poll interval, throttle, timeouts) are in `const.py`.
