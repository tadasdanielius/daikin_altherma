import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfTemperature
from homeassistant.helpers.typing import StateType

from . import DOMAIN, AlthermaAPI, temperature_value
from .details import UNIT_NAMES, detail_keys, detail_profiles
from .entity import AlthermaEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Daikin climate based on config_entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    api = data.api
    coordinator = data.coordinator
    translation = {
        "LeavingWaterTemperatureCurrent": "Leaving Water Temperature",
        "IndoorTemperature": "Indoor Temperature",
        "OutdoorTemperature": "Outdoor Temperature",
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
            entities.append(AlthermaUnitSensor(coordinator, api, sensor, name))

        if (
            "TargetTemperature" in device.climate_control.unit.operations
            and "TargetTemperature" not in sensors
        ):
            entities.append(
                AlthermaUnitSensor(
                    coordinator,
                    api,
                    "TargetTemperature",
                    "Target Temperature",
                    section="operations",
                )
            )

    for function, profile in detail_profiles(device):
        info = (
            api.space_heating_device_info
            if function == "function/SpaceHeating"
            else api.HWT_device_info
        )
        if info is not None:
            entities.extend(
                UnitDetailSensor(coordinator, api, function, key, info)
                for key in detail_keys(profile)
            )

    tank = device.hot_water_tank
    if tank is not None and api.HWT_device_info is not None:
        modes = tank.unit.operations.get("OperationMode")
        if isinstance(modes, list) and modes:
            entities.append(WaterTankModeSensor(coordinator, api, modes))
        if any(
            key in tank.sensors for key in ("TankTemperature", "SensorTemperature")
        ) or ("SensorTemperature" in tank.operations):
            entities.append(WaterTankTemperatureSensor(coordinator, api, "current"))
        if api.water_tank_temperature_operation[0] is not None:
            entities.append(WaterTankTemperatureSensor(coordinator, api, "target"))

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
        consumption_types = {"Electrical": "Energy", "Gas": "Gas"}
        for consumption_type, ct_name in consumption_types.items():
            for unit_function, controller in device.altherma_units.items():
                if not controller.unit.consumptions_available:
                    continue

                if consumption_type not in controller.unit.consumptions:
                    continue

                device_info = api.space_heating_device_info
                if unit_function in (
                    "function/DomesticHotWaterTank",
                    "function/DomesticHotWater",
                ):
                    device_info = api.HWT_device_info

                unit_name = await api.async_execute(
                    lambda controller=controller: controller.unit_name
                )
                actions = controller.unit.consumptions[consumption_type].actions
                for action, details in actions.items():
                    contents = details.consumption_contents
                    if "Daily" in contents:
                        entities.append(
                            ConsumptionSensor(
                                coordinator,
                                api,
                                device_info,
                                unit_function,
                                unit_name,
                                action,
                                "D",
                                "2 Hours",
                                consumption_type=consumption_type,
                                consumption_type_name=ct_name,
                            )
                        )
                    if "Weekly" in contents:
                        entities.append(
                            ConsumptionSensor(
                                coordinator,
                                api,
                                device_info,
                                unit_function,
                                unit_name,
                                action,
                                "W",
                                "Day",
                                consumption_type=consumption_type,
                                consumption_type_name=ct_name,
                            )
                        )
                    if "Monthly" in contents:
                        entities.append(
                            ConsumptionSensor(
                                coordinator,
                                api,
                                device_info,
                                unit_function,
                                unit_name,
                                action,
                                "M",
                                "Month",
                                consumption_type=consumption_type,
                                consumption_type_name=ct_name,
                            )
                        )

    except Exception:
        _LOGGER.warning("consumption information could not be added", exc_info=True)
    async_add_entities(entities, update_before_add=False)


class AlthermaUnitSensor(AlthermaEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator,
        api: AlthermaAPI,
        sensor: str,
        name: str = None,
        section="sensors",
    ):
        super().__init__(coordinator, api)
        self._api = api
        self._attr_name = name if name is not None else sensor
        self._attr_device_info = api.space_heating_device_info
        self._attr_unique_id = (
            f"{self._api.info['serial_number']}-SpaceHeating-{sensor}"
        )
        self._section = section
        self._sensor = sensor

    @property
    def native_value(self) -> StateType:
        status = temperature_value(
            self._api.space_heating_status.get(self._section, {}).get(self._sensor)
        )
        if status is not None:
            return round(status, 2)
        else:
            return None

    @property
    def device_info(self):
        return self._attr_device_info


class WaterTankModeSensor(AlthermaEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "hot_water_tank_mode"
    _attr_icon = "mdi:water-boiler"

    def __init__(self, coordinator, api, modes):
        super().__init__(coordinator, api)
        self._attr_name = "Hot Water Tank Mode"
        self._attr_unique_id = f"{api.info['serial_number']}-HotWaterTank-OperationMode"
        self._attr_device_info = api.HWT_device_info
        self._attr_options = modes

    @property
    def native_value(self):
        return self._api.water_tank_status.get("operations", {}).get("OperationMode")


class UnitDetailSensor(AlthermaEntity, SensorEntity):
    def __init__(self, coordinator, api, function, key, info):
        super().__init__(coordinator, api)
        self._function = function
        self._key = key
        self._attr_device_info = info
        self._attr_name = f"{UNIT_NAMES[function]} {key.replace('_', ' ').title()}"
        self._attr_unique_id = (
            f"{api.info['serial_number']}-{function.split('/')[-1]}-{key}"
        )
        self._attr_icon = {
            "active_schedule": "mdi:calendar-week",
            "next_schedule": "mdi:calendar-clock",
            "error_code": "mdi:alert-circle-outline",
        }[key]
        if key == "error_code":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        detail = self._api.unit_details.get(self._function, {}).get(self._key)
        return detail[0] if detail else None

    @property
    def extra_state_attributes(self):
        detail = self._api.unit_details.get(self._function, {}).get(self._key)
        return detail[1] if detail else None


class WaterTankTemperatureSensor(AlthermaEntity, SensorEntity):
    """Expose water-heater attributes as graphable, read-only sensors."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator, api, kind):
        super().__init__(coordinator, api)
        self._kind = kind
        self._attr_name = f"Water Tank {kind.capitalize()} Temperature"
        self._attr_unique_id = (
            f"{api.info['serial_number']}-HotWaterTank-{kind}-temperature"
        )
        self._attr_device_info = api.HWT_device_info
        if kind == "current":
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        if self._kind == "current":
            return self._api.water_tank_current_temperature
        return self._api.water_tank_target_temperature


def unit_display_name(name, unit_function):
    """Keep meaningful names and replace adapter placeholders with a stable label."""
    if isinstance(name, str):
        name = name.strip()
        if name and name.casefold() not in ("$null", "null", "none"):
            return name
    return {
        "function/SpaceHeating": "Space Heating",
        "function/DomesticHotWaterTank": "Hot Water Tank",
        "function/DomesticHotWater": "Hot Water Tank",
    }.get(unit_function, unit_function.rsplit("/", 1)[-1])


def _find_last_value(values):
    return next((value for value in reversed(values) if value is not None), None)


class ConsumptionSensor(AlthermaEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self,
        coordinator,
        api: AlthermaAPI,
        device_info,
        unit_function: str,
        unit_name: str,
        action: str,
        content_id: str,
        content_name: str,
        consumption_type: str = "Electrical",
        consumption_type_name: str = "Energy",
    ):

        super().__init__(coordinator, api)
        self._api = api
        self.unit_function = unit_function
        self.unit_name = unit_display_name(unit_name, unit_function)
        self.action = action
        self.content_id = content_id
        self.content_name = content_name
        self.consumption_type = consumption_type
        self._attr_name = (
            f"{self.unit_name} {content_name} {action} {consumption_type_name}"
        )
        self._attr_device_info = device_info

        self._attr_unique_id = f"{self._api.info['serial_number']}/{unit_function}/{consumption_type}/{action}/{content_id}"

    def _consumption_content(self):
        content = (
            self._api.status.get(self.unit_function, {})
            .get("consumption", {})
            .get(self.consumption_type, {})
            .get(self.action, {})
            .get(self.content_id, [])
        )
        return content if isinstance(content, list) else []

    @property
    def extra_state_attributes(self):
        values = self._consumption_content()
        if self.content_name == "2 Hours":
            periods = [
                f"{hour:02d}:00 - {(hour + 2) % 24:02d}:00" for hour in range(0, 24, 2)
            ]
            keys = [
                f"{day} {period}"
                for day in ("Yesterday", "Today")
                for period in periods
            ]
        else:
            periods = (
                [
                    "Monday",
                    "Tuesday",
                    "Wednesday",
                    "Thursday",
                    "Friday",
                    "Saturday",
                    "Sunday",
                ]
                if self.content_name == "Day"
                else [
                    "January",
                    "February",
                    "March",
                    "April",
                    "May",
                    "June",
                    "July",
                    "August",
                    "September",
                    "October",
                    "November",
                    "December",
                ]
            )
            keys = [f"Last {period}" for period in periods] + periods
        return {
            keys[index] if index < len(keys) else str(index): value
            if value is not None
            else "-"
            for index, value in enumerate(values)
        }

    @property
    def native_value(self) -> StateType:
        return _find_last_value(self._consumption_content())

    @property
    def device_info(self):
        return self._attr_device_info
