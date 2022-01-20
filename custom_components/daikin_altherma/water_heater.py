import logging

from homeassistant.components.water_heater import (
    STATE_OFF,
    STATE_ON,
    STATE_PERFORMANCE,
    SUPPORT_OPERATION_MODE,
    SUPPORT_TARGET_TEMPERATURE,
    WaterHeaterEntity,
)
from homeassistant.const import ATTR_TEMPERATURE, TEMP_CELSIUS
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
)

from . import DOMAIN, AlthermaAPI

_LOGGER = logging.getLogger(__name__)

OPERATION_LIST = [STATE_OFF, STATE_ON, STATE_PERFORMANCE]

SUPPORT_FLAGS_HEATER = SUPPORT_TARGET_TEMPERATURE | SUPPORT_OPERATION_MODE


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Daikin climate based on config_entry."""
    api = hass.data[DOMAIN].get(entry.entry_id)
    coordinator = hass.data[DOMAIN]['coordinator']
    async_add_entities([AlthermaWaterHeater(coordinator, api)], update_before_add=False)


class AlthermaWaterHeater(WaterHeaterEntity, CoordinatorEntity):
    _attr_temperature_unit = TEMP_CELSIUS
    _attr_operation_list = OPERATION_LIST
    _attr_supported_features = SUPPORT_FLAGS_HEATER

    def __init__(self, coordinator, api: AlthermaAPI):
        super().__init__(coordinator)
        self._attr_name = "Domestic Hot Water Tank"
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
        conf = device._unit.operation_config['DomesticHotWaterTemperatureHeating']
        if 'settable' in conf:
            return conf['settable']
        return False

    @property
    def _supported_features(self):
        result = SUPPORT_FLAGS_HEATER if self._is_settable_target_temp() else SUPPORT_OPERATION_MODE
        return result

    @property
    def target_temperature(self) -> float:
        target_temperature = self._api.status["function/DomesticHotWaterTank"][
            "operations"
        ]["TargetTemperature"]

        return target_temperature

    @property
    def current_temperature(self) -> float:
        current_temperature = self._api.status["function/DomesticHotWaterTank"][
            "sensors"
        ]["TankTemperature"]
        return current_temperature

    @property
    def current_operation(self):
        return self._api.water_tank_operation

    @property
    def min_temp(self):
        return self._api.water_tank_target_temp_config["minValue"]

    @property
    def max_temp(self):
        return self._api.water_tank_target_temp_config["maxValue"]

    async def async_update(self):
        await self._api.async_update()
