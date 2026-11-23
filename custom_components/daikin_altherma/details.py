"""Read optional unit diagnostics from the discovered adapter profiles."""

import json
import logging

from aiohttp import ClientError
from pyaltherma.errors import AlthermaException

from .schedule import MANUAL_SCHEDULE_ID, WEEKDAYS, WEEKEND, decode_active, valid_time

_LOGGER = logging.getLogger(__name__)
UNIT_NAMES = {
    "function/SpaceHeating": "Space Heating",
    "function/DomesticHotWater": "Hot Water Tank",
    "function/DomesticHotWaterTank": "Hot Water Tank",
}


def detail_profiles(device):
    for item in device.profiles:
        function = item["label"]
        profile = item["profile"]
        if function in UNIT_NAMES and isinstance(profile, dict):
            yield function, profile


def detail_keys(profile):
    if "Error" in profile or "ErrorState" in (profile.get("UnitStatus") or []):
        yield "error_code"
    schedule = profile.get("Schedule")
    if isinstance(schedule, dict):
        if "Next" in schedule or "List" in schedule:
            yield "next_schedule"
        if isinstance(schedule.get("List"), dict):
            yield "active_schedule"


def decode_next(data):
    if data == {}:
        return "none", {}
    day, start = data["Day"], data["StartTime"]
    if type(day) is not int or not 0 <= day < 7 or not valid_time(start):
        raise ValueError("Invalid schedule day or time")
    weekday = (WEEKDAYS + WEEKEND)[day].capitalize()
    time = f"{start // 100 % 24:02d}:{start % 100:02d}"
    mode = data.get("OperationMode")
    parts = [f"{weekday} {time}"]
    if mode:
        parts.append(mode)
    temperature = data.get("TargetTemperature")
    if temperature is not None:
        temperature /= 10
        parts.append(f"{temperature} °C")
    return " · ".join(parts), {
        "day": weekday,
        "start_time": time,
        "operation_mode": mode,
        "target_temperature": temperature,
    }


async def read_detail(controller, key, profile, model):
    if key == "error_code":
        error = await controller.read("Error")
        if isinstance(error, str):
            return error or "none", {}
        return None
    if key == "next_schedule":
        return decode_next(
            json.loads(await controller.read("Schedule", "Next"))["data"]
        )
    active = json.loads(await controller.read("Schedule", "Active"))["data"]
    schedule_id = active["id"]
    if type(schedule_id) is not int or schedule_id < 0:
        raise ValueError("Invalid active schedule ID")
    if schedule_id == MANUAL_SCHEDULE_ID:
        return "Manual", {}
    path = active["path"]
    if not isinstance(path, str):
        raise ValueError("Invalid active schedule path")
    kind = path.rstrip("/").split("/")[-2]
    if kind not in profile["Schedule"]["List"]:
        raise ValueError("Unknown schedule kind")
    entries = json.loads(await controller.read("Schedule", f"List/{kind}"))["data"]
    return decode_active(
        controller.unit_function,
        profile["Schedule"]["List"][kind],
        schedule_id,
        entries,
        model,
    )


async def read_unit_details(api, status, details):
    for function, profile in detail_profiles(api.device):
        if function not in status:
            continue
        controller = api.device.altherma_units[function]
        info = (
            api.space_heating_device_info
            if function == "function/SpaceHeating"
            else api.HWT_device_info
        )
        values = details[function] = {}
        for key in detail_keys(profile):
            try:
                values[key] = await read_detail(
                    controller, key, profile, (info or {}).get("model")
                )
            except (ClientError, OSError) as err:
                # A delayed response must not be consumed by the next request.
                _LOGGER.debug("Optional Daikin read interrupted: %s", err)
                return
            except (
                AlthermaException,
                ValueError,
                TypeError,
                IndexError,
                KeyError,
            ) as err:
                _LOGGER.debug("Cannot read %s for %s: %s", key, function, err)
