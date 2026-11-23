"""Setup feedback distinguishes input, connectivity and protocol failures."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import ClientConnectionError, WSServerHandshakeError
from pyaltherma.errors import AlthermaException

from custom_components.daikin_altherma import config_flow


@pytest.mark.parametrize(
    "value, expected",
    [
        (" 192.168.1.50 ", "192.168.1.50"),
        ("daikin.local", "daikin.local"),
        ("adapter", "adapter"),
        ("adapter.local:8080", "adapter.local:8080"),
        ("192.168.1.50:80", "192.168.1.50:80"),
        ("2001:db8::1", "[2001:db8::1]"),
        ("[2001:db8::1]", "[2001:db8::1]"),
        ("[2001:db8::1]:8080", "[2001:db8::1]:8080"),
    ],
)
async def test_normalized_host_is_used_and_saved(value, expected):
    flow = make_flow()
    flow._async_get_device_info = AsyncMock(return_value=DEVICE_INFO)
    result = await flow.async_step_user({"host": value})
    assert flow.host == expected
    assert result["data"] == {"host": expected}
    flow._abort_if_unique_id_configured.assert_called_once_with(
        updates={"host": expected}
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "  ",
        "http://192.168.1.50",
        "ws://adapter/mca",
        "adapter/mca",
        "adapter/",
        "user@adapter",
        "adapter?foo",
        "adapter#foo",
        "adapter\\path",
        "bad host",
        "adapter:abc",
        "adapter:0",
        "adapter:65536",
        "adapter:",
        "[2001:db8::1]junk",
        "[2001:db8::1",
        "[bad-address]",
        "-adapter",
        "adapter..local",
    ],
)
async def test_invalid_host_does_not_attempt_connection(value):
    flow = make_flow()
    flow._async_get_device_info = AsyncMock()
    result = await flow.async_step_user({"host": value})
    assert result["errors"] == {"host": "invalid_host"}
    flow._async_get_device_info.assert_not_awaited()


DEVICE_INFO = {"serial_number": "serial", "manufacturer": "Daikin", "duty": "Heating"}


def make_flow():
    flow = config_flow.DaikinAlthermaConfigFlow()
    flow.hass = Mock()
    flow.context = {}
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = Mock()
    flow._async_abort_entries_match = Mock()
    return flow


@pytest.mark.parametrize("step", ["user", "zeroconf"])
@pytest.mark.parametrize(
    "failure, reason",
    [
        (TimeoutError(), "timeout"),
        (ClientConnectionError("connection refused"), "cannot_connect"),
        (OSError("name resolution failed"), "cannot_connect"),
        (
            WSServerHandshakeError(Mock(real_url="ws://adapter/mca"), (), status=404),
            "invalid_response",
        ),
        (AlthermaException("unexpected response"), "invalid_response"),
        (ValueError("incomplete device info"), "invalid_response"),
        (KeyError("m2m:rsp"), "invalid_response"),
        (TypeError("bad response"), "invalid_response"),
    ],
)
async def test_connection_feedback_and_cleanup(
    monkeypatch, caplog, step, failure, reason
):
    connection = SimpleNamespace(close=AsyncMock())
    device = SimpleNamespace(discover_units=AsyncMock(side_effect=failure))
    monkeypatch.setattr(config_flow, "DaikinWSConnection", lambda *a: connection)
    monkeypatch.setattr(config_flow, "AlthermaController", lambda *a: device)
    monkeypatch.setattr(config_flow, "async_get_clientsession", lambda *a: None)
    flow = make_flow()
    if step == "user":
        result = await flow.async_step_user({"host": "adapter"})
        assert result["errors"] == {"base": reason}
        assert result["data_schema"]({}) == {"host": "adapter"}
    else:
        result = await flow.async_step_zeroconf(SimpleNamespace(host="adapter"))
        assert result["reason"] == reason
    connection.close.assert_awaited_once()
    assert "Daikin setup at adapter failed" in caplog.text
    assert type(failure).__name__ in caplog.text


async def test_setup_cancellation_is_not_reported_as_connection_failure(monkeypatch):
    connection = SimpleNamespace(close=AsyncMock())
    device = SimpleNamespace(
        discover_units=AsyncMock(side_effect=asyncio.CancelledError())
    )
    monkeypatch.setattr(config_flow, "DaikinWSConnection", lambda *a: connection)
    monkeypatch.setattr(config_flow, "AlthermaController", lambda *a: device)
    monkeypatch.setattr(config_flow, "async_get_clientsession", lambda *a: None)
    with pytest.raises(asyncio.CancelledError):
        await make_flow().async_step_user({"host": "adapter"})
    connection.close.assert_awaited_once()


async def test_ipv6_discovery_uses_bracketed_host():
    flow = make_flow()
    flow._async_get_device_info = AsyncMock(return_value=DEVICE_INFO)
    await flow.async_step_zeroconf(SimpleNamespace(host="2001:db8::1"))
    result = await flow.async_step_zeroconf_confirm({})
    assert result["data"] == {"host": "[2001:db8::1]"}


def test_all_setup_feedback_is_translated():
    root = Path(config_flow.__file__).parent
    for path in (root / "strings.json", root / "translations/en.json"):
        config = json.loads(path.read_text())["config"]
        assert "adapter" in config["step"]["user"]["description"].lower()
        for reason in ("timeout", "cannot_connect", "invalid_response"):
            assert config["error"][reason]
            assert config["abort"][reason]
        assert config["error"]["invalid_host"]
        assert "already configured" in config["abort"]["already_configured"]
