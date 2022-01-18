"""The Daikin Altherma integration."""
from __future__ import annotations

from datetime import timedelta
import logging

from aiohttp import ClientConnectionError
from pyaltherma.comm import DaikinWSConnection
from pyaltherma.controllers import AlthermaController

from homeassistant.components.water_heater import STATE_OFF, STATE_ON, STATE_PERFORMANCE
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.util import Throttle

from .const import DOMAIN

PLATFORMS = ["water_heater", "sensor", "switch", "select", "number"]
MIN_TIME_BETWEEN_UPDATES = timedelta(seconds=10)
_LOGGER = logging.getLogger(__name__)


async def setup_api_instance(hass, host):
    session = hass.helpers.aiohttp_client.async_get_clientsession()
    conn = DaikinWSConnection(session, host)
    device = AlthermaController(conn)
    await device.discover_units()
    api = AlthermaAPI(device)
    await api.api_init()
    return api


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Daikin Altherma from a config entry."""
    conf = entry.data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = api = await setup_api_instance(
        hass, conf[CONF_HOST]
    )
    hass.data[DOMAIN][entry.entry_id] = api

    hass.config_entries.async_setup_platforms(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


class AlthermaAPI:
    def __init__(self, device: AlthermaController) -> None:
        """Initialize the Daikin Handle."""
        self._device = device
        self.host = device.ws_connection.host
        self._status = None
        self._info = None
        self._available = True
        self._hwt_device_info = None
        self._space_heating_device_info = None

        self._climate_control_powered = False

    async def turn_on_climate_control(self):
        await self._device.climate_control.turn_on()
        self._climate_control_powered = True

    async def turn_off_climate_control(self):
        await self._device.climate_control.turn_off()
        self._climate_control_powered = False

    async def async_is_climate_control_on(self):
        self._climate_control_powered = await self._device.climate_control.is_turned_on
        return self.is_climate_control_on()

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
        return self._status["function/DomesticHotWaterTank"]

    @property
    def space_heating_status(self):
        return self._status["function/SpaceHeating"]

    @property
    def device(self) -> AlthermaController:
        return self._device

    async def api_init(self):
        self._status = await self.device.get_current_state()
        self._info = await self.device.device_info()
        self._climate_control_powered = await self._device.climate_control.is_turned_on
        await self.get_HWT_device_info()
        await self.get_space_heating_device_info()
        await self._device.ws_connection.close()

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    async def async_update(self, **kwargs):
        """Pull the latest data from Daikin."""
        try:
            self._status = await self.device.get_current_state()
            self._climate_control_powered = await self._device.climate_control.is_turned_on
            await self.device.ws_connection.close()
            self._available = True
        except ClientConnectionError:
            _LOGGER.warning("Connection failed for %s", self.ip_address)
            self._available = False

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
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
            self._hwt_device_info = DeviceInfo(
                **{
                    "identifiers": {(DOMAIN, f"{self._info['serial_number']} Hot Water Tank")},
                    # "name": self._info["duty"],
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
            self._space_heating_device_info = DeviceInfo(
                **{
                    "identifiers": {(DOMAIN, f"{self._info['serial_number']} Space Heating")},
                    # "name": self._info["duty"],
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
        status = self.water_tank_status
        ops = status["operations"]
        is_on = ops["Power"] == "on"
        # First check if it is on and if yes then check whatever it is in powerful mode
        if is_on:
            state = ops["powerful"]
            if state == 0:
                return STATE_ON
            else:
                return STATE_PERFORMANCE
        else:
            return STATE_OFF

    async def async_set_water_tank_state(self, state):
        """
        Sets new hot water tank state. It can be off / on / powerful
        @param state: string
        @return: Nothing
        """
        if state == STATE_OFF:
            await self.device.hot_water_tank.set_powerful(False)
            await self.device.hot_water_tank.turn_off()
        elif state == STATE_ON:
            await self.device.hot_water_tank.turn_on()
            await self.device.hot_water_tank.set_powerful(False)
        else:
            await self.device.hot_water_tank.turn_on()
            await self.device.hot_water_tank.set_powerful(True)
        await self.device.ws_connection.close()

    @property
    def water_tank_target_temp_config(self) -> dict:
        """
        Returns the configuration values for target temperature.
        Normally it should have the maximum, minimum and step numbers
        @rtype: dict
        """

        if "DomesticHotWaterTemperatureHeating" in self.device.hot_water_tank._unit.operation_config:
            return self.device.hot_water_tank._unit.operation_config["DomesticHotWaterTemperatureHeating"]

        return self.device.hot_water_tank._unit.operation_config["TargetTemperature"][
            "heating"
        ]
