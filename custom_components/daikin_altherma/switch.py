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
    async_add_entities([
        AlthermaUnitPowerSwitch(coordinator, api)
    ], update_before_add=False)


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
    def extra_state_attributes(self):
        return self._api.status["function/SpaceHeating"]['states']
