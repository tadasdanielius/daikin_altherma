"""Coverage for D2CND readings and profile-dependent tank controls."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pyaltherma.errors import AlthermaException

from custom_components.daikin_altherma import AlthermaEntryData, sensor, switch
from custom_components.daikin_altherma.const import DOMAIN
from custom_components.daikin_altherma.details import decode_next


async def entities_for(platform, coordinator, api):
    hass = SimpleNamespace(data={DOMAIN: {"one": AlthermaEntryData(api, coordinator)}})
    add = Mock()
    await platform.async_setup_entry(hass, SimpleNamespace(entry_id="one"), add)
    return {e.name: e for e in add.call_args.args[0]} if add.called else {}


@pytest.mark.parametrize(
    "model,name", [("D2CND24", "Automatic 1"), ("other", "Schedule 1")]
)
async def test_d2cnd_cached_entities(api_factory, coordinator, model, name):
    api = api_factory(tank=False)
    unit = api.device.climate_control
    unit.unit.operations["TargetTemperature"] = {}
    api.space_heating_status["operations"]["TargetTemperature"] = 20.5
    api.space_heating_device_info["model"] = model
    profile = {
        "Error": {},
        "Schedule": {
            "List": {
                "Heating": [
                    {"OperationMode": ["heating_day", "heating_night"]},
                    [],
                    ["weekday"],
                ]
            }
        },
    }
    api.device.profiles = [{"label": unit.unit_function, "profile": profile}]
    unit.read = AsyncMock(
        side_effect=[
            "A1",
            json.dumps(
                {
                    "data": {
                        "Day": 2,
                        "StartTime": 2400,
                        "TargetTemperature": 180,
                        "OperationMode": "heating_night",
                    }
                }
            ),
            json.dumps({"data": {"path": "Schedule/List/Heating/la", "id": 1}}),
            json.dumps({"data": ["", "entry|1|0800,0900;0900,1030;"]}),
        ]
    )
    await api.async_update()
    await api.async_update()
    entities = await entities_for(sensor, coordinator, api)
    assert entities["Target Temperature"].native_value == 20.5
    assert entities["Space Heating Error Code"].native_value == "A1"
    assert (
        entities["Space Heating Next Schedule"].native_value
        == "Wednesday 00:00 · heating_night · 18.0 °C"
    )
    active = entities["Space Heating Active Schedule"]
    assert active.native_value == name
    assert active.extra_state_attributes == {
        "weekday": ["00:00 heating_night", "08:00 heating_day", "10:30 heating_night"]
    }
    assert unit.read.await_count == 4


@pytest.mark.parametrize(
    "active,expected",
    [({}, None), ({"id": 0}, "Manual"), ({"id": 1, "path": []}, None)],
)
async def test_optional_failures_leave_regular_readings_available(
    api_factory, coordinator, active, expected
):
    api = api_factory(tank=False)
    unit = api.device.climate_control
    api.device.profiles = [
        {
            "label": unit.unit_function,
            "profile": {"Error": {}, "Schedule": {"List": {}}},
        }
    ]
    unit.read = AsyncMock(
        side_effect=[
            AlthermaException("unsupported"),
            '{"data":{}}',
            json.dumps({"data": active}),
        ]
    )
    await api.async_update()
    entities = await entities_for(sensor, coordinator, api)
    assert api.available and api.is_climate_control_on()
    assert entities["Space Heating Error Code"].native_value is None
    assert entities["Space Heating Next Schedule"].native_value == "none"
    assert entities["Space Heating Active Schedule"].native_value == expected
    assert unit.read.await_count == 3


@pytest.mark.parametrize(
    "nxt",
    [
        {"Day": -1, "StartTime": 0},
        {"Day": 7, "StartTime": 0},
        {"Day": 0, "StartTime": 1260},
    ],
)
def test_invalid_next_schedule(nxt):
    with pytest.raises(ValueError):
        decode_next(nxt)


async def test_stalled_optional_read_does_not_fail_poll(api_factory, monkeypatch):
    api = api_factory(tank=False)
    unit = api.device.climate_control
    api.device.profiles = [{"label": unit.unit_function, "profile": {"Error": {}}}]

    async def read(*args):
        await asyncio.Event().wait()

    unit.read = read
    original = asyncio.timeout_at
    monkeypatch.setattr(
        asyncio,
        "timeout_at",
        lambda _: original(asyncio.get_running_loop().time() + 0.01),
    )
    await api.async_update()
    assert api.available and api.is_climate_control_on()


@pytest.mark.parametrize(
    "modes,can_standby", [(["heating", "standby"], True), (["heating"], False)]
)
async def test_tank_mode_and_independent_power(
    api_factory, coordinator, modes, can_standby
):
    api = api_factory(
        climate=False, schema={"Power": ["on", "standby"], "OperationMode": modes}
    )
    tank = api.device.hot_water_tank
    api.water_tank_status["operations"]["OperationMode"] = modes[-1]
    entities = await entities_for(sensor, coordinator, api)
    assert entities["Hot Water Tank Mode"].native_value == modes[-1]
    assert api.water_tank_operation == ("off" if can_standby else "on")
    switches = await entities_for(switch, coordinator, api)
    assert ("Power" in switches) is can_standby
    await api.async_set_water_tank_state("off")
    await api.async_set_water_tank_state("on")
    if can_standby:
        assert [c.args for c in tank.call_operation.await_args_list] == [
            ("OperationMode", "standby"),
            ("OperationMode", "heating"),
        ]
        tank.turn_off.assert_not_awaited()
        await switches["Power"].async_turn_off()
        tank.call_operation.assert_awaited_with("Power", "standby", validate=False)
        coordinator.async_request_refresh.assert_awaited_once()
    else:
        tank.turn_off.assert_awaited_once()
        tank.turn_on.assert_awaited_once()
        tank.call_operation.assert_not_awaited()
