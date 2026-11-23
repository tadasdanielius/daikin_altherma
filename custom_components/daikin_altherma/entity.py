"""Shared coordinator behavior for Daikin entities."""

from homeassistant.helpers.update_coordinator import CoordinatorEntity


class AlthermaEntity(CoordinatorEntity):
    def __init__(self, coordinator, api):
        super().__init__(coordinator)
        self._api = api

    @property
    def available(self):
        return super().available and self._api.available
