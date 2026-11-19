"""The Daikin Altherma integration."""
from __future__ import annotations

import json
import logging
from datetime import timedelta

import async_timeout
from aiohttp import ClientConnectionError, ClientError, ServerTimeoutError
from homeassistant.components.water_heater import STATE_OFF, STATE_ON, STATE_PERFORMANCE
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import Throttle
from pyaltherma.comm import DaikinWSConnection
from pyaltherma.controllers import AlthermaController
from pyaltherma.errors import AlthermaException

from .const import DOMAIN, MIN_TIME_BETWEEN_UPDATES_SECONDS, UPDATE_INTERVAL_SECONDS, ASYNC_UPDATE_TIMEOUT_SECONDS, \
    MAX_UPDATE_FAILED

PLATFORMS = ["water_heater", "sensor", "switch", "select", "number", "binary_sensor"]
MIN_TIME_BETWEEN_UPDATES = timedelta(seconds=MIN_TIME_BETWEEN_UPDATES_SECONDS)
_LOGGER = logging.getLogger(__name__)


async def setup_api_instance(hass, host):
    session = async_get_clientsession(hass)
    conn = DaikinWSConnection(session, host)
    device = AlthermaController(conn)
    await device.discover_units()

    unit_profiles = device.profiles

    # Uncomment this if you want to store profile info in json files.
    # try:
    #    import os
    #    import json
    #    for profile in unit_profiles:
    #        filepath: str = os.path.join(hass.config.config_dir, f'daikin_altherma_{profile["idx"]}.json')
    #        with open(filepath, 'w') as f:
    #            f.write(json.dumps(profile['profile']))
    # except Exception as e:
    #    _LOGGER.warning(f'Failed to save profile state to file {filepath}. It does not affect the operation of the integration.', exc_info=True)

    api = AlthermaAPI(device)
    await api.api_init()
    return api


def create_update_function(api):
    _api = api

    async def async_update_data():
        try:
            async with async_timeout.timeout(ASYNC_UPDATE_TIMEOUT_SECONDS):
                await _api.async_update()
        except TimeoutError:
            _api.mark_unavailable(f'no response within {ASYNC_UPDATE_TIMEOUT_SECONDS}s')
            raise

    return async_update_data


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Daikin Altherma from a config entry."""
    conf = entry.data
    hass.data.setdefault(DOMAIN, {})
    try:
        api = await setup_api_instance(hass, conf[CONF_HOST])
    except (ClientError, TimeoutError, AlthermaException) as error:
        # HA retries setup with backoff instead of leaving the entry failed
        raise ConfigEntryNotReady(f"Daikin adapter at {conf[CONF_HOST]} not reachable: {error}") from error
    hass.data[DOMAIN][entry.entry_id] = api
    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="daikin_altherma_coordinator",
        update_method=create_update_function(api),
        update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
    )
    await coordinator.async_refresh()
    hass.data[DOMAIN]['coordinator'] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

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
        self._failed_updates = 0
        self._type_error_failure = 0

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
        if "function/DomesticHotWaterTank" in self._status:
            return self._status["function/DomesticHotWaterTank"]
        if "function/DomesticHotWater" in self._status:
            return self._status["function/DomesticHotWater"]

    @property
    def space_heating_status(self):
        return self._status["function/SpaceHeating"]

    @property
    def device(self) -> AlthermaController:
        return self._device

    async def _read_status(self):
        status = await self.device.get_current_state()
        # Fault code ("" when no fault) is not part of pyaltherma's unit state
        for unit_function, controller in self.device.altherma_units.items():
            status[unit_function]['error'] = await controller.read('Error')
        for controller in (self.device.climate_control, self.device.hot_water_tank):
            if controller is not None:
                status[controller.unit_function]['next_schedule'] = await self._read_schedule_data(
                    controller, 'Next')
                status[controller.unit_function]['schedule'] = await self._read_active_schedule(controller)
        return status

    @staticmethod
    async def _read_schedule_data(controller, prop):
        # Schedule values are JSON strings wrapped as {"data": ...}
        raw = await controller.read('Schedule', prop)
        try:
            return json.loads(raw)['data']
        except (TypeError, ValueError, KeyError):
            return None

    def unit_profile(self, unit_function):
        for profile in self.device.profiles:
            if profile['label'] == unit_function:
                return profile['profile']
        return None

    async def _read_active_schedule(self, controller):
        # Active: {"path": ".../schedule/List/Heating/la", "id": n}; List: one entry string per schedule
        active = await self._read_schedule_data(controller, 'Active')
        if active is None:
            return None
        kind = active['path'].rstrip('/').split('/')[-2]
        entries = await self._read_schedule_data(controller, f'List/{kind}')
        if entries is None:
            return None
        return {'active': active, 'entries': entries}

    async def api_init(self):
        self._status = await self._read_status()
        self._info = await self.device.device_info()
        if self._device.climate_control is not None:
            self._climate_control_powered = await self._device.climate_control.is_turned_on
        else:
            self._climate_control_powered = False

        await self.get_HWT_device_info()
        await self.get_space_heating_device_info()
        await self._device.ws_connection.close()

    def get_state(self, state_key):
        if self.status is not None:
            state = False
            if 'function/DomesticHotWaterTank' in self.status:
                dhw_states = self.status['function/DomesticHotWaterTank']['states']
                if 'InstallerState' in dhw_states:
                    state = state | dhw_states['InstallerState']
            elif 'function/DomesticHotWater' in self.status:
                dhw_states = self.status['function/DomesticHotWater']['states']
                if 'InstallerState' in dhw_states:
                    state = state | dhw_states['InstallerState']
            else:
                state = False

            if 'function/SpaceHeating' in self.status:
                sh_states = self.status['function/SpaceHeating']['states']
                if 'InstallerState' in sh_states:
                    state = state | sh_states['InstallerState']

            return state
        return None

    async def refresh_unit(self):
        await self.device.refresh()

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    async def async_update(self, **kwargs):
        """Pull the latest data from Daikin."""
        try:
            prev_installer_state = self.get_state('InstallerState')
            self._status = await self._read_status()
            self._climate_control_powered = await self._device.climate_control.is_turned_on
            installer_state = self.get_state('InstallerState')
            if prev_installer_state is not None and prev_installer_state != installer_state and installer_state is False:
                # Leaving installer mode can have changes which may not be refreshed properly
                # For example from fixed temperature to weather dependent are read from profile during initialization
                # and it should update the profile after installer state
                _LOGGER.warning(
                    'Unit left installer state. If there are heavy changes it is probably better to reload integration. Currently it may not fully incorporate new settings.')

                await self.refresh_unit()

            await self.device.ws_connection.close()

            if not self._available:
                _LOGGER.info('Daikin became available again.')

            self._available = True
            self._failed_updates = 0
            self._type_error_failure = 0
        except TypeError as e:
            # Report only once
            self._type_error_failure += 1
            if self._type_error_failure < 2:
                _LOGGER.error(f'Failed to update the device status with error {e}', e)

        except (ClientConnectionError, ServerTimeoutError, TimeoutError) as error:
            # CancelledError must propagate: it carries update timeouts and shutdown
            self._failed_updates += 1
            self.mark_unavailable(error)
            # self._failed_updates += 1
            # if self._failed_updates < 2
            #    _LOGGER.error(f'Too many failed updates. Making component unavailable.')
            #    self._available = False
            # Report only once
            # if self._failed_updates == 1:
            #    _LOGGER.error(f'Failed update status from heat-pump. ({error})')
        except Exception:
            if self._available:
                _LOGGER.error(f'Something went wrong while updating data from the device', exc_info=True)
            self._available = False

    def mark_unavailable(self, error):
        if self._available:
            # report only once
            _LOGGER.error(f"Failed to the get the data from the device [{self.host}] ({error})")
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
            if hwt is None:
                return None

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
            if space_heating is None:
                return None

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
        # Units with OperationMode (e.g. D2CND) are switched off via standby, Power stays on
        is_on = ops["Power"] == "on" and ops.get("OperationMode") != "standby"
        # First check if it is on and if yes then check whatever it is in powerful mode
        if is_on:
            if 'powerful' in ops:
                state = ops["powerful"]
            else:
                state = 0

            if state == 0:
                return STATE_ON
            else:
                return STATE_PERFORMANCE
        else:
            return STATE_OFF

    def hwt_powerful_support(self):
        device = self.device
        if device.hot_water_tank is None:
            return False
        powerful_support = 'powerful' in [x.lower() for x in device.hot_water_tank.operations]
        return powerful_support

    async def async_set_water_tank_state(self, state):
        """
        Sets new hot water tank state. It can be off / on / powerful
        @param state: string
        @return: Nothing
        """
        hwt = self.device.hot_water_tank
        has_mode = 'OperationMode' in hwt.operations
        if state == STATE_OFF:
            if self.hwt_powerful_support():
                await hwt.set_powerful(False)
            if has_mode:
                # Like the app: standby the tank, Power is left to its own switch
                await hwt.call_operation('OperationMode', 'standby')
            else:
                await hwt.turn_off()
        else:
            if self.water_tank_status['operations'].get('Power') != 'on':
                await hwt.turn_on()
            if has_mode:
                await hwt.call_operation('OperationMode', 'heating')
            if self.hwt_powerful_support():
                await hwt.set_powerful(state != STATE_ON)
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

        if "TargetTemperature" in self.device.hot_water_tank._unit.operation_config:
            return self.device.hot_water_tank._unit.operation_config["TargetTemperature"][
                "heating"
            ]
        return {}
