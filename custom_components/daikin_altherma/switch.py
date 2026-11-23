import logging

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.const import EntityCategory

from . import DOMAIN, AlthermaAPI
from .entity import AlthermaEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Daikin climate based on config_entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    api = data.api
    coordinator = data.coordinator
    entities = []

    climate_control = api.device.climate_control
    operations = (
        climate_control.unit.operations
        if climate_control and climate_control.unit
        else {}
    )
    if "Power" in operations:
        entities.append(AlthermaUnitPowerSwitch(coordinator, api))
    if "EcoMode" in operations:
        eco_switch = AlthermaOperationSwitch(
            coordinator,
            api,
            operation="EcoMode",
            unit_function=climate_control.unit_function,
            states=["0", "1"],
            attr_name="EcoMode",
        )
        entities.append(eco_switch)
    tank = api.device.hot_water_tank
    if api.tank_standby_support and {"on", "standby"}.issubset(
        tank.unit.operations.get("Power") or []
    ):
        entities.append(
            AlthermaOperationSwitch(
                coordinator,
                api,
                "Power",
                tank.unit_function,
                states=("standby", "on"),
                values=("standby", "on"),
                attr_name="Power",
                icon="mdi:power",
                device_info=api.HWT_device_info,
                entity_category=EntityCategory.DIAGNOSTIC,
            )
        )
    if entities:
        async_add_entities(entities, update_before_add=False)


class AlthermaOperationSwitch(AlthermaEntity, SwitchEntity):
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(
        self,
        coordinator,
        api: AlthermaAPI,
        operation,
        unit_function,
        states=("0", "1"),
        attr_name="undefined",
        icon="mdi:toggle-switch",
        values=(0, 1),
        device_info=None,
        entity_category=None,
    ):

        super().__init__(coordinator, api)
        self._api = api
        self._attr_name = attr_name
        self._attr_entity_category = entity_category
        self._values = values
        self._attr_device_info = device_info or api.space_heating_device_info
        self._attr_unique_id = f"{self._api.info['serial_number']}-{unit_function.split('/')[-1]}-{attr_name}"
        self._attr_icon = icon
        self._unit_function = unit_function
        self._operation = operation
        self._states = states

    async def async_turn_on(self, **kwargs) -> None:
        await self._set_state(self._values[1])

    async def _set_state(self, state):
        device = self._api.device
        controller = device.altherma_units[self._unit_function]
        # Some adapters advertise EcoMode without numeric range metadata.
        await self._api.async_execute(
            controller.call_operation, self._operation, state, validate=False
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self._set_state(self._values[0])

    @property
    def is_on(self) -> bool:
        _op_state = self._api.status.get(self._unit_function, {}).get("operations", {})

        if self._operation in _op_state:
            state = _op_state[self._operation]
            return str(state) == self._states[1]
        else:
            _LOGGER.error(f"Op {self._operation} is not in the op state {_op_state}")
            return None

    @property
    def device_info(self):
        return self._attr_device_info

    async def async_toggle(self, **kwargs) -> None:
        state = self.is_on
        if state is not None:
            if state:
                await self.async_turn_off()
            else:
                await self.async_turn_on()
        else:
            _LOGGER.warning(
                f"{self._unit_function}[{self._operation}] unable to determine current state."
            )


class AlthermaUnitPowerSwitch(AlthermaEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(self, coordinator, api: AlthermaAPI):
        super().__init__(coordinator, api)
        self._api = api
        self._attr_name = "Climate Control"
        self._attr_device_info = api.space_heating_device_info
        self._attr_unique_id = (
            f"{self._api.info['serial_number']}-SpaceHeating-power-switch"
        )
        self._attr_icon = "mdi:power"

    async def async_turn_on(self, **kwargs) -> None:
        await self._api.turn_on_climate_control()

        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self._api.turn_off_climate_control()

        await self.coordinator.async_request_refresh()

    async def async_toggle(self, **kwargs) -> None:
        await self.coordinator.async_request_refresh()
        is_on = self._api.is_climate_control_on()
        if is_on is None:
            return
        if is_on:
            await self.async_turn_off()
        else:
            await self.async_turn_on()

    @property
    def is_on(self) -> bool:
        return self._api.is_climate_control_on()

    @property
    def device_info(self):
        return self._attr_device_info

    @property
    def extra_state_attributes(self):
        return self._api.space_heating_status.get("states", {})
