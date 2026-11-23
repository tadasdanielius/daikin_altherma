"""Behavioral coverage for different profiles and failed device communication."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import ClientConnectionError
from homeassistant.config_entries import ConfigEntryState, current_entry
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

import custom_components.daikin_altherma as integration
from custom_components.daikin_altherma import (
    AlthermaEntryData,
    binary_sensor,
    create_update_function,
    number,
    select,
    sensor,
    switch,
    water_heater,
)
from custom_components.daikin_altherma.config_flow import DaikinAlthermaConfigFlow
from custom_components.daikin_altherma.connection import DaikinWSConnection
from custom_components.daikin_altherma.const import DOMAIN
from custom_components.daikin_altherma.water_heater import AlthermaWaterHeater


@pytest.mark.parametrize(
    "key,nested,function",
    [
        ("DomesticHotWaterTemperatureHeating", False, "function/DomesticHotWaterTank"),
        ("TargetTemperature", True, "function/DomesticHotWater"),
        ("TargetTemperature", False, "function/DomesticHotWater"),
    ],
)
async def test_water_temperature_profiles(
    api_factory, coordinator, key, nested, function
):
    config = {"minValue": 30, "maxValue": 60, "stepValue": 1}
    api = api_factory(
        schema={key: {"heating": config} if nested else config}, function=function
    )
    heater = AlthermaWaterHeater(coordinator, api)
    assert heater.native_target_temperature == 45
    assert (heater.min_temp, heater.max_temp) == (30, 60)
    assert heater.unique_id == "test-serial-heater"
    await heater.async_set_temperature(temperature=48)
    api.device.hot_water_tank.call_operation.assert_awaited_once_with(key, 48.0)
    api.device.ws_connection.close.assert_awaited_once()
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.parametrize("value", [None, "bad", float("nan"), float("inf"), 29, 61])
async def test_invalid_temperature_never_sent(api_factory, coordinator, value):
    api = api_factory()
    with pytest.raises(HomeAssistantError):
        await AlthermaWaterHeater(coordinator, api).async_set_temperature(
            temperature=value
        )
    api.device.hot_water_tank.call_operation.assert_not_awaited()


@pytest.mark.parametrize("weather_dependent", [True, False])
async def test_read_only_temperature(api_factory, coordinator, weather_dependent):
    api = api_factory()
    if weather_dependent:
        api.water_tank_status["states"]["WeatherDependentState"] = True
    else:
        api.water_tank_target_temp_config["settable"] = False
    heater = AlthermaWaterHeater(coordinator, api)
    assert (
        not heater.supported_features
        & water_heater.WaterHeaterEntityFeature.TARGET_TEMPERATURE
    )
    with pytest.raises(HomeAssistantError):
        await heater.async_set_temperature(temperature=48)
    api.device.hot_water_tank.call_operation.assert_not_awaited()


async def test_water_toggle_uses_property(api_factory, coordinator):
    api = api_factory()
    api.water_tank_status["operations"]["Power"] = "standby"
    await AlthermaWaterHeater(coordinator, api).async_toggle()
    api.device.hot_water_tank.turn_on.assert_awaited_once()


@pytest.mark.parametrize(
    "powerful,expected",
    [(0, "on"), ("0", "on"), (1, "performance"), ("1", "performance")],
)
def test_powerful_values(api_factory, powerful, expected):
    api = api_factory()
    api.water_tank_status["operations"]["powerful"] = powerful
    assert api.water_tank_operation == expected


async def test_unsupported_performance_never_turns_on(api_factory):
    api = api_factory(schema={"Power": ["on", "standby"]})
    with pytest.raises(HomeAssistantError):
        await api.async_set_water_tank_state("performance")
    api.device.hot_water_tank.turn_on.assert_not_awaited()


@pytest.mark.parametrize(
    "failure",
    [TypeError("bad response"), ClientConnectionError("offline"), TimeoutError()],
)
async def test_poll_failure_and_recovery(api_factory, coordinator, failure):
    api = api_factory()
    previous = deepcopy(api.status)
    heater = AlthermaWaterHeater(coordinator, api)
    api.device.get_current_state.side_effect = failure
    with pytest.raises(UpdateFailed):
        await create_update_function(api)()
    assert not heater.available
    assert api.status == previous
    api.device.ws_connection.close.assert_awaited_once()
    api.device.get_current_state.side_effect = None
    await create_update_function(api)()
    assert heater.available


async def test_cancellation_propagates_and_closes(api_factory):
    api = api_factory()
    entered = asyncio.Event()

    async def blocked():
        entered.set()
        await asyncio.Event().wait()

    api.device.get_current_state.side_effect = blocked
    task = asyncio.create_task(api.async_update())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not api.available
    api.device.ws_connection.close.assert_awaited_once()
    assert not api._lock.locked()


async def test_poll_timeout(api_factory, monkeypatch):
    api = api_factory()

    original_timeout = asyncio.timeout
    monkeypatch.setattr(
        integration.asyncio, "timeout", lambda _: original_timeout(0.01)
    )
    api.device.get_current_state.side_effect = lambda: None

    async def blocked():
        await asyncio.Event().wait()

    api.device.get_current_state.side_effect = blocked
    with pytest.raises(UpdateFailed):
        await create_update_function(api)()
    assert not api.available
    api.device.ws_connection.close.assert_awaited_once()


async def test_installer_exit_refreshes_before_publishing(api_factory):
    api = api_factory()
    api.space_heating_status["states"]["InstallerState"] = True
    new = deepcopy(api.status)
    new["function/SpaceHeating"]["states"]["InstallerState"] = False
    refreshed = deepcopy(new)
    refreshed["function/SpaceHeating"]["operations"][
        "LeavingWaterTemperatureHeating"
    ] = 40
    api.device.get_current_state.side_effect = [new, refreshed]
    await api.async_update()
    api.device.refresh.assert_awaited_once()
    assert api.status == refreshed


async def test_hot_water_only_poll_and_platforms(api_factory, coordinator):
    api = api_factory(climate=False)
    await api.async_update()
    assert api.available
    assert api.is_climate_control_on() is None
    hass = SimpleNamespace(data={DOMAIN: {"one": AlthermaEntryData(api, coordinator)}})
    entry = SimpleNamespace(entry_id="one")
    for platform in (number, select, switch):
        add = Mock()
        await platform.async_setup_entry(hass, entry, add)
        add.assert_not_called()
    add = Mock()
    await water_heater.async_setup_entry(hass, entry, add)
    assert len(add.call_args.args[0]) == 1


async def test_fixed_only_space_temperature_control(api_factory, coordinator):
    api = api_factory(tank=False)
    hass = SimpleNamespace(data={DOMAIN: {"one": AlthermaEntryData(api, coordinator)}})
    add = Mock()
    await number.async_setup_entry(hass, SimpleNamespace(entry_id="one"), add)
    control = add.call_args.args[0][0]
    assert control.unique_id == "test-serial-SpaceHeating-temp-control"
    assert control.native_value == 35
    assert control.available
    await control.async_set_native_value(40)
    api.device.climate_control.call_operation.assert_awaited_once_with(
        "LeavingWaterTemperatureHeating", 40.0
    )


async def test_mode_switch_and_profile_refresh(api_factory, coordinator):
    api = api_factory()
    control = number.AlthermaUnitTemperatureControl(coordinator, api)
    schema = api.device.climate_control.unit.operation_config
    schema["LeavingWaterTemperatureHeating"]["settable"] = False
    schema["LeavingWaterTemperatureOffsetHeating"] = {
        "minValue": -10,
        "maxValue": 10,
        "stepValue": 1,
        "settable": True,
    }
    api.space_heating_status["operations"]["LeavingWaterTemperatureOffsetHeating"] = 2
    assert control.available
    assert (
        control.native_value,
        control.native_min_value,
        control.native_max_value,
    ) == (2, -10, 10)
    await control.async_set_native_value(3)
    api.device.climate_control.call_operation.assert_awaited_once_with(
        "LeavingWaterTemperatureOffsetHeating", 3.0
    )
    api.space_heating_status["operations"]["OperationMode"] = "cooling"
    assert not control.available
    assert control.native_value is None
    assert control.native_min_value is not None
    with pytest.raises(HomeAssistantError):
        await control.async_set_native_value(5)


async def test_mode_not_in_library_enum_is_supported(api_factory, coordinator):
    api = api_factory()
    api.device.climate_control.unit.operations["OperationMode"] = [
        "heating",
        "custom_mode",
    ]
    entity = select.AlthermaUnitOperationMode(coordinator, api)
    await entity.async_select_option("custom_mode")
    api.device.climate_control.call_operation.assert_awaited_once_with(
        "OperationMode", "custom_mode"
    )
    with pytest.raises(HomeAssistantError):
        await entity.async_select_option("cooling")


async def test_poll_and_command_do_not_interleave(api_factory):
    api = api_factory()
    entered, release = asyncio.Event(), asyncio.Event()

    async def read():
        entered.set()
        await release.wait()
        return api.status

    api.device.get_current_state.side_effect = read
    poll = asyncio.create_task(api.async_update())
    await entered.wait()
    command = asyncio.create_task(api.turn_on_climate_control())
    await asyncio.sleep(0)
    api.device.climate_control.turn_on.assert_not_awaited()
    release.set()
    await asyncio.gather(poll, command)
    api.device.climate_control.turn_on.assert_awaited_once()
    assert api.device.ws_connection.close.await_count == 2


async def test_failed_command_closes_connection(api_factory):
    api = api_factory()
    api.device.climate_control.turn_on.side_effect = ClientConnectionError()
    with pytest.raises(HomeAssistantError):
        await api.turn_on_climate_control()
    api.device.ws_connection.close.assert_awaited_once()


async def test_close_before_connect():
    await DaikinWSConnection(Mock(), "adapter").close()


@pytest.mark.parametrize("content", [[], [None], [1, None], [1, 2, 3]])
def test_partial_consumption_does_not_crash(api_factory, coordinator, content):
    api = api_factory()
    api.space_heating_status["consumption"] = {
        "Electrical": {"Heating": {"D": content}}
    }
    entity = sensor.ConsumptionSensor(
        coordinator,
        api,
        {},
        "function/SpaceHeating",
        "Heating",
        "Heating",
        "D",
        "2 Hours",
    )
    assert len(entity.extra_state_attributes) == len(content)
    assert entity.native_value == next(
        (x for x in reversed(content) if x is not None), None
    )


def test_missing_measurements_are_unknown(api_factory, coordinator):
    api = api_factory()
    entity = sensor.AlthermaUnitSensor(coordinator, api, "IndoorTemperature")
    assert entity.native_value is None
    api.water_tank_status["sensors"] = {}
    assert AlthermaWaterHeater(coordinator, api).native_current_temperature is None


@pytest.mark.parametrize("step", ["user", "zeroconf"])
async def test_config_flow_uses_async_timeout_and_cleanup(monkeypatch, step):
    import custom_components.daikin_altherma.config_flow as flow_module

    conn = SimpleNamespace(close=AsyncMock())
    device = SimpleNamespace(
        discover_units=AsyncMock(),
        device_info=AsyncMock(
            return_value={
                "serial_number": "serial",
                "manufacturer": "Daikin",
                "duty": "Heating",
            }
        ),
    )
    monkeypatch.setattr(flow_module, "DaikinWSConnection", lambda *a: conn)
    monkeypatch.setattr(flow_module, "AlthermaController", lambda *a: device)
    monkeypatch.setattr(flow_module, "async_get_clientsession", lambda *a: None)
    flow = DaikinAlthermaConfigFlow()
    flow.hass = Mock()
    flow.context = {}
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = Mock()
    flow._async_abort_entries_match = Mock()
    if step == "user":
        result = await flow.async_step_user({"host": "adapter"})
        assert result["type"] == "create_entry"
    else:
        result = await flow.async_step_zeroconf(SimpleNamespace(host="adapter"))
        assert result["step_id"] == "zeroconf_confirm"
    conn.close.assert_awaited_once()
    flow._abort_if_unique_id_configured.assert_called_once_with(
        updates={"host": "adapter"}
    )
    device.discover_units.side_effect = ClientConnectionError()
    flow.async_show_form = Mock(return_value={})
    await flow.async_step_user({"host": "adapter"})
    assert flow.async_show_form.call_args.kwargs["errors"] == {"base": "cannot_connect"}
    assert conn.close.await_count == 2


def make_entry(name):
    return SimpleNamespace(
        entry_id=name,
        data={"host": name},
        state=ConfigEntryState.SETUP_IN_PROGRESS,
        pref_disable_polling=False,
        async_on_unload=Mock(),
        title=name,
    )


async def setup_in_context(hass, entry):
    token = current_entry.set(entry)
    try:
        return await integration.async_setup_entry(hass, entry)
    finally:
        current_entry.reset(token)


async def test_concurrent_adapter_setup_and_unload(hass, api_factory, monkeypatch):
    apis = {name: api_factory() for name in ("one", "two")}
    entered = set()
    ready = asyncio.Event()

    async def setup_api(hass, host):
        return apis[host]

    async def forward(entry, platforms):
        entered.add(entry.entry_id)
        if len(entered) == 2:
            ready.set()
        await ready.wait()
        for platform in (number, select, switch, sensor, binary_sensor, water_heater):
            add = Mock()
            await platform.async_setup_entry(hass, entry, add)
            if add.called:
                for entity in add.call_args.args[0]:
                    assert entity._api is apis[entry.entry_id]
                    assert (
                        entity.coordinator
                        is hass.data[DOMAIN][entry.entry_id].coordinator
                    )

    hass.config_entries = SimpleNamespace(
        async_forward_entry_setups=forward,
        async_unload_platforms=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(integration, "setup_api_instance", setup_api)
    entries = [make_entry("one"), make_entry("two")]
    await asyncio.gather(*(setup_in_context(hass, entry) for entry in entries))
    first, second = (hass.data[DOMAIN][key].coordinator for key in ("one", "two"))
    assert isinstance(first, DataUpdateCoordinator)
    assert first is not second
    assert first.data == apis["one"].status
    await integration.async_unload_entry(hass, entries[0])
    assert "one" not in hass.data[DOMAIN]
    assert "two" in hass.data[DOMAIN]
    await integration.async_unload_entry(hass, entries[1])


async def test_setup_connection_failure_retries(hass, monkeypatch):
    monkeypatch.setattr(
        integration,
        "setup_api_instance",
        AsyncMock(side_effect=ClientConnectionError()),
    )
    with pytest.raises(ConfigEntryNotReady):
        await setup_in_context(hass, make_entry("one"))
    assert not hass.data.get(DOMAIN)


async def test_first_poll_failure_retries(hass, api_factory, monkeypatch):
    api = api_factory()
    api.device.get_current_state.side_effect = TypeError("bad response")
    monkeypatch.setattr(integration, "setup_api_instance", AsyncMock(return_value=api))
    with pytest.raises(ConfigEntryNotReady):
        await setup_in_context(hass, make_entry("one"))
    assert not hass.data.get(DOMAIN)


@pytest.mark.parametrize(
    "status",
    [
        None,
        {},
        {"function/SpaceHeating": None},
        {"function/SpaceHeating": {"states": None}},
    ],
)
async def test_invalid_snapshot_does_not_replace_previous(api_factory, status):
    api = api_factory()
    previous = deepcopy(api.status)
    api.space_heating_status["states"]["InstallerState"] = True
    previous = deepcopy(api.status)
    api.device.get_current_state.return_value = status
    with pytest.raises(UpdateFailed):
        await create_update_function(api)()
    assert api.status == previous
    assert not api.available
    api.device.ws_connection.close.assert_awaited_once()


async def test_setup_discovery_failure_closes_connection(hass, monkeypatch):
    conn = SimpleNamespace(close=AsyncMock())
    controller = SimpleNamespace(
        discover_units=AsyncMock(side_effect=ClientConnectionError())
    )
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _: None)
    monkeypatch.setattr(integration, "DaikinWSConnection", lambda *a: conn)
    monkeypatch.setattr(integration, "AlthermaController", lambda *a: controller)
    with pytest.raises(ConfigEntryNotReady):
        await setup_in_context(hass, make_entry("one"))
    conn.close.assert_awaited_once()


async def test_setup_timeout_closes_connection(hass, monkeypatch):
    conn = SimpleNamespace(close=AsyncMock())

    async def blocked():
        await asyncio.Event().wait()

    controller = SimpleNamespace(discover_units=AsyncMock(side_effect=blocked))
    monkeypatch.setattr(integration, "TIMEOUT", 0.01)
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _: None)
    monkeypatch.setattr(integration, "DaikinWSConnection", lambda *a: conn)
    monkeypatch.setattr(integration, "AlthermaController", lambda *a: controller)
    with pytest.raises(ConfigEntryNotReady):
        await setup_in_context(hass, make_entry("one"))
    conn.close.assert_awaited_once()


async def test_real_coordinator_tracks_failure_and_recovery(
    hass, api_factory, monkeypatch
):
    api = api_factory()
    monkeypatch.setattr(integration, "setup_api_instance", AsyncMock(return_value=api))
    hass.config_entries = SimpleNamespace(
        async_forward_entry_setups=AsyncMock(),
        async_unload_platforms=AsyncMock(return_value=True),
    )
    entry = make_entry("one")
    await setup_in_context(hass, entry)
    coordinator = hass.data[DOMAIN]["one"].coordinator
    heater = AlthermaWaterHeater(coordinator, api)
    assert heater.available
    api.device.get_current_state.side_effect = TypeError("malformed response")
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert not heater.available
    api.device.get_current_state.side_effect = None
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert heater.available
    # Failed unload must keep the live adapter and coordinator intact.
    hass.config_entries.async_unload_platforms.return_value = False
    assert not await integration.async_unload_entry(hass, entry)
    assert hass.data[DOMAIN]["one"].api is api
    hass.config_entries.async_unload_platforms.return_value = True
    await integration.async_unload_entry(hass, entry)


async def test_command_refresh_not_throttled(hass, api_factory, monkeypatch):
    api = api_factory()
    monkeypatch.setattr(integration, "setup_api_instance", AsyncMock(return_value=api))
    hass.config_entries = SimpleNamespace(
        async_forward_entry_setups=AsyncMock(),
        async_unload_platforms=AsyncMock(return_value=True),
    )
    entry = make_entry("one")
    await setup_in_context(hass, entry)
    coordinator = hass.data[DOMAIN]["one"].coordinator
    heater = AlthermaWaterHeater(coordinator, api)
    updated = deepcopy(api.status)
    updated["function/DomesticHotWaterTank"]["operations"][
        "DomesticHotWaterTemperatureHeating"
    ] = 50
    api.device.get_current_state.return_value = updated
    await heater.async_set_temperature(temperature=50)
    assert heater.native_target_temperature == 50
    assert api.device.get_current_state.await_count == 2
    await integration.async_unload_entry(hass, entry)
