import logging

from homeassistant.components.water_heater import (
    STATE_OFF,
    STATE_ON,
    STATE_PERFORMANCE,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError

from . import DOMAIN, AlthermaAPI, validate_temperature
from .entity import AlthermaEntity

_LOGGER = logging.getLogger(__name__)

OPERATION_LIST = [STATE_OFF, STATE_ON, STATE_PERFORMANCE]

OPERATION_LIST_NO_PERF = [STATE_OFF, STATE_ON]

SUPPORT_FLAGS_HEATER = (
    WaterHeaterEntityFeature.TARGET_TEMPERATURE
    | WaterHeaterEntityFeature.OPERATION_MODE
)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Daikin climate based on config_entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    api = data.api
    if api.HWT_device_info is not None:
        coordinator = data.coordinator
        async_add_entities(
            [AlthermaWaterHeater(coordinator, api)], update_before_add=False
        )
    else:
        _LOGGER.debug("No hot water tank unit advertised")


class AlthermaWaterHeater(AlthermaEntity, WaterHeaterEntity):
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_operation_list = OPERATION_LIST
    _attr_supported_features = SUPPORT_FLAGS_HEATER

    def __init__(self, coordinator, api: AlthermaAPI):
        super().__init__(coordinator, api)
        self._attr_name = "Domestic Hot Water Tank"
        self._attr_operation_list = OPERATION_LIST
        device = api.device
        self.powerful_support = "powerful" in [
            x.lower() for x in device.hot_water_tank.operations
        ]
        if not self.powerful_support:
            self._attr_operation_list = OPERATION_LIST_NO_PERF
        self._attr_device_info = api.HWT_device_info
        self._api = api
        self._attr_unique_id = f"{self._api.info['serial_number']}-heater"
        self._attr_icon = "mdi:bathtub-outline"

    @property
    def device_info(self):
        return self._attr_device_info

    async def async_set_temperature(self, **kwargs):
        if not self._is_settable_target_temp():
            raise HomeAssistantError(
                "Target temperature is not writable in the current mode"
            )
        key, config = self._api.water_tank_temperature_operation
        temperature = validate_temperature(kwargs.get(ATTR_TEMPERATURE), config)
        await self._api.async_execute(
            self._api.device.hot_water_tank.call_operation, key, temperature
        )
        await self.coordinator.async_request_refresh()

    async def async_set_operation_mode(self, operation_mode):
        await self._api.async_set_water_tank_state(operation_mode)
        await self.coordinator.async_request_refresh()

    def _is_settable_target_temp(self):
        key, config = self._api.water_tank_temperature_operation
        return bool(
            key
            and config.get("settable", True)
            and "minValue" in config
            and "maxValue" in config
            and not self._get_status()
            .get("states", {})
            .get("WeatherDependentState", False)
        )

    def _get_status(self):
        return self._api.water_tank_status

    @property
    def supported_features(self):
        features = WaterHeaterEntityFeature.OPERATION_MODE
        if self._is_settable_target_temp():
            features |= WaterHeaterEntityFeature.TARGET_TEMPERATURE
        return features

    @property
    def native_target_temperature(self):
        return self._api.water_tank_target_temperature

    @property
    def native_current_temperature(self):
        return self._api.water_tank_current_temperature

    # Older HA versions read the legacy names when publishing entity state.
    if not hasattr(WaterHeaterEntity, "native_current_temperature"):
        _attr_temperature_unit = _attr_native_temperature_unit
        current_temperature = native_current_temperature
        target_temperature = native_target_temperature

    @property
    def current_operation(self):
        return self._api.water_tank_operation

    @property
    def min_temp(self):
        if self._api.water_tank_target_temp_config is None:
            return None
        if "minValue" in self._api.water_tank_target_temp_config:
            return self._api.water_tank_target_temp_config["minValue"]
        return None

    @property
    def max_temp(self):
        if self._api.water_tank_target_temp_config is None:
            return None
        if "maxValue" in self._api.water_tank_target_temp_config:
            return self._api.water_tank_target_temp_config["maxValue"]
        return None

    async def async_turn_on(self, **kwargs) -> None:
        await self.async_set_operation_mode(STATE_ON)

    async def async_turn_off(self, **kwargs) -> None:
        await self.async_set_operation_mode(STATE_OFF)

    async def async_toggle(self, **kwargs) -> None:
        current_operation = self.current_operation
        if current_operation == STATE_OFF:
            await self.async_turn_on()
        else:
            await self.async_turn_off()
