import logging
from homeassistant.components.switch import SwitchEntity, DEVICE_CLASS_SWITCH
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
)

from . import DOMAIN, AlthermaAPI

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Daikin climate based on config_entry."""
    api = hass.data[DOMAIN].get(entry.entry_id)
    coordinator = hass.data[DOMAIN]['coordinator']
    entities = [AlthermaUnitPowerSwitch(coordinator, api)]

    climate_control = api.device.climate_control
    operations = climate_control.unit.operations
    if 'EcoMode' in operations:
        eco_switch = AlthermaOperationSwitch(
            coordinator, api,
            operation='EcoMode',
            unit_function=climate_control.unit_function,
            states=['1', '0'],
            attr_name="EcoMode"
        )
        entities.append(eco_switch)
    async_add_entities(entities, update_before_add=False)


class AlthermaOperationSwitch(SwitchEntity, CoordinatorEntity):
    _attr_device_class = DEVICE_CLASS_SWITCH

    def __init__(self, coordinator, api: AlthermaAPI,
                 operation, unit_function,
                 states=["0", "1"],
                 attr_name='undefined',
                 icon="mdi:toggle-switch"):

        super().__init__(coordinator)
        self._api = api
        self._attr_name = attr_name
        self._attr_device_info = api.space_heating_device_info
        self._attr_unique_id = f"{self._api.info['serial_number']}-SpaceHeating-{attr_name}"
        self._state = None
        self._attr_icon = icon
        self._unit_function = unit_function
        self._operation = operation
        self._states = states

    async def async_turn_on(self, **kwargs) -> None:
        await self._set_state(1)

    async def _set_state(self, state):
        device = self._api.device
        controller = device.altherma_units[self._unit_function]
        await controller.call_operation(self._operation, state)
        self._state = state
        await self.coordinator.async_request_refresh()
        await self._api.device.ws_connection.close()

    async def async_turn_off(self, **kwargs) -> None:
        await self._set_state(0)

    @property
    def is_on(self) -> bool:
        # _op_state = self._api.status[self._controller.unit_function]['operations']
        _op_state = self._api.status[self._unit_function]['operations']
        if self._operation in _op_state:
            state = _op_state[self._operation]
            return str(state) == self._states[0]
        else:
            return None

    @property
    def available(self):
        return self._api.available

    @property
    def device_info(self):
        return self._attr_device_info

    async def async_update(self):
        await self._api.async_update()

    async def async_toggle(self, **kwargs) -> None:
        state = self.is_on
        if state is not None:
            if state == self._states[1]:
                await self.turn_on()
            else:
                await self.turn_off()


class AlthermaUnitPowerSwitch(SwitchEntity, CoordinatorEntity):
    _attr_device_class = DEVICE_CLASS_SWITCH

    def __init__(self, coordinator, api: AlthermaAPI):
        super().__init__(coordinator)
        self._api = api
        self._attr_name = 'Climate Control'
        self._attr_device_info = api.space_heating_device_info
        self._attr_unique_id = f"{self._api.info['serial_number']}-SpaceHeating-power-switch"
        self._state = None
        self._attr_icon = 'mdi:power'

    async def async_turn_on(self, **kwargs) -> None:
        await self._api.turn_on_climate_control()

        self._state = True
        await self.coordinator.async_request_refresh()
        await self._api.device.ws_connection.close()

    async def async_turn_off(self, **kwargs) -> None:
        await self._api.turn_off_climate_control()

        self._state = False
        await self.coordinator.async_request_refresh()
        await self._api.device.ws_connection.close()

    async def async_toggle(self, **kwargs) -> None:
        is_on = await self._api.async_is_climate_control_on()
        if is_on:
            await self.async_turn_off()
        else:
            await self.async_turn_on()

    @property
    def is_on(self) -> bool:
        state = self._state if self._state is not None else self._api.is_climate_control_on()
        self._state = None
        return state

    @property
    def device_info(self):
        return self._attr_device_info

    async def async_update(self):
        await self._api.async_update()

    @property
    def available(self):
        return self._api.available

    @property
    def extra_state_attributes(self):
        return self._api.status["function/SpaceHeating"]['states']
