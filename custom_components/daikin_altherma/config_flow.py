"""Config flow for Daikin Altherma."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
from asyncio import timeout
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol
from aiohttp import ClientError, WSServerHandshakeError
from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import DiscoveryInfoType
from pyaltherma.controllers import AlthermaController
from pyaltherma.errors import AlthermaException

from .connection import DaikinWSConnection, async_close_connection
from .const import DOMAIN, TIMEOUT

_LOGGER = logging.getLogger(__name__)


def normalize_host(value: str) -> str:
    """Accept an adapter IP/hostname (and optional port), never a full URL."""
    host = value.strip()
    if not host or any(char.isspace() or char in "/\\@?#" for char in host):
        raise ValueError("Enter an IP address or hostname, without a URL or path")
    # pyaltherma inserts the host into ws://{host}/mca, requiring IPv6 brackets.
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return f"[{host}]" if address.version == 6 else host
    parsed = urlsplit(f"//{host}")
    name = parsed.hostname
    if not name or host.endswith(":") or parsed.port == 0:
        raise ValueError("Invalid adapter address or port")
    if ":" in name:
        if not re.fullmatch(r"\[[^\]]+\](?::[0-9]+)?", host):
            raise ValueError("Invalid IPv6 address or port")
        ipaddress.IPv6Address(name)
    else:
        ascii_name = name.encode("idna").decode("ascii").rstrip(".")
        if len(ascii_name) > 253 or not all(
            re.fullmatch(r"[a-zA-Z0-9_](?:[a-zA-Z0-9_-]{0,61}[a-zA-Z0-9_])?", label)
            for label in ascii_name.split(".")
        ):
            raise ValueError("Invalid adapter hostname")
    return host


def setup_error_reason(error: Exception) -> str:
    """Describe observed failures without guessing hardware compatibility."""
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, WSServerHandshakeError):
        return "invalid_response"
    if isinstance(error, (ClientError, OSError)):
        return "cannot_connect"
    return "invalid_response"


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

    async def _async_get_device_info(self):
        conn = DaikinWSConnection(async_get_clientsession(self.hass), self.host)
        try:
            async with timeout(TIMEOUT):
                device = AlthermaController(conn)
                await device.discover_units()
                info = await device.device_info()
                if not isinstance(info, dict) or not all(
                    info.get(key) for key in ("serial_number", "manufacturer", "duty")
                ):
                    raise ValueError("Incomplete device information")
                return info
        finally:
            await async_close_connection(conn)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors = {}
        if user_input is not None:
            self.host = user_input[CONF_HOST]
            try:
                self.host = normalize_host(self.host)
            except ValueError:
                return self.async_show_form(
                    step_id="user",
                    data_schema=self.schema,
                    errors={CONF_HOST: "invalid_host"},
                )
            try:
                self.device_info = await self._async_get_device_info()

                await self.async_set_unique_id(self.device_info["serial_number"])
                self._abort_if_unique_id_configured(updates={CONF_HOST: self.host})

                title = f"{self.device_info['manufacturer']} {self.device_info['duty']} ({self.device_info['serial_number']})"
                return self.async_create_entry(title=title, data={CONF_HOST: self.host})
            except (
                asyncio.TimeoutError,
                ClientError,
                OSError,
                AlthermaException,
                ValueError,
                TypeError,
                KeyError,
            ) as err:
                errors["base"] = self._log_setup_error(err)
        return self.async_show_form(
            step_id="user", data_schema=self.schema, errors=errors
        )

    async def async_step_zeroconf(
        self, discovery_info: DiscoveryInfoType
    ) -> FlowResult:
        self.host = normalize_host(discovery_info.host)
        self._async_abort_entries_match({CONF_HOST: self.host})
        try:
            self.device_info = await self._async_get_device_info()
        except (
            asyncio.TimeoutError,
            ClientError,
            OSError,
            AlthermaException,
            ValueError,
            TypeError,
            KeyError,
        ) as err:
            return self.async_abort(reason=self._log_setup_error(err))

        await self.async_set_unique_id(self.device_info["serial_number"])
        self._abort_if_unique_id_configured(updates={CONF_HOST: self.host})
        self.context.update({"title_placeholders": self.device_info})
        return await self.async_step_zeroconf_confirm()

    def _log_setup_error(self, error: Exception) -> str:
        reason = setup_error_reason(error)
        _LOGGER.warning(
            "Daikin setup at %s failed (%s): %s: %s. "
            "Check the adapter address, network access and local API support; "
            "see the integration README troubleshooting section.",
            self.host,
            reason,
            type(error).__name__,
            error,
        )
        _LOGGER.debug("Daikin setup failure details", exc_info=True)
        return reason

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            title = f"{self.device_info['manufacturer']} {self.device_info['duty']} ({self.device_info['serial_number']})"
            return self.async_create_entry(title=title, data={CONF_HOST: self.host})
        return self.async_show_form(
            step_id="zeroconf_confirm", description_placeholders=self.device_info
        )
