"""Decoding of the adapter's Schedule/List entries.

An entry looks like "$NDJ|1|0000,0015;0730,2400;,;0000,0015;0900,2400;,;":
name | flag | one "start,end;" HHMM slot per period, a fixed number of slots per day group
(groups come from the unit profile: e.g. [weekday, weekend] or [monday .. sunday]).
Inside a period the unit runs the schedule's first operation mode, outside it the second
(heating_day / heating_night) or standby when the profile lists only one (DHW: heating).
"""

WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']
WEEKEND = ['saturday', 'sunday']

# Schedule names are not in the data (NameAdjustable: false); positions as shown by the app on D2CND
SCHEDULE_NAMES = {
    'function/SpaceHeating': {1: 'Automatic 1', 2: 'Automatic 2'},
    'function/DomesticHotWaterTank': {1: 'Automatic 1', 2: 'Automatic 2', 3: 'Automatic 1/2 -1h', 4: '24h'},
}


def schedule_name(unit_function, schedule_id):
    return SCHEDULE_NAMES.get(unit_function, {}).get(schedule_id, f'Schedule {schedule_id}')


def _hhmm(value):
    return f'{value // 100:02d}:{value % 100:02d}'


def _switch_points(periods, in_mode, out_mode):
    """Periods [(start, end)] in HHMM -> ["00:00 out_mode", "08:00 in_mode", ...] like the app."""
    points = []
    for start, end in sorted(periods):
        if start == end:
            continue  # empty period, e.g. "0000,0000"
        if points and points[-1] == (start, out_mode):
            points.pop()  # touching periods: no switch in between
        elif not points and start != 0:
            points.append((0, out_mode))
        points.append((start, in_mode))
        if end < 2400:
            points.append((end, out_mode))
    if not points:
        points.append((0, out_mode))
    return [f'{_hhmm(time)} {mode}' for time, mode in points]


def decode_entry(entry, groups, modes):
    """Return {group: [switch points]} for one Schedule/List entry string."""
    if not groups:
        return {}
    body = entry.split('|', 2)[2]
    slots = [slot for slot in body.split(';') if slot != '']
    per_group = len(slots) // len(groups)
    in_mode = modes[0]
    out_mode = modes[1] if len(modes) > 1 else 'standby'

    days = {}
    for index, group in enumerate(groups):
        periods = []
        for slot in slots[index * per_group:(index + 1) * per_group]:
            start, end = slot.split(',')
            if start and end:
                periods.append((int(start), int(end)))
        days[group] = _switch_points(periods, in_mode, out_mode)

    # The app groups identical days of a per-day schedule into weekday/weekend
    if all(day in days for day in WEEKDAYS + WEEKEND):
        weekday = {tuple(days[day]) for day in WEEKDAYS}
        weekend = {tuple(days[day]) for day in WEEKEND}
        if len(weekday) == 1 and len(weekend) == 1:
            return {'weekday': list(weekday.pop()), 'weekend': list(weekend.pop())}
    return days


def decode_active(unit_function, profile, active, entries):
    """Active schedule -> (name, {group: [switch points]}) using the unit profile."""
    schedule_id = active['id']
    kind = active['path'].rstrip('/').split('/')[-2]  # .../schedule/List/Heating/la
    layout = profile['Schedule']['List'][kind]
    modes = layout[0]['OperationMode']
    groups = layout[schedule_id + 1]  # layout[0] is the slot spec, then one group list per entry
    return schedule_name(unit_function, schedule_id), decode_entry(entries[schedule_id], groups, modes)
