"""Temperature controls selected from the adapter's advertised capabilities."""

from homeassistant.components.number import NumberEntity
from homeassistant.const import UnitOfTemperature

from . import DOMAIN, AlthermaAPI, validate_temperature
from .entity import AlthermaEntity


def _numeric_config(config):
    return isinstance(config, dict) and all(
        key in config for key in ("minValue", "maxValue")
    )


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    api, coordinator = data.api, data.coordinator
    climate = api.device.climate_control
    if climate is None or climate.unit is None:
        return
    schema = climate.unit.operation_config
    entities = []
    if any(
        _numeric_config(schema.get(f"LeavingWaterTemperature{offset}{mode}"))
        for offset in ("", "Offset")
        for mode in ("Heating", "Cooling", "Auto")
    ):
        entities.append(AlthermaUnitTemperatureControl(coordinator, api))
    for period in ("Day", "Night"):
        operation = f"TargetTemperature{period}"
        if _numeric_config(schema.get(operation)):
            entities.append(
                GenericOperationControl(
                    coordinator, api, f"Target Temperature {period}", operation
                )
            )
    if any(
        _numeric_config(schema.get(f"RoomTemperature{mode}"))
        for mode in ("Heating", "Cooling", "Auto")
    ):
        entities.append(RoomTemperatureOperationControl(coordinator, api))
    async_add_entities(entities, update_before_add=False)


class TemperatureControl(AlthermaEntity, NumberEntity):
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_icon = "mdi:sun-thermometer-outline"
    _attr_mode = "box"

    def __init__(self, coordinator, api: AlthermaAPI):
        super().__init__(coordinator, api)
        self._attr_device_info = api.space_heating_device_info

    def _get_value_config(self):
        raise NotImplementedError

    @property
    def available(self):
        key, config = self._get_value_config()
        return (
            super().available
            and key is not None
            and _numeric_config(config)
            and config.get("settable", True)
            and key in self._api.space_heating_status.get("operations", {})
        )

    @property
    def native_value(self):
        key, _ = self._get_value_config()
        return self._api.space_heating_status.get("operations", {}).get(key)

    @property
    def native_min_value(self):
        return self._get_value_config()[1].get("minValue", super().native_min_value)

    @property
    def native_max_value(self):
        return self._get_value_config()[1].get("maxValue", super().native_max_value)

    @property
    def native_step(self):
        return self._get_value_config()[1].get("stepValue", super().native_step)

    async def async_set_native_value(self, value):
        key, config = self._get_value_config()
        value = validate_temperature(value, config)
        await self._api.async_execute(
            self._api.device.climate_control.call_operation, key, value
        )
        await self.coordinator.async_request_refresh()

    def _mode_config(self, prefix):
        climate = self._api.device.climate_control
        if climate is None or climate.unit is None:
            return None, {}
        mode = self._api.space_heating_status.get("operations", {}).get("OperationMode")
        if not isinstance(mode, str):
            return None, {}
        key = f"{prefix}{mode.capitalize()}"
        config = climate.unit.operation_config.get(key)
        return (key, config) if _numeric_config(config) else (None, {})


class GenericOperationControl(TemperatureControl):
    def __init__(self, coordinator, api, name, operation):
        super().__init__(coordinator, api)
        self._operation = operation
        self._attr_name = name
        self._attr_unique_id = (
            f"{api.info['serial_number']}-{operation}-SpaceHeating-control"
        )

    def _get_value_config(self):
        climate = self._api.device.climate_control
        if climate is None or climate.unit is None:
            return None, {}
        config = climate.unit.operation_config.get(self._operation)
        return (self._operation, config) if _numeric_config(config) else (None, {})


class RoomTemperatureOperationControl(TemperatureControl):
    def __init__(self, coordinator, api):
        super().__init__(coordinator, api)
        self._attr_name = "Room Temperature"
        self._attr_unique_id = f"{api.info['serial_number']}-SpaceHeating-room-temp"

    def _get_value_config(self):
        return self._mode_config("RoomTemperature")


class AlthermaUnitTemperatureControl(TemperatureControl):
    def __init__(self, coordinator, api):
        super().__init__(coordinator, api)
        self._attr_name = "Temperature Control"
        self._attr_unique_id = f"{api.info['serial_number']}-SpaceHeating-temp-control"

    def _get_value_config(self):
        fixed_key, fixed = self._mode_config("LeavingWaterTemperature")
        offset_key, offset = self._mode_config("LeavingWaterTemperatureOffset")
        if fixed_key and fixed.get("settable", True):
            return fixed_key, fixed
        if offset_key and offset.get("settable", True):
            return offset_key, offset
        return None, {}
