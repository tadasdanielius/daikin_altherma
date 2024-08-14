import logging
from homeassistant.components.water_heater import (
    STATE_OFF,
    STATE_ON,
    STATE_PERFORMANCE,
    WaterHeaterEntity,
    WaterHeaterEntityFeature
)
from homeassistant.const import UnitOfTemperature, ATTR_TEMPERATURE
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
)

from . import DOMAIN, AlthermaAPI

_LOGGER = logging.getLogger(__name__)

OPERATION_LIST = [STATE_OFF, STATE_ON, STATE_PERFORMANCE]

OPERATION_LIST_NO_PERF = [STATE_OFF, STATE_ON]

SUPPORT_FLAGS_HEATER = WaterHeaterEntityFeature.TARGET_TEMPERATURE | WaterHeaterEntityFeature.OPERATION_MODE


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Daikin climate based on config_entry."""
    api = hass.data[DOMAIN].get(entry.entry_id)
    if api.HWT_device_info is not None:
        coordinator = hass.data[DOMAIN]['coordinator']
        async_add_entities([AlthermaWaterHeater(coordinator, api)], update_before_add=False)
    else:
        _LOGGER.warning(f'Cannot find daikin hot water tank unit.')


class AlthermaWaterHeater(WaterHeaterEntity, CoordinatorEntity):
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_operation_list = OPERATION_LIST
    _attr_supported_features = SUPPORT_FLAGS_HEATER

    def __init__(self, coordinator, api: AlthermaAPI):
        super().__init__(coordinator)
        self._attr_name = "Domestic Hot Water Tank"
        self._attr_operation_list = OPERATION_LIST
        device = api.device
        self.powerful_support = 'powerful' in [x.lower() for x in device.hot_water_tank.operations]
        if not self.powerful_support:
            self._attr_operation_list = OPERATION_LIST_NO_PERF
        self._attr_device_info = api.HWT_device_info
        self._api = api
        self._attr_unique_id = f"{self._api.info['serial_number']}-heater"
        self._attr_icon = 'mdi:bathtub-outline'

    @property
    def device_info(self):
        return self._attr_device_info

    async def async_set_temperature(self, **kwargs):
        target_temperature = kwargs.get(ATTR_TEMPERATURE)
        device = self._api.device.hot_water_tank
        if self._is_settable_target_temp():
            await device.set_domestic_hot_water_temperature_heating(target_temperature)

        await self.coordinator.async_request_refresh()

    async def async_set_operation_mode(self, operation_mode):
        await self._api.async_set_water_tank_state(operation_mode)
        await self.coordinator.async_request_refresh()

    def _is_settable_target_temp(self):
        device = self._api.device.hot_water_tank
        if device is None:
            return False

        if device._unit is None:
            return False
        if 'DomesticHotWaterTemperatureHeating' not in device._unit.operation_config:
            return False

        conf = device._unit.operation_config['DomesticHotWaterTemperatureHeating']
        if 'settable' in conf:
            return conf['settable']
        return True

    def _get_status(self):
        if "function/DomesticHotWaterTank" in self._api.status:
            status = self._api.status[f"function/DomesticHotWaterTank"]

        elif "function/DomesticHotWater" in self._api.status:
            status = self._api.status[f"function/DomesticHotWater"]
        else:
            status = None
        return status

    @property
    def supported_features(self):
        status = self._get_status()
        states = status['states']
        if 'WeatherDependentState' in states:
            if states['WeatherDependentState']:
                return WaterHeaterEntityFeature.OPERATION_MODE
        return SUPPORT_FLAGS_HEATER

    @property
    def target_temperature(self) -> float:
        status = self._get_status()
        operations = status["operations"]
        if "DomesticHotWaterTemperatureHeating" in operations:
            return operations["DomesticHotWaterTemperatureHeating"]
        else:
            return operations["TargetTemperature"]

    @property
    def current_temperature(self) -> float:
        status = self._get_status()
        if "sensors" in status:
            sensors = status["sensors"]
            if "TankTemperature" in sensors:
                return sensors['TankTemperature']
            if len(sensors) > 0:
                return list(sensors.values())[0]
        if "operations" in status:
            operations = status["operations"]
            if "SensorTemperature" in operations:
                return operations["SensorTemperature"]
        return 0

    @property
    def current_operation(self):
        return self._api.water_tank_operation

    @property
    def min_temp(self):
        if self._api.water_tank_target_temp_config is None:
            return None
        if 'minValue' in self._api.water_tank_target_temp_config:
            return self._api.water_tank_target_temp_config["minValue"]
        return None

    @property
    def max_temp(self):
        if self._api.water_tank_target_temp_config is None:
            return None
        if 'maxValue' in self._api.water_tank_target_temp_config:
            return self._api.water_tank_target_temp_config["maxValue"]
        return None

    async def async_update(self):
        await self._api.async_update()

    @property
    def available(self):
        return self._api.available

    async def async_turn_on(self, **kwargs) -> None:
        await self.async_set_operation_mode(STATE_ON)

    async def async_turn_off(self, **kwargs) -> None:
        await self.async_set_operation_mode(STATE_OFF)

    async def async_toggle(self, **kwargs) -> None:
        current_operation = self.current_operation()
        if current_operation == STATE_OFF:
            await self.async_turn_on()
        else:
            await self.async_turn_off()
