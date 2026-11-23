"""Fixtures use real HA classes and mock only the adapter/network boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from homeassistant.core import HomeAssistant

from custom_components.daikin_altherma import AlthermaAPI


@pytest_asyncio.fixture
async def hass(tmp_path):
    instance = HomeAssistant(str(tmp_path))
    yield instance
    await instance.async_stop()


@pytest.fixture
def api_factory():
    def make(
        *,
        schema=None,
        function="function/DomesticHotWaterTank",
        climate=True,
        tank=True,
    ):
        schema = (
            schema
            if schema is not None
            else {
                "DomesticHotWaterTemperatureHeating": {
                    "minValue": 30,
                    "maxValue": 60,
                    "stepValue": 1,
                    "settable": True,
                },
                "Power": ["on", "standby"],
                "powerful": ["0", "1"],
            }
        )
        hot_water = (
            SimpleNamespace(
                unit=SimpleNamespace(
                    operation_config=schema,
                    operations=schema,
                    consumptions_available=False,
                ),
                operations=list(schema),
                sensors=["TankTemperature"],
                unit_function=function,
                call_operation=AsyncMock(),
                turn_on=AsyncMock(),
                turn_off=AsyncMock(),
                set_powerful=AsyncMock(),
            )
            if tank
            else None
        )
        heating_schema = {
            "Power": ["on", "standby"],
            "OperationMode": ["heating", "cooling"],
            "LeavingWaterTemperatureHeating": {
                "minValue": 20,
                "maxValue": 55,
                "stepValue": 1,
                "settable": True,
            },
        }
        heating = (
            SimpleNamespace(
                unit=SimpleNamespace(
                    operation_config=heating_schema,
                    operations=heating_schema,
                    consumptions_available=False,
                ),
                operations=list(heating_schema),
                unit_function="function/SpaceHeating",
                sensors=[],
                call_operation=AsyncMock(),
                turn_on=AsyncMock(),
                turn_off=AsyncMock(),
            )
            if climate
            else None
        )
        status = {}
        units = {}
        if tank:
            key = next((k for k in schema if "Temperature" in k), None)
            status[function] = {
                "operations": {"Power": "on", "powerful": 0, key: 45},
                "states": {},
                "sensors": {"TankTemperature": 43},
            }
            units[function] = hot_water
        if climate:
            status["function/SpaceHeating"] = {
                "operations": {
                    "Power": "on",
                    "OperationMode": "heating",
                    "LeavingWaterTemperatureHeating": 35,
                },
                "states": {},
                "sensors": {},
            }
            units["function/SpaceHeating"] = heating
        device = SimpleNamespace(
            ws_connection=SimpleNamespace(host="adapter", close=AsyncMock()),
            hot_water_tank=hot_water,
            climate_control=heating,
            altherma_units=units,
            get_current_state=AsyncMock(return_value=status),
            refresh=AsyncMock(),
        )
        api = AlthermaAPI(device)
        api._status = status
        api._info = {"serial_number": "test-serial"}
        api._available = True
        api._hwt_device_info = (
            {"identifiers": {("daikin_altherma", "test-serial Hot Water Tank")}}
            if tank
            else None
        )
        api._space_heating_device_info = (
            {"identifiers": {("daikin_altherma", "test-serial Space Heating")}}
            if climate
            else None
        )
        return api

    return make


@pytest.fixture
def coordinator():
    return SimpleNamespace(last_update_success=True, async_request_refresh=AsyncMock())
