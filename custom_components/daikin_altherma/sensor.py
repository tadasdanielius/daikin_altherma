import logging
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfTemperature
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
)

from . import DOMAIN, AlthermaAPI
from .schedule import decode_active

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
        # Active setpoint (follows schedule), unlike the TargetTemperatureDay/Night presets
        if 'TargetTemperature' in device.climate_control.unit.operations:
            entities.append(
                AlthermaUnitSensor(coordinator, api, 'TargetTemperature', 'Target Temperature', category='operations')
            )

    # Tank mode (heating/standby) is independent of its Power on/off
    hwt = device.hot_water_tank if device is not None else None
    if hwt is not None and api.HWT_device_info is not None and 'OperationMode' in hwt.unit.operations:
        entities.append(
            AlthermaWaterTankModeSensor(coordinator, api, hwt.unit.operations['OperationMode'])
        )

    if api.space_heating_device_info is not None:
        entities.append(AlthermaErrorCodeSensor(
            coordinator, api, 'Space Heating Error Code', api.space_heating_device_info, 'function/SpaceHeating'))
        entities.append(AlthermaNextScheduleSensor(
            coordinator, api, 'Space Heating Next Schedule', api.space_heating_device_info, 'function/SpaceHeating'))
        entities.append(AlthermaActiveScheduleSensor(
            coordinator, api, 'Space Heating Active Schedule', api.space_heating_device_info, 'function/SpaceHeating'))
    if hwt is not None and api.HWT_device_info is not None:
        entities.append(AlthermaErrorCodeSensor(
            coordinator, api, 'Hot Water Tank Error Code', api.HWT_device_info, hwt.unit_function))
        entities.append(AlthermaNextScheduleSensor(
            coordinator, api, 'Hot Water Tank Next Schedule', api.HWT_device_info, hwt.unit_function))
        entities.append(AlthermaActiveScheduleSensor(
            coordinator, api, 'Hot Water Tank Active Schedule', api.HWT_device_info, hwt.unit_function))

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

        # Actions - heating or cooling
        # Consumption Type - Electrical or Gas
        # Contents - Daily / Weekly / Monthly
        consumption_types = {'Electrical': 'Energy', 'Gas': 'Gas'}
        for consumption_type, ct_name in consumption_types.items():
            for unit_function, controller in device.altherma_units.items():
                if not controller.unit.consumptions_available:
                    continue

                if consumption_type not in controller.unit.consumptions:
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
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator, api: AlthermaAPI, sensor: str, name: str = None, category: str = 'sensors'):
        super().__init__(coordinator)
        self._api = api
        self._attr_name = name if name is not None else sensor
        self._attr_device_info = api.space_heating_device_info
        self._attr_unique_id = f"{self._api.info['serial_number']}-SpaceHeating-{sensor}"
        self._sensor = sensor
        self._category = category

    @property
    def native_value(self) -> StateType:
        unit_status = self._api.status['function/SpaceHeating']
        status = unit_status[self._category].get(self._sensor)
        if status is not None:
            return round(status, 2)
        else:
            return None

    @property
    def device_info(self):
        return self._attr_device_info

    async def async_update(self):
        await self._api.async_update()

    @property
    def available(self):
        return self._api.available


class AlthermaWaterTankModeSensor(SensorEntity, CoordinatorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = 'hot_water_tank_mode'

    def __init__(self, coordinator, api: AlthermaAPI, options):
        super().__init__(coordinator)
        self._api = api
        self._attr_name = 'Hot Water Tank Mode'
        self._attr_device_info = api.HWT_device_info
        self._attr_unique_id = f"{self._api.info['serial_number']}-HotWaterTank-OperationMode"
        self._attr_options = list(options)
        self._attr_icon = 'mdi:water-boiler'

    @property
    def native_value(self) -> StateType:
        status = self._api.water_tank_status
        if status is None:
            return None
        return status['operations'].get('OperationMode')

    @property
    def device_info(self):
        return self._attr_device_info

    async def async_update(self):
        await self._api.async_update()

    @property
    def available(self):
        return self._api.available


class AlthermaErrorCodeSensor(SensorEntity, CoordinatorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = 'mdi:alert-circle-outline'

    def __init__(self, coordinator, api: AlthermaAPI, name: str, device_info, unit_function: str):
        super().__init__(coordinator)
        self._api = api
        self._attr_name = name
        self._attr_device_info = device_info
        self._attr_unique_id = f"{self._api.info['serial_number']}-{unit_function.split('/')[1]}-error_code"
        self._unit_function = unit_function

    @property
    def native_value(self) -> StateType:
        # Adapter reports "" when there is no fault; None means the endpoint is unsupported
        error = self._api.status[self._unit_function].get('error')
        if error is None:
            return None
        return error or 'none'

    @property
    def device_info(self):
        return self._attr_device_info

    async def async_update(self):
        await self._api.async_update()

    @property
    def available(self):
        return self._api.available


WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


class AlthermaNextScheduleSensor(SensorEntity, CoordinatorEntity):
    _attr_icon = 'mdi:calendar-clock'

    def __init__(self, coordinator, api: AlthermaAPI, name: str, device_info, unit_function: str):
        super().__init__(coordinator)
        self._api = api
        self._attr_name = name
        self._attr_device_info = device_info
        self._attr_unique_id = f"{self._api.info['serial_number']}-{unit_function.split('/')[1]}-next_schedule"
        self._unit_function = unit_function

    def _next(self):
        return self._api.status[self._unit_function].get('next_schedule')

    @property
    def native_value(self) -> StateType:
        # Rendered like the Daikin app: "Wednesday 00:00 · heating_night · 18.0 °C"
        nxt = self._next()
        if nxt is None:
            return None
        parts = [f"{WEEKDAYS[nxt['Day']]} {self._start_time(nxt)}", nxt.get('OperationMode')]
        temperature = self._temperature(nxt)
        if temperature is not None:
            parts.append(f"{temperature} °C")
        return ' · '.join(str(p) for p in parts if p is not None)

    @property
    def extra_state_attributes(self):
        nxt = self._next()
        if nxt is None:
            return None
        return {
            'day': WEEKDAYS[nxt['Day']],
            'start_time': self._start_time(nxt),
            'operation_mode': nxt.get('OperationMode'),
            'target_temperature': self._temperature(nxt),
        }

    @staticmethod
    def _start_time(nxt):
        # HHMM; 2400 is end of day, shown as 00:00 like the app
        start = nxt['StartTime']
        return f"{start // 100 % 24:02d}:{start % 100:02d}"

    @staticmethod
    def _temperature(nxt):
        value = nxt.get('TargetTemperature')
        return value / 10 if value is not None else None

    @property
    def device_info(self):
        return self._attr_device_info

    async def async_update(self):
        await self._api.async_update()

    @property
    def available(self):
        return self._api.available


class AlthermaActiveScheduleSensor(SensorEntity, CoordinatorEntity):
    _attr_icon = 'mdi:calendar-week'

    def __init__(self, coordinator, api: AlthermaAPI, name: str, device_info, unit_function: str):
        super().__init__(coordinator)
        self._api = api
        self._attr_name = name
        self._attr_device_info = device_info
        self._attr_unique_id = f"{self._api.info['serial_number']}-{unit_function.split('/')[1]}-active_schedule"
        self._unit_function = unit_function

    def _decoded(self):
        schedule = self._api.status[self._unit_function].get('schedule')
        profile = self._api.unit_profile(self._unit_function)
        if schedule is None or profile is None:
            return None
        try:
            return decode_active(self._unit_function, profile, schedule['active'], schedule['entries'])
        except (KeyError, IndexError, ValueError):
            _LOGGER.debug('Cannot decode schedule %s', schedule, exc_info=True)
            return None

    @property
    def native_value(self) -> StateType:
        decoded = self._decoded()
        return decoded[0] if decoded is not None else None

    @property
    def extra_state_attributes(self):
        # {group: ["00:00 standby", "08:00 heating", ...]} as listed in the app
        decoded = self._decoded()
        return decoded[1] if decoded is not None else None

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
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    _attr_state_class = SensorStateClass.TOTAL_INCREASING

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
