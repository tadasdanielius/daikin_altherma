import logging
from homeassistant.components.sensor import SensorEntity, DEVICE_CLASS_TEMPERATURE, STATE_CLASS_TOTAL_INCREASING
from homeassistant.const import TEMP_CELSIUS, ENERGY_KILO_WATT_HOUR, DEVICE_CLASS_ENERGY
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
    translation = {
        'LeavingWaterTemperatureCurrent': 'Leaving Water Temperature',
        'IndoorTemperature': 'Indoor Temperature',
        'OutdoorTemperature': 'Outdoor Temperature'
    }
    device = api.device
    entities = []
    if device is not None and device.climate_control is not None:
        sensors = device.climate_control.sensors
        for sensor in sensors:
            if sensor in translation:
                name = translation[sensor]
            else:
                name = sensor
            entities.append(
                AlthermaUnitSensor(coordinator, api, sensor, name)
            )

    #entities = [
    #    AlthermaUnitSensor(coordinator, api, 'LeavingWaterTemperatureCurrent', 'Leaving Water Temperature'),
    #    AlthermaUnitSensor(coordinator, api, 'IndoorTemperature', 'Indoor Temperature'),
    #    AlthermaUnitSensor(coordinator, api, 'OutdoorTemperature', 'Outdoor Temperature')
    #]
    try:
        device = api.device
        # Electrical -> (Heating/Cooling) -> (D, W, M)
        # controller.unit.consumptions['Electrical'].actions -> Heating/Cooling
        # controller.unit.consumptions['Electrical'].actions['Heating'].consumption_contents -> Daily/Weekly/Monthly
        # controller.unit.consumptions['Electrical'].actions['Heating'].consumption_contents['Daily']
        # d.contentCount / d.resolution
        consumption_types = {'Electrical': 'Energy', 'Gas': 'Gas'}
        for consumption_type, ct_name in consumption_types.items():
            for unit_function, controller in device.altherma_units.items():
                if not controller.unit.consumptions_available:
                    continue

                device_info = api.space_heating_device_info
                if unit_function == 'function/DomesticHotWaterTank':
                    device_info = api.HWT_device_info

                unit_name = await controller.unit_name
                actions = controller.unit.consumptions[consumption_type].actions
                for action, details in actions.items():
                    contents = details.consumption_contents
                    if 'Daily' in contents:
                        entities.append(
                            ConsumptionSensor(coordinator, api, device_info, unit_function, unit_name, action, 'D',
                                              '2 Hours', consumption_type=consumption_type, consumption_type_name=ct_name)
                        )
                    if 'Weekly' in contents:
                        entities.append(
                            ConsumptionSensor(coordinator, api, device_info, unit_function, unit_name, action, 'W', 'Day',
                                              consumption_type=consumption_type, consumption_type_name=ct_name)
                        )
                    if 'Monthly' in contents:
                        entities.append(
                            ConsumptionSensor(coordinator, api, device_info, unit_function, unit_name, action, 'M', 'Month',
                                              consumption_type=consumption_type, consumption_type_name=ct_name)
                        )

    except:
        _LOGGER.warning('consumption information could not be added', exc_info=True)
    async_add_entities(entities, update_before_add=False)


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

    @property
    def available(self):
        return self._api.available


def _find_last_value(a):
    if a[-1] is not None:
        return a[-1]

    last_value = None
    for val in a:
        if val is not None:
            last_value = val
    return last_value


class ConsumptionSensor(SensorEntity, CoordinatorEntity):
    _attr_device_class = DEVICE_CLASS_ENERGY
    _attr_native_unit_of_measurement = ENERGY_KILO_WATT_HOUR

    # Not sure if it is a right class?
    _attr_state_class = STATE_CLASS_TOTAL_INCREASING

    def __init__(
            self, coordinator,
            api: AlthermaAPI,
            device_info,
            unit_function: str,
            unit_name: str,
            action: str,
            content_id: str,
            content_name: str,
            consumption_type: str = 'Electrical',
            consumption_type_name: str = 'Energy'):

        super().__init__(coordinator)
        self._api = api
        self.unit_function = unit_function
        self.unit_name = unit_name
        self.action = action
        self.content_id = content_id
        self.content_name = content_name
        self.consumption_type = consumption_type
        self._attr_name = f'{unit_name} {content_name} {action} {consumption_type_name}'
        self._attr_device_info = device_info

        self._attr_unique_id = f"{self._api.info['serial_number']}/{unit_function}/{consumption_type}/{action}/{content_id}"

    @property
    def extra_state_attributes(self):
        unit_status = self._api.status[self.unit_function]
        consumption = unit_status['consumption'][self.consumption_type]
        consumption_action = consumption[self.action]
        consumption_content = consumption_action[self.content_id]
        last_value = _find_last_value(consumption_content)
        consumption_content_non_null = [x if x is not None else '-' for x in consumption_content]
        attribute_keys = list(range(len(consumption_content_non_null)))
        attributes = {}
        if self.content_name == '2 Hours':
            time_periods = list(range(0, 25, 2))
            time_periods[-1] = 0
            time_periods = ["{:02d}".format(x) for x in time_periods]
            for idx, value in enumerate(time_periods[1:]):
                attribute_keys[idx] = f"Yesterday {time_periods[idx]}:00 - {value}:00"
                attribute_keys[idx + 12] = f"Today {time_periods[idx]}:00 - {value}:00"
            attributes = dict(zip(attribute_keys, consumption_content_non_null))
            return attributes

        if self.content_name == 'Day':
            time_periods = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
            for idx, value in enumerate(time_periods):
                attribute_keys[idx] = f'Last {value}'
                attribute_keys[idx + len(time_periods)] = value
            attributes = dict(zip(attribute_keys, consumption_content_non_null))

        if self.content_name == 'Month':
            time_periods = ['January', 'February', 'March', 'April',
                            'May', 'June', 'July', 'August', 'September',
                            'October', 'November', 'December'
                            ]
        for idx, value in enumerate(time_periods):
            attribute_keys[idx] = f'Last {value}'
            attribute_keys[idx + len(time_periods)] = value
        attributes = dict(zip(attribute_keys, consumption_content_non_null))

        return attributes

    @property
    def native_value(self) -> StateType:
        unit_status = self._api.status[self.unit_function]
        consumption = unit_status['consumption'][self.consumption_type]
        consumption_action = consumption[self.action]
        consumption_content = consumption_action[self.content_id]
        last_value = _find_last_value(consumption_content)

        return last_value

    @property
    def available(self):
        return self._api.available

    @property
    def device_info(self):
        return self._attr_device_info

    async def async_update(self):
        await self._api.async_update()
