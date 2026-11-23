import logging

from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import HomeAssistantError

from . import DOMAIN, AlthermaAPI
from .entity import AlthermaEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Daikin climate based on config_entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    api = data.api

    coordinator = data.coordinator
    climate = api.device.climate_control
    if climate is None or climate.unit is None:
        return
    modes = climate.unit.operations.get("OperationMode")
    if not isinstance(modes, list) or not modes:
        return
    async_add_entities(
        [AlthermaUnitOperationMode(coordinator, api)], update_before_add=False
    )


class AlthermaUnitOperationMode(AlthermaEntity, SelectEntity):
    def __init__(self, coordinator, api: AlthermaAPI):
        super().__init__(coordinator, api)
        self._api = api
        self._attr_name = "Operation Mode"
        self._attr_device_info = api.space_heating_device_info
        self._attr_unique_id = (
            f"{self._api.info['serial_number']}-SpaceHeating-power-mode"
        )

        self._attr_icon = "mdi:sun-snowflake"

    @property
    def options(self):
        climate = self._api.device.climate_control
        if climate is None or climate.unit is None:
            return []
        modes = climate.unit.operations.get("OperationMode", [])
        return modes if isinstance(modes, list) else []

    @property
    def current_option(self) -> str:
        status: dict = self._api.space_heating_status
        operations = status.get("operations", {})
        mode = operations.get("OperationMode", None)
        return mode

    async def async_select_option(self, option: str) -> None:
        modes = self._api.device.climate_control.unit.operations.get(
            "OperationMode", []
        )
        if option not in modes:
            raise HomeAssistantError("Operation mode is not supported")
        await self._api.async_execute(
            self._api.device.climate_control.call_operation, "OperationMode", option
        )
        await self.coordinator.async_request_refresh()

    @property
    def device_info(self):
        return self._attr_device_info
