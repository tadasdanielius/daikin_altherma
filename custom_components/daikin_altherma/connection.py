"""Cleanup and status decoding for the pinned pyaltherma transport."""

import asyncio
import logging

from pyaltherma.comm import DaikinWSConnection as BaseConnection

from .const import ASYNC_UPDATE_TIMEOUT_SECONDS

_LOGGER = logging.getLogger(__name__)

BOOLEAN_STATES = frozenset(
    {
        "ErrorState",
        "WarningState",
        "EmergencyState",
        "InstallerState",
        "WeatherDependentState",
        "TargetTemperatureOverruledState",
    }
)


class DaikinWSConnection(BaseConnection):
    """Handle protocol flags and cleanup before the first successful connection."""

    async def request(self, dest, *args, **kwargs):
        response = await super().request(dest, *args, **kwargs)
        state = dest.rsplit("/UnitStatus/", 1)[-1].removesuffix("/la")
        if (
            "/UnitStatus/" in dest
            and state in BOOLEAN_STATES
            and isinstance(response, dict)
        ):
            # pyaltherma 0.0.21 calls bool(con); bool("0") and bool("false")
            # are True. Normalize flags before that conversion loses the value.
            content = response.get("m2m:rsp", {}).get("pc", {}).get("m2m:cin", {})
            if "con" in content:
                content["con"] = state_flag(content["con"])
        return response

    async def close(self):
        # pyaltherma 0.0.21 dereferences _client even before connect succeeds.
        if self._client is not None:
            await super().close()


async def async_close_connection(connection):
    """Bound cleanup without hiding the original request error or cancellation."""
    try:
        async with asyncio.timeout(ASYNC_UPDATE_TIMEOUT_SECONDS):
            await connection.close()
    except Exception:
        _LOGGER.warning("Failed to close Daikin connection", exc_info=True)


def state_flag(value):
    """Decode a protocol boolean without treating nonempty false strings as true."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        value = value.strip().casefold()
        if value in ("0", "false"):
            return False
        if value in ("1", "true"):
            return True
    raise ValueError("Invalid Daikin status flag")
