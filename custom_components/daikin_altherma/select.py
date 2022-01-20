import logging
from homeassistant.components.select import SelectEntity
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
)
from pyaltherma.const import ClimateControlMode

from . import DOMAIN, AlthermaAPI

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Daikin climate based on config_entry."""
    api = hass.data[DOMAIN].get(entry.entry_id)

    coordinator = hass.data[DOMAIN]['coordinator']
    async_add_entities([
        AlthermaUnitOperationMode(coordinator, api)
    ], update_before_add=False)


class AlthermaUnitOperationMode(SelectEntity, CoordinatorEntity):

    def __init__(self, coordinator, api: AlthermaAPI):
        super().__init__(coordinator)
        self._api = api
        self._attr_name = 'Operation Mode'
        self._attr_device_info = api.space_heating_device_info
        self._attr_unique_id = f"{self._api.info['serial_number']}-SpaceHeating-power-mode"
        self._attr_options = [x.value for x in list(ClimateControlMode)]
        self._attr_icon = 'mdi:sun-snowflake'

    @property
    def current_option(self) -> str:
        status: dict = self._api.space_heating_status
        operations = status.get('operations', {})
        mode = operations.get('OperationMode', None)
        return mode

    async def async_select_option(self, option: str) -> None:
        new_op = ClimateControlMode(option)
        await self._api.device.climate_control.set_operation_mode(new_op)
        await self.coordinator.async_refresh()

    @property
    def device_info(self):
        return self._attr_device_info

    async def async_update(self):
        await self._api.async_update()
