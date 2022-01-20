import logging
from homeassistant.components.sensor import SensorEntity, DEVICE_CLASS_TEMPERATURE
from homeassistant.const import TEMP_CELSIUS
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
)

from . import DOMAIN, AlthermaAPI

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Daikin climate based on config_entry."""
    api = hass.data[DOMAIN].get(entry.entry_id)
    coordinator = hass.data[DOMAIN]['coordinator']
    async_add_entities([
        AlthermaUnitSensor(coordinator, api, 'LeavingWaterTemperatureCurrent', 'Leaving Water Temperature'),
        AlthermaUnitSensor(coordinator, api, 'IndoorTemperature', 'Indoor Temperature'),
        AlthermaUnitSensor(coordinator, api, 'OutdoorTemperature', 'Outdoor Temperature')
    ], update_before_add=False)


class AlthermaUnitSensor(SensorEntity, CoordinatorEntity):
    _attr_device_class = DEVICE_CLASS_TEMPERATURE
    _attr_native_unit_of_measurement = TEMP_CELSIUS

    def __init__(self, coordinator, api: AlthermaAPI, sensor: str, name: str = None):
        super().__init__(coordinator)
        self._api = api
        self._attr_name = name if name is not None else sensor
        self._attr_device_info = api.space_heating_device_info
        self._attr_unique_id = f"{self._api.info['serial_number']}-SpaceHeating-{sensor}"
        self._sensor = sensor

    @property
    def native_value(self) -> StateType:
        unit_status = self._api.status['function/SpaceHeating']
        sensors = unit_status['sensors']
        status = sensors[self._sensor]
        return round(status, 2)

    @property
    def device_info(self):
        return self._attr_device_info

    async def async_update(self):
        await self._api.async_update()
