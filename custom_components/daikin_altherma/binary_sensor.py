import logging
from homeassistant.components.binary_sensor import BinarySensorEntity, DEVICE_CLASS_PROBLEM
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
)

from . import DOMAIN, AlthermaAPI

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Daikin climate based on config_entry."""
    api = hass.data[DOMAIN].get(entry.entry_id)
    coordinator = hass.data[DOMAIN]['coordinator']
    entities = []
    if api.space_heating_device_info is not None:
        entities.append(AlthermaUnitProblemSensor(
            coordinator, api, 'Space Heating Unit State',
            api.space_heating_device_info,
            'SpaceHeating'))

    if api.HWT_device_info is not None:
        hwt = api.device.hot_water_tank
        unit_function = hwt.unit_function
        unit_ref = unit_function.split('/')[1]
        _LOGGER.warning(f"Tank unit function: {unit_function}")
        entities.append(AlthermaUnitProblemSensor(
            coordinator, api, 'Hot Water Tank State',
            api.HWT_device_info,
            #'DomesticHotWaterTank'
            unit_ref
        ))
    async_add_entities(entities, update_before_add=False)


class AlthermaUnitProblemSensor(BinarySensorEntity, CoordinatorEntity):
    _attr_device_class = DEVICE_CLASS_PROBLEM

    def __init__(
            self, coordinator, api: AlthermaAPI,
            name: str, device_info, unit_ref
    ):
        super().__init__(coordinator)
        self._api = api
        self._attr_name = name
        self._attr_device_info = device_info
        self._attr_unique_id = f"{self._api.info['serial_number']}-{unit_ref}-problem_sensor"
        self._state = None
        self._unit_ref = unit_ref

    @property
    def device_info(self):
        return self._attr_device_info

    async def async_update(self):
        await self._api.async_update()

    @property
    def is_on(self):
        return self._is_problem_state()

    def _is_problem_state(self):
        unit_status = self._api.status[f'function/{self._unit_ref}']
        states = unit_status['states'].copy()
        # Not a problem if we are in weather dependent state
        if 'WeatherDependentState' in states:
            del states['WeatherDependentState']
        values = list(states.values())
        return sum(values) > 0

    @property
    def extra_state_attributes(self):
        return self._api.status[f"function/{self._unit_ref}"]['states']

    @property
    def available(self):
        return self._api.available
