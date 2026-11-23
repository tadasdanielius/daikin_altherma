import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)

from . import DOMAIN, AlthermaAPI
from .connection import state_flag
from .entity import AlthermaEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Daikin climate based on config_entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    api = data.api
    coordinator = data.coordinator
    entities = []
    if api.space_heating_device_info is not None:
        entities.append(
            AlthermaUnitProblemSensor(
                coordinator,
                api,
                "Space Heating Unit State",
                api.space_heating_device_info,
                "SpaceHeating",
            )
        )

    if api.HWT_device_info is not None:
        hwt = api.device.hot_water_tank
        unit_function = hwt.unit_function
        unit_ref = unit_function.split("/")[1]
        entities.append(
            AlthermaUnitProblemSensor(
                coordinator,
                api,
                "Hot Water Tank State",
                api.HWT_device_info,
                # 'DomesticHotWaterTank'
                unit_ref,
            )
        )
    async_add_entities(entities, update_before_add=False)


class AlthermaUnitProblemSensor(AlthermaEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator, api: AlthermaAPI, name: str, device_info, unit_ref):
        super().__init__(coordinator, api)
        self._api = api
        self._attr_name = name
        self._attr_device_info = device_info
        self._attr_unique_id = (
            f"{self._api.info['serial_number']}-{unit_ref}-problem_sensor"
        )
        self._state = None
        self._unit_ref = unit_ref

    @property
    def device_info(self):
        return self._attr_device_info

    @property
    def is_on(self):
        return self._is_problem_state()

    def _is_problem_state(self):
        states = self._api.status.get(f"function/{self._unit_ref}", {}).get(
            "states", {}
        )
        flags = []
        # Installer, weather-dependent and overridden-target states are status
        # information, not equipment faults. Keep them in the entity attributes.
        for key in ("ErrorState", "WarningState", "EmergencyState"):
            if key in states:
                try:
                    flags.append(state_flag(states[key]))
                except ValueError:
                    flags.append(None)
        if True in flags:
            return True
        if not flags or None in flags:
            return None
        return False

    @property
    def extra_state_attributes(self):
        return self._api.status.get(f"function/{self._unit_ref}", {}).get("states", {})
