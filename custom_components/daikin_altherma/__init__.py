"""The Daikin Altherma integration."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from inspect import signature
from math import isfinite

from aiohttp import ClientError
from homeassistant.components.water_heater import STATE_OFF, STATE_ON, STATE_PERFORMANCE
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pyaltherma.controllers import AlthermaController
from pyaltherma.errors import AlthermaException

from .connection import DaikinWSConnection, async_close_connection
from .const import (
    ASYNC_UPDATE_TIMEOUT_SECONDS,
    DOMAIN,
    TIMEOUT,
    UPDATE_INTERVAL_SECONDS,
)

PLATFORMS = ["water_heater", "sensor", "switch", "select", "number", "binary_sensor"]
_LOGGER = logging.getLogger(__name__)
API_ERRORS = (
    ClientError,
    TimeoutError,
    OSError,
    AlthermaException,
    ValueError,
    TypeError,
    KeyError,
)


@dataclass
class AlthermaEntryData:
    api: AlthermaAPI
    coordinator: DataUpdateCoordinator


async def setup_api_instance(hass, host):
    conn = DaikinWSConnection(async_get_clientsession(hass), host)
    try:
        async with asyncio.timeout(TIMEOUT):
            device = AlthermaController(conn)
            await device.discover_units()
            api = AlthermaAPI(device)
            await api.api_init()
            return api
    finally:
        await async_close_connection(conn)


def create_update_function(api):
    async def async_update_data():
        try:
            await api.async_update()
        except API_ERRORS as err:
            raise UpdateFailed(f"Unable to update Daikin at {api.host}: {err}") from err
        return api.status

    return async_update_data


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a separately coordinated connection for each adapter."""
    try:
        api = await setup_api_instance(hass, entry.data[CONF_HOST])
    except API_ERRORS as err:
        raise ConfigEntryNotReady(
            f"Unable to initialize Daikin at {entry.data[CONF_HOST]} "
            f"({type(err).__name__}): {err}. Check the adapter address, "
            "network access and local API support; see README troubleshooting."
        ) from err

    # Older HA releases obtain the entry from the setup context instead.
    coordinator_options = {}
    if "config_entry" in signature(DataUpdateCoordinator).parameters:
        coordinator_options["config_entry"] = entry
    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"daikin_altherma_{entry.entry_id}",
        update_method=create_update_function(api),
        update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        **coordinator_options,
    )
    try:
        await coordinator.async_config_entry_first_refresh()
    except BaseException:
        await coordinator.async_shutdown()
        await api.async_close()
        raise
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = AlthermaEntryData(
        api, coordinator
    )
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await coordinator.async_shutdown()
        await api.async_close()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload entities before closing their adapter connection."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    data = hass.data[DOMAIN].pop(entry.entry_id)
    await data.coordinator.async_shutdown()
    await data.api.async_close()
    return True


class AlthermaAPI:
    def __init__(self, device: AlthermaController) -> None:
        self._device = device
        self.host = device.ws_connection.host
        self._status = {}
        self._info = None
        self._available = False
        self._hwt_device_info = None
        self._space_heating_device_info = None
        self._climate_control_powered = None
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self, timeout=ASYNC_UPDATE_TIMEOUT_SECONDS):
        """Serialize complete reads and commands, including connection cleanup."""
        async with self._lock:
            try:
                async with asyncio.timeout(timeout):
                    yield
            except BaseException:
                self._available = False
                raise
            finally:
                await async_close_connection(self.device.ws_connection)

    async def async_close(self):
        async with self._lock:
            await async_close_connection(self.device.ws_connection)

    async def async_execute(self, method, *args, **kwargs):
        """Run a command with the same lock and timeout as polling."""
        try:
            async with self.transaction():
                return await method(*args, **kwargs)
        except API_ERRORS as err:
            raise HomeAssistantError(f"Daikin command failed: {err}") from err

    async def turn_on_climate_control(self):
        await self.async_execute(self._device.climate_control.turn_on)

    async def turn_off_climate_control(self):
        await self.async_execute(self._device.climate_control.turn_off)

    def is_climate_control_on(self):
        return self._climate_control_powered

    @property
    def status(self):
        return self._status

    @property
    def info(self):
        return self._info

    @property
    def water_tank_status(self):
        return self._status.get(
            "function/DomesticHotWaterTank",
            self._status.get("function/DomesticHotWater", {}),
        )

    @property
    def water_tank_current_temperature(self):
        """Return a known water-temperature reading from the cached snapshot."""
        status = self.water_tank_status
        sensors = status.get("sensors", {})
        for key in ("TankTemperature", "SensorTemperature"):
            if key in sensors:
                return temperature_value(sensors[key])
        return temperature_value(status.get("operations", {}).get("SensorTemperature"))

    @property
    def water_tank_target_temperature(self):
        key, _ = self.water_tank_temperature_operation
        return temperature_value(self.water_tank_status.get("operations", {}).get(key))

    @property
    def space_heating_status(self):
        return self._status.get("function/SpaceHeating", {})

    @property
    def device(self) -> AlthermaController:
        return self._device

    async def api_init(self):
        async with self.transaction(TIMEOUT):
            self._info = await self.device.device_info()
            await self.get_HWT_device_info()
            await self.get_space_heating_device_info()

    def get_state(self, state_key, status=None):
        status = self._status if status is None else status
        return any(
            unit.get("states", {}).get(state_key, False) for unit in status.values()
        )

    async def refresh_unit(self):
        await self.device.refresh()

    async def async_update(self):
        """Publish a complete snapshot only after a successful read."""
        async with self.transaction():
            status = await self.device.get_current_state()
            self._validate_status(status)
            if self.get_state("InstallerState") and not self.get_state(
                "InstallerState", status
            ):
                await self.refresh_unit()
                status = await self.device.get_current_state()
                self._validate_status(status)
            power = (
                status.get("function/SpaceHeating", {})
                .get("operations", {})
                .get("Power")
            )
            self._climate_control_powered = None if power is None else power == "on"
            self._status = status
            self._available = True

    @staticmethod
    def _validate_status(status):
        if not isinstance(status, dict) or not status:
            raise ValueError("Device returned no unit status")
        for unit in status.values():
            if not isinstance(unit, dict):
                raise TypeError("Invalid unit status")
            for section in ("operations", "states", "sensors"):
                if not isinstance(unit.get(section, {}), dict):
                    raise TypeError(f"Invalid {section} status")

    @property
    def available(self) -> bool:
        return self._available

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            **{
                "identifiers": {(DOMAIN, self._info["serial_number"])},
                "name": self._info["duty"],
                "manufacturer": self._info["manufacturer"],
                "model": self._info["model_name"],
                "sw_version": self._info["firmware"],
            }
        )

    async def get_HWT_device_info(self) -> DeviceInfo:
        if self._hwt_device_info is None:
            hwt = self.device.hot_water_tank
            if hwt is None:
                return None

            self._hwt_device_info = DeviceInfo(
                **{
                    "identifiers": {
                        (DOMAIN, f"{self._info['serial_number']} Hot Water Tank")
                    },
                    "name": "Hot Water Tank",
                    "manufacturer": self._info["manufacturer"],
                    "model": await hwt.model_number,
                    "sw_version": f"{await hwt.indoor_software}/{await hwt.outdoor_software}",
                }
            )
        return self._hwt_device_info

    async def get_space_heating_device_info(self) -> DeviceInfo:
        if self._space_heating_device_info is None:
            space_heating = self.device.climate_control
            if space_heating is None:
                return None

            self._space_heating_device_info = DeviceInfo(
                **{
                    "identifiers": {
                        (DOMAIN, f"{self._info['serial_number']} Space Heating")
                    },
                    "name": "Space Heating",
                    "manufacturer": self._info["manufacturer"],
                    "model": await space_heating.model_number,
                    "sw_version": f"{await space_heating.indoor_software}/{await space_heating.outdoor_software}",
                }
            )
        return self._space_heating_device_info

    @property
    def HWT_device_info(self):
        return self._hwt_device_info

    @property
    def space_heating_device_info(self):
        return self._space_heating_device_info

    @property
    def water_tank_operation(self):
        ops = self.water_tank_status.get("operations", {})
        if "Power" not in ops:
            return None
        if ops["Power"] != "on":
            return STATE_OFF
        powerful = ops.get("powerful", ops.get("Powerful", 0))
        return STATE_PERFORMANCE if str(powerful).lower() in ("1", "true") else STATE_ON

    def hwt_powerful_support(self):
        tank = self.device.hot_water_tank
        return tank is not None and any(
            x.lower() == "powerful" for x in tank.operations
        )

    async def async_set_water_tank_state(self, state):
        tank = self.device.hot_water_tank
        if tank is None or state not in (STATE_OFF, STATE_ON, STATE_PERFORMANCE):
            raise HomeAssistantError("Unsupported water heater operation")
        if state == STATE_PERFORMANCE and not self.hwt_powerful_support():
            raise HomeAssistantError("Powerful mode is not supported")

        async def set_state():
            if state == STATE_OFF:
                if self.hwt_powerful_support():
                    await tank.set_powerful(False)
                await tank.turn_off()
            else:
                await tank.turn_on()
                if self.hwt_powerful_support():
                    await tank.set_powerful(state == STATE_PERFORMANCE)

        await self.async_execute(set_state)

    @property
    def water_tank_temperature_operation(self):
        tank = self.device.hot_water_tank
        if tank is None or tank.unit is None:
            return None, {}
        schema = tank.unit.operation_config
        for key in ("DomesticHotWaterTemperatureHeating", "TargetTemperature"):
            config = schema.get(key)
            if isinstance(config, dict):
                if isinstance(config.get("heating"), dict):
                    config = {**config, **config["heating"]}
                return key, config
        return None, {}

    @property
    def water_tank_target_temp_config(self) -> dict:
        return self.water_tank_temperature_operation[1]


def validate_temperature(value, config):
    """The dependency logs invalid values but still sends them; reject them here."""
    if not config or not config.get("settable", True):
        raise HomeAssistantError("Temperature is not writable in the current mode")
    try:
        value = float(value)
        minimum, maximum = float(config["minValue"]), float(config["maxValue"])
    except (TypeError, ValueError, KeyError) as err:
        raise HomeAssistantError(
            "Temperature or device temperature limits are invalid"
        ) from err
    if not isfinite(value) or not minimum <= value <= maximum:
        raise HomeAssistantError(f"Temperature must be between {minimum} and {maximum}")
    return value


def temperature_value(value):
    """Treat absent, placeholder and non-finite temperature readings as unknown."""
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (ValueError, TypeError):
        return None
    return value if isfinite(value) else None
