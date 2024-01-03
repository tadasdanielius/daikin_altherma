import logging
from homeassistant.components.number import NumberEntity
from homeassistant.const import UnitOfTemperature
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
)
from pyaltherma.const import ClimateControlMode

from . import DOMAIN, AlthermaAPI

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Daikin climate based on config_entry."""
    api = hass.data[DOMAIN].get(entry.entry_id)

    coordinator = hass.data[DOMAIN]['coordinator']
    entities = []
    device = api.device
    climate_control = device.climate_control
    if climate_control is not None:
        unit = climate_control.unit
        if unit is not None:
            operations = unit.operations
            if 'LeavingWaterTemperatureOffsetHeating' in operations or \
                    'LeavingWaterTemperatureOffsetCooling' in operations or \
                    'LeavingWaterTemperatureOffsetAuto' in operations:
                entities.append(AlthermaUnitTemperatureControl(coordinator, api))
            if 'TargetTemperatureDay' in operations:
                profile = operations['TargetTemperatureDay']
                if type(profile) is dict:
                    entities.append(
                        GenericOperationControl(
                            coordinator, api, 'Target Temperature Day', 'TargetTemperatureDay', profile)
                    )
                else:
                    _LOGGER.error(f'Profile (TargetTemperatureDay) is not dictionary!')

            if 'TargetTemperatureNight' in operations:
                profile = operations['TargetTemperatureNight']
                if type(profile) is dict:
                    entities.append(
                        GenericOperationControl(
                            coordinator, api, 'Target Temperature Night', 'TargetTemperatureNight', profile)
                    )
                else:
                    _LOGGER.error(f'Profile (TargetTemperatureNight) is not dictionary!')

            if 'RoomTemperatureHeating' in operations:
                profile = operations['RoomTemperatureHeating']
                if type(profile) is dict:
                    entities.append(
                        GenericOperationControl(
                            coordinator, api, 'Room Temperature', 'RoomTemperatureHeating', profile
                        )
                    )
                else:
                    _LOGGER.error(f'Room Temperature (RoomTemperatureHeating) is not a dictionary!')

    #async_add_entities([
    #    AlthermaUnitTemperatureControl(coordinator, api)
    #], update_before_add=False)
    async_add_entities(entities, update_before_add=False)


class GenericOperationControl(NumberEntity, CoordinatorEntity):
    def __init__(self, coordinator, api: AlthermaAPI, name: str, operation: str, profile):
        super().__init__(coordinator)
        self._api = api
        self._operation = operation
        self._profile = profile
        self._attr_name = name
        self._attr_device_info = api.space_heating_device_info
        self._attr_unique_id = f"{self._api.info['serial_number']}-{operation}-SpaceHeating-control"
        self._attr_icon = 'mdi:sun-thermometer-outline'
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    @property
    def native_value(self) -> float:
        status = self._api.space_heating_status
        operations = status.get('operations', {})
        return operations.get(self._operation, 0)

    @property
    def native_min_value(self) -> float:
        val = self._profile['minValue']
        return val

    @property
    def native_max_value(self) -> float:
        val = self._profile['maxValue']
        return val

    @property
    def native_step(self) -> float:
        val = self._profile['stepValue']
        return val

    @property
    def device_info(self):
        return self._attr_device_info

    @property
    def mode(self) -> str:
        return 'box'

    async def async_set_native_value(self, value: float) -> None:
        await self._api.device.climate_control.call_operation(self._operation, float(value), validate=False)
        await self.coordinator.async_request_refresh()

    @property
    def device_info(self):
        return self._attr_device_info

    @property
    def available(self):
        return self._api.available

    async def async_update(self):
        await self._api.async_update()


class AlthermaUnitTemperatureControl(NumberEntity, CoordinatorEntity):

    def __init__(self, coordinator, api: AlthermaAPI):
        super().__init__(coordinator)
        self._api = api
        self._attr_name = 'Temperature Control'
        self._attr_device_info = api.space_heating_device_info
        self._attr_unique_id = f"{self._api.info['serial_number']}-SpaceHeating-temp-control"
        self._attr_options = [x.value for x in list(ClimateControlMode)]
        self._attr_icon = 'mdi:sun-thermometer-outline'
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    @property
    def native_value(self) -> float:
        status = self._api.space_heating_status
        operations = status.get('operations', {})
        key, _ = self._get_value_config()
        return operations.get(key, 0)

    @property
    def native_min_value(self) -> float:
        _, config = self._get_value_config()
        return config['minValue']

    @property
    def native_max_value(self) -> float:
        _, config = self._get_value_config()
        return config['maxValue']

    @property
    def native_step(self) -> float:
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

    async def async_set_native_value(self, value: float) -> None:
        key, _ = self._get_value_config()
        await self._api.device.climate_control.call_operation(key, float(value))
        await self.coordinator.async_request_refresh()

    @property
    def device_info(self):
        return self._attr_device_info

    @property
    def available(self):
        return self._api.available

    async def async_update(self):
        await self._api.async_update()

    @property
    def mode(self) -> str:
        return 'box'
