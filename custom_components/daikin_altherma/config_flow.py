"""Config flow for Daikin Altherma."""
# import my_pypi_dependency
from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import ClientError
from async_timeout import timeout
from pyaltherma.comm import DaikinWSConnection
from pyaltherma.controllers import AlthermaController
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.typing import DiscoveryInfoType

from .const import DOMAIN, TIMEOUT

_LOGGER = logging.getLogger(__name__)


class DaikinAlthermaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self.device_info: dict = None
        self.host: str | None = None

    @property
    def schema(self):
        """Return current schema."""
        return vol.Schema(
            {
                vol.Required(CONF_HOST, default=self.host): str,
            }
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors = {}
        if user_input is not None:
            try:
                with timeout(TIMEOUT):
                    self.host = user_input[CONF_HOST]
                    conn = DaikinWSConnection(
                        self.hass.helpers.aiohttp_client.async_get_clientsession(),
                        self.host,
                    )
                    device = AlthermaController(conn)
                    await device.discover_units()
                    self.device_info = await device.device_info()
                    await conn.close()

                await self.async_set_unique_id(self.device_info["serial_number"])
                self._abort_if_unique_id_configured()

                title = f"{self.device_info['manufacturer']} {self.device_info['duty']} ({self.device_info['serial_number']})"
                return self.async_create_entry(title=title, data=user_input)
            except (asyncio.TimeoutError, ClientError):
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="user", data_schema=self.schema, errors=errors
        )

    async def async_step_zeroconf(
        self, discovery_info: DiscoveryInfoType
    ) -> FlowResult:
        self.host = discovery_info["host"]
        self._async_abort_entries_match({CONF_HOST: self.host})
        try:
            with timeout(TIMEOUT):
                conn = DaikinWSConnection(
                    self.hass.helpers.aiohttp_client.async_get_clientsession(),
                    self.host,
                )
                device = AlthermaController(conn)
                await device.discover_units()
                self.device_info = await device.device_info()
                await conn.close()
        except (asyncio.TimeoutError, ClientError):
            return self.async_abort(reason="cannot_connect")

        await self.async_set_unique_id(self.device_info["serial_number"])
        self._abort_if_unique_id_configured()
        self.context.update({"title_placeholders": self.device_info})
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            title = f"{self.device_info['manufacturer']} {self.device_info['duty']} ({self.device_info['serial_number']})"
            return self.async_create_entry(title=title, data={CONF_HOST: self.host})
        return self.async_show_form(
            step_id="zeroconf_confirm", description_placeholders=self.device_info
        )
