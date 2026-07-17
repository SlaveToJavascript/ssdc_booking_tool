import json
import re
from datetime import datetime, timedelta
from pathlib import Path

AVAILABILITY_FILE = Path(__file__).with_name("availabilities.json")

SESSION_TIMES = {
    "S1": "8:00 AM - 9:40 AM",
    "S2": "9:50 AM - 11:30 AM",
    "S3": "12:15 PM - 1:55 PM",
    "S4": "2:05 PM - 3:45 PM",
    "S5": "3:55 PM - 5:35 PM",
    "S6": "6:20 PM - 8:00 PM",
    "S7": "8:10 PM - 9:50 PM",
}

COMMAND_HELP = (
    "Commands:\n"
    "/add 20/7 S1\n"
    "/add 14/7 S1-S5 16/7-18/7 all\n"
    "/remove 20/7 S1\n"
    "/list_availabilities"
)


def _load_availabilities():
    if not AVAILABILITY_FILE.exists():
        return {}

    try:
        with AVAILABILITY_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_availabilities(data):
    AVAILABILITY_FILE.write_text(
        json.dumps(data, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _parse_date_to_datetime(date_text):
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?", date_text.strip())
    if not match:
        raise ValueError("Use date format DD/MM, for example 20/7.")

    day = int(match.group(1))
    month = int(match.group(2))
    year_text = match.group(3)
    current_year = datetime.now().year

    if year_text:
        year = int(year_text)
        if year < 100:
            year += 2000
    else:
        year = current_year

    try:
        parsed = datetime(year, month, day)
    except ValueError:
        raise ValueError("That date does not look valid.")

    if not year_text and parsed.date() < datetime.now().date():
        parsed = datetime(year + 1, month, day)

    return parsed


def _parse_date(date_text):
    return _parse_date_to_datetime(date_text).strftime("%d/%m/%Y")


def _parse_date_range(date_text):
    if "-" not in date_text:
        return [_parse_date(date_text)]

    start_text, end_text = [part.strip() for part in date_text.split("-", 1)]
    start_date = _parse_date_to_datetime(start_text)
    end_date = _parse_date_to_datetime(end_text)

    if end_date < start_date:
        raise ValueError("Date ranges must go from earlier to later dates.")

    dates = []
    current = start_date
    while current <= end_date:
        dates.append(current.strftime("%d/%m/%Y"))
        current += timedelta(days=1)
    return dates


def _parse_session(session_text):
    session = session_text.strip().upper()
    if session not in SESSION_TIMES:
        raise ValueError("Use a valid session from S1 to S7.")
    return session


def _parse_session_range(session_text):
    normalized = session_text.strip().upper()
    if normalized == "ALL":
        return list(SESSION_TIMES)

    if "-" not in normalized:
        return [_parse_session(normalized)]

    start_text, end_text = [part.strip() for part in normalized.split("-", 1)]
    start_session = _parse_session(start_text)
    end_session = _parse_session(end_text)
    start_number = int(start_session[1:])
    end_number = int(end_session[1:])

    if end_number < start_number:
        raise ValueError("Session ranges must go from lower to higher sessions, for example S1-S5.")

    return [f"S{number}" for number in range(start_number, end_number + 1)]


def _parse_command_groups(command_text):
    parts = command_text.strip().split()
    if len(parts) < 3 or len(parts[1:]) % 2 != 0:
        raise ValueError(COMMAND_HELP)

    groups = []
    for index in range(1, len(parts), 2):
        dates = _parse_date_range(parts[index])
        sessions = _parse_session_range(parts[index + 1])
        groups.append((dates, sessions))
    return groups


def _sort_sessions(sessions):
    return sorted(sessions, key=lambda item: int(item[1:]))


def _format_change_summary(action, changed_count, requested_count):
    if changed_count == 0:
        return f"ℹ️ No availabilities {action}."

    skipped_count = requested_count - changed_count
    suffix = ""
    if action == "added" and skipped_count:
        suffix = f" ({skipped_count} already existed)"
    elif action == "removed" and skipped_count:
        suffix = f" ({skipped_count} not found)"

    noun = "availability" if changed_count == 1 else "availabilities"
    return f"✅ {changed_count} {noun} {action}{suffix}."


def _format_date_short(date_key, show_year=False):
    parsed = datetime.strptime(date_key, "%d/%m/%Y")
    if show_year:
        return f"{parsed.day}/{parsed.month}/{parsed.year}"
    return f"{parsed.day}/{parsed.month}"


def _format_date_range_short(start_date_key, end_date_key, show_year=False):
    if start_date_key == end_date_key:
        return _format_date_short(start_date_key, show_year)
    return f"{_format_date_short(start_date_key, show_year)} – {_format_date_short(end_date_key, show_year)}"


def _format_sessions_compact(sessions):
    sorted_sessions = _sort_sessions(sessions)
    if sorted_sessions == list(SESSION_TIMES):
        return "all slots"

    ranges = []
    start = previous = int(sorted_sessions[0][1:])

    for session in sorted_sessions[1:]:
        current = int(session[1:])
        if current == previous + 1:
            previous = current
            continue

        ranges.append((start, previous))
        start = previous = current

    ranges.append((start, previous))

    return ", ".join(
        f"S{start}" if start == end else f"S{start} – S{end}"
        for start, end in ranges
    )


def _format_availabilities(data):
    if not data:
        return "No availabilities saved.\n\n" + COMMAND_HELP

    sorted_dates = sorted(data, key=lambda value: datetime.strptime(value, "%d/%m/%Y"))
    years = {datetime.strptime(date_key, "%d/%m/%Y").year for date_key in sorted_dates}
    show_year = len(years) > 1
    grouped_lines = []
    group_start = sorted_dates[0]
    group_end = sorted_dates[0]
    group_sessions = tuple(_sort_sessions(data[group_start]))
    previous_date = datetime.strptime(group_start, "%d/%m/%Y")

    for date_key in sorted_dates[1:]:
        current_date = datetime.strptime(date_key, "%d/%m/%Y")
        current_sessions = tuple(_sort_sessions(data[date_key]))

        if current_sessions == group_sessions and current_date == previous_date + timedelta(days=1):
            group_end = date_key
        else:
            grouped_lines.append(
                f"{_format_date_range_short(group_start, group_end, show_year)}: {_format_sessions_compact(group_sessions)}"
            )
            group_start = group_end = date_key
            group_sessions = current_sessions

        previous_date = current_date

    grouped_lines.append(
        f"{_format_date_range_short(group_start, group_end, show_year)}: {_format_sessions_compact(group_sessions)}"
    )

    return "\n".join(["Saved availabilities:", *grouped_lines])


def add_availability_from_command(command_text):
    try:
        groups = _parse_command_groups(command_text)
    except ValueError as error:
        return f"⚠️ {error}"

    data = _load_availabilities()
    requested_count = 0
    added_count = 0

    for dates, sessions_to_add in groups:
        for date_key in dates:
            sessions = set(data.get(date_key, []))
            for session in sessions_to_add:
                requested_count += 1
                if session not in sessions:
                    sessions.add(session)
                    added_count += 1
            data[date_key] = _sort_sessions(sessions)

    _save_availabilities(data)
    return f"{_format_change_summary('added', added_count, requested_count)}\n\n{_format_availabilities(data)}"


def remove_availability_from_command(command_text):
    try:
        groups = _parse_command_groups(command_text)
    except ValueError as error:
        return f"⚠️ {error}"

    data = _load_availabilities()
    requested_count = 0
    removed_count = 0

    for dates, sessions_to_remove in groups:
        for date_key in dates:
            sessions = set(data.get(date_key, []))
            for session in sessions_to_remove:
                requested_count += 1
                if session in sessions:
                    sessions.remove(session)
                    removed_count += 1

            if sessions:
                data[date_key] = _sort_sessions(sessions)
            else:
                data.pop(date_key, None)

    _save_availabilities(data)
    return f"{_format_change_summary('removed', removed_count, requested_count)}\n\n{_format_availabilities(data)}"


def list_availabilities():
    data = _load_availabilities()
    return _format_availabilities(data)
