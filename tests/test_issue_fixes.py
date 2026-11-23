"""Regression coverage for reported integration issues."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfTemperature
from pyaltherma.controllers import AlthermaUnitController
from pyaltherma.profile import AlthermaUnit

from custom_components.daikin_altherma import AlthermaEntryData, sensor
from custom_components.daikin_altherma.binary_sensor import AlthermaUnitProblemSensor
from custom_components.daikin_altherma.connection import DaikinWSConnection
from custom_components.daikin_altherma.const import DOMAIN
from custom_components.daikin_altherma.water_heater import AlthermaWaterHeater


async def test_ha_publishes_water_heater_temperatures(hass, api_factory, coordinator):
    api = api_factory()
    heater = AlthermaWaterHeater(coordinator, api)
    heater.hass = hass
    assert heater.state_attributes["current_temperature"] == 43
    assert heater.state_attributes["temperature"] == 45

    api.water_tank_status["sensors"]["TankTemperature"] = None
    api.water_tank_status["operations"]["DomesticHotWaterTemperatureHeating"] = 50
    assert heater.state_attributes["current_temperature"] is None
    assert heater.state_attributes["temperature"] == 50


@pytest.mark.parametrize(
    "function,key,nested",
    [
        ("function/DomesticHotWaterTank", "DomesticHotWaterTemperatureHeating", False),
        ("function/DomesticHotWater", "TargetTemperature", True),
        ("function/DomesticHotWater", "TargetTemperature", False),
    ],
)
async def test_tank_sensors_follow_cached_water_heater(
    api_factory, coordinator, function, key, nested
):
    config = {"minValue": 30, "maxValue": 60}
    api = api_factory(
        function=function, schema={key: {"heating": config} if nested else config}
    )
    hass = SimpleNamespace(data={DOMAIN: {"one": AlthermaEntryData(api, coordinator)}})
    add = Mock()
    await sensor.async_setup_entry(hass, SimpleNamespace(entry_id="one"), add)
    current, target = add.call_args.args[0]
    heater = AlthermaWaterHeater(coordinator, api)
    assert current.name == "Water Tank Current Temperature"
    assert target.name == "Water Tank Target Temperature"
    assert current.unique_id == "test-serial-HotWaterTank-current-temperature"
    assert target.unique_id == "test-serial-HotWaterTank-target-temperature"
    assert current.device_info == target.device_info == api.HWT_device_info
    assert current.device_class == SensorDeviceClass.TEMPERATURE
    assert current.native_unit_of_measurement == UnitOfTemperature.CELSIUS
    assert current.state_class == SensorStateClass.MEASUREMENT
    assert current.native_value == heater.native_current_temperature == 43
    assert target.native_value == heater.native_target_temperature == 45
    api.water_tank_status["sensors"]["TankTemperature"] = 44
    api.water_tank_status["operations"][key] = 48
    assert current.native_value == heater.native_current_temperature == 44
    assert target.native_value == heater.native_target_temperature == 48
    api.device.get_current_state.assert_not_awaited()
    api.device.ws_connection.close.assert_not_awaited()
    coordinator.last_update_success = False
    assert not current.available and not target.available


@pytest.mark.parametrize(
    "tank,current,target",
    [
        (False, False, False),
        (True, False, False),
        (True, True, False),
        (True, False, True),
    ],
)
async def test_only_advertised_tank_sensors_are_created(
    api_factory, coordinator, tank, current, target
):
    schema = {"TargetTemperature": {"minValue": 30, "maxValue": 60}} if target else {}
    if current:
        schema["SensorTemperature"] = {"stepValue": 0.5}
    api = api_factory(tank=tank, climate=False, schema=schema)
    if tank:
        api.device.hot_water_tank.sensors = []
        api.water_tank_status["sensors"] = {}
        api.water_tank_status["operations"]["SensorTemperature"] = 42
    hass = SimpleNamespace(data={DOMAIN: {"one": AlthermaEntryData(api, coordinator)}})
    add = Mock()
    await sensor.async_setup_entry(hass, SimpleNamespace(entry_id="one"), add)
    entities = add.call_args.args[0]
    assert len(entities) == int(current) + int(target)
    if current:
        assert entities[0].native_value == 42


@pytest.mark.parametrize(
    "value", [None, "$NULL", "invalid", float("nan"), float("inf"), True]
)
def test_invalid_tank_readings_are_unknown(api_factory, coordinator, value):
    api = api_factory()
    api.water_tank_status["sensors"]["TankTemperature"] = value
    api.water_tank_status["operations"]["DomesticHotWaterTemperatureHeating"] = value
    for kind in ("current", "target"):
        assert (
            sensor.WaterTankTemperatureSensor(coordinator, api, kind).native_value
            is None
        )
    heater = AlthermaWaterHeater(coordinator, api)
    assert (
        heater.native_current_temperature is None
        and heater.native_target_temperature is None
    )


def test_unrelated_tank_measurement_not_reported_as_temperature(
    api_factory, coordinator
):
    api = api_factory()
    api.water_tank_status["sensors"] = {"Pressure": 1.5}
    assert (
        sensor.WaterTankTemperatureSensor(coordinator, api, "current").native_value
        is None
    )
    assert AlthermaWaterHeater(coordinator, api).native_current_temperature is None


@pytest.mark.parametrize("name", [None, "", "   ", "$NULL", " $null ", "NULL", "None"])
@pytest.mark.parametrize(
    "function,label",
    [
        ("function/SpaceHeating", "Space Heating"),
        ("function/DomesticHotWaterTank", "Hot Water Tank"),
        ("function/DomesticHotWater", "Hot Water Tank"),
    ],
)
def test_consumption_placeholder_names_keep_identity(
    api_factory, coordinator, name, function, label
):
    entity = sensor.ConsumptionSensor(
        coordinator, api_factory(), {}, function, name, "Heating", "D", "2 Hours"
    )
    assert entity.name == f"{label} 2 Hours Heating Energy"
    assert entity.unique_id == f"test-serial/{function}/Electrical/Heating/D"


def test_user_provided_unit_name_is_preserved(api_factory, coordinator):
    entity = sensor.ConsumptionSensor(
        coordinator,
        api_factory(),
        {},
        "function/SpaceHeating",
        "My heating zone",
        "Heating",
        "D",
        "2 Hours",
    )
    assert entity.name == "My heating zone 2 Hours Heating Energy"


@pytest.mark.parametrize(
    "states,expected",
    [
        (
            {
                "ErrorState": False,
                "WarningState": False,
                "InstallerState": True,
                "TargetTemperatureOverruledState": True,
                "WeatherDependentState": True,
            },
            False,
        ),
        ({"ErrorState": "0", "WarningState": "false"}, False),
        ({"ErrorState": "1"}, True),
        ({"WarningState": True}, True),
        ({"EmergencyState": 1}, True),
        ({"InstallerState": True}, None),
        ({}, None),
        ({"ErrorState": None}, None),
        ({"ErrorState": None, "WarningState": True}, True),
        ({"ErrorState": False, "WarningState": None}, None),
    ],
)
def test_problem_sensor_only_reports_fault_flags(
    api_factory, coordinator, states, expected
):
    api = api_factory()
    api.space_heating_status["states"] = states
    entity = AlthermaUnitProblemSensor(
        coordinator,
        api,
        "Space Heating Unit State",
        api.space_heating_device_info,
        "SpaceHeating",
    )
    assert entity.is_on is expected
    assert entity.extra_state_attributes == states
    assert entity.unique_id == "test-serial-SpaceHeating-problem_sensor"


@pytest.mark.parametrize(
    "value,expected",
    [
        (False, False),
        (True, True),
        (0, False),
        (1, True),
        ("0", False),
        ("false", False),
        (" FALSE ", False),
        ("1", True),
        ("true", True),
    ],
)
async def test_false_strings_decoded_before_pyaltherma_boolean_conversion(
    value, expected
):
    conn = DaikinWSConnection(Mock(), "adapter")
    conn._request = AsyncMock(
        return_value={"m2m:rsp": {"pc": {"m2m:cin": {"con": value}}}}
    )
    unit = AlthermaUnit(1, {"UnitStatus": ["ErrorState"]})
    controller = AlthermaUnitController(unit, conn)
    assert await controller.read_states() == {"ErrorState": expected}
    conn._request.assert_awaited_once()


@pytest.mark.parametrize("value", [None, "$NULL", "bad", 2])
async def test_invalid_flags_do_not_masquerade_as_healthy(value):
    conn = DaikinWSConnection(Mock(), "adapter")
    conn._request = AsyncMock(
        return_value={"m2m:rsp": {"pc": {"m2m:cin": {"con": value}}}}
    )
    controller = AlthermaUnitController(
        AlthermaUnit(1, {"UnitStatus": ["ErrorState"]}), conn
    )
    with pytest.raises(ValueError, match="Invalid Daikin status flag"):
        await controller.read_states()


@pytest.mark.parametrize(
    "path,value",
    [
        ("/Operation/Power/la", "standby"),
        ("/UnitStatus/ControlModeState/la", "room"),
        ("/UnitStatus/ErrorCode/la", "U4"),
    ],
)
async def test_transport_preserves_nonboolean_responses(path, value):
    response = {"m2m:rsp": {"pc": {"m2m:cin": {"con": value}}}}
    conn = DaikinWSConnection(Mock(), "adapter")
    conn._request = AsyncMock(return_value=response)
    assert await conn.request("/[0]/MNAE/1" + path) == response
    assert response["m2m:rsp"]["pc"]["m2m:cin"]["con"] == value
