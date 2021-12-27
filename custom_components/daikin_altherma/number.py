import logging
from datetime import timedelta

import async_timeout
from pyaltherma.const import ClimateControlMode

from homeassistant.components.number import NumberEntity
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from . import DOMAIN, AlthermaAPI

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Daikin climate based on config_entry."""
    api = hass.data[DOMAIN].get(entry.entry_id)

    async def async_update_data():
        try:
            async with async_timeout.timeout(10):
                await api.async_update()
        except:
            raise

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="daikin_space_heating_mode",
        update_method=async_update_data,
        update_interval=timedelta(seconds=30),
    )
    await coordinator.async_config_entry_first_refresh()
    async_add_entities([
        AlthermaUnitTemperatureControl(coordinator, api)
    ], update_before_add=False)


class AlthermaUnitTemperatureControl(NumberEntity, CoordinatorEntity):

    def __init__(self, coordinator, api: AlthermaAPI):
        super().__init__(coordinator)
        self._api = api
        self._attr_name = 'Temperature Control'
        self._attr_device_info = api.space_heating_device_info
        self._attr_unique_id = f"{self._api.info['serial_number']}-SpaceHeating-temp-control"
        self._attr_options = [x.value for x in list(ClimateControlMode)]
        self._attr_icon = 'mdi:sun-thermometer-outline'

    @property
    def value(self) -> float:
        status = self._api.space_heating_status
        operations = status.get('operations', {})
        key, _ = self._get_value_config()
        return operations.get(key, 0)

    @property
    def min_value(self) -> int:
        _, config = self._get_value_config()
        return config['minValue']

    @property
    def max_value(self) -> int:
        _, config = self._get_value_config()
        return config['maxValue']

    @property
    def step(self) -> int:
        _, config = self._get_value_config()
        return config['stepValue']

    def _get_value_config(self):
        schema = self._api.device.climate_control.unit.operation_config
        status: dict = self._api.space_heating_status
        operations = status.get('operations', {})
        mode = operations.get('OperationMode', None)
        if mode is not None:
            fixed_prop = f'LeavingWaterTemperature{mode.capitalize()}'
            offset_prop = f'LeavingWaterTemperatureOffset{mode.capitalize()}'
            fixed_config = schema.get(fixed_prop, {'settable': False})
            offset_config = schema.get(offset_prop, {'settable': False})
            if fixed_config['settable']:
                return fixed_prop, fixed_config
            else:
                return offset_prop, offset_config
        else:
            return None

    async def async_set_value(self, value: float) -> None:
        key, _ = self._get_value_config()
        await self._api.device.climate_control.call_operation(key, int(value))
        await self.coordinator.async_refresh()

    @property
    def device_info(self):
        return self._attr_device_info

    async def async_update(self):
        await self._api.async_update()
