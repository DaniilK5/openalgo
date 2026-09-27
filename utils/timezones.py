"""Application and schedule timezone defaults.

Exchange calendars and broker contracts keep their own timezones. These
constants apply only to generic application displays and new user schedules.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Almaty").strip() or "Asia/Almaty"
ZoneInfo(APP_TIMEZONE)
LEGACY_SCHEDULE_TIMEZONE = "Asia/Kolkata"

_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_REFERENCE_MONDAY = date(2026, 1, 5)


def timezone_for(name: str | None, fallback: str = APP_TIMEZONE) -> ZoneInfo:
    """Return a named timezone, falling back to the application's configured zone."""
    try:
        return ZoneInfo(name or fallback)
    except (KeyError, ValueError):
        return ZoneInfo(fallback)


def to_app_datetime(value: datetime, *, naive_timezone: str = "UTC") -> datetime:
    """Convert a timestamp to the application timezone as an aware datetime."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone_for(naive_timezone))
    return value.astimezone(timezone_for(APP_TIMEZONE))


def format_app_datetime(
    value: datetime | str,
    *,
    naive_timezone: str = "UTC",
    format_string: str = "%d-%m-%Y %H:%M:%S",
) -> str:
    """Format a timestamp for display in the application timezone.

    Naive timestamps are interpreted in the source timezone specified by the
    caller; existing database log timestamps use UTC.
    """
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    return to_app_datetime(value, naive_timezone=naive_timezone).strftime(format_string)


def convert_weekly_schedule(
    time_text: str | None,
    days: list[str] | tuple[str, ...] | None,
    from_timezone: str,
    to_timezone: str = APP_TIMEZONE,
) -> tuple[str | None, list[str]]:
    """Convert a recurring wall-clock time and weekday set between timezones.

    A stable Monday is used as a calendar anchor; the result includes a weekday
    shift when conversion crosses midnight.
    """
    if not time_text:
        return time_text, list(days or [])

    try:
        hour_text, minute_text = time_text.split(":", maxsplit=1)
        hour, minute = int(hour_text), int(minute_text)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (ValueError, TypeError):
        return time_text, list(days or [])

    source_zone = timezone_for(from_timezone)
    target_zone = timezone_for(to_timezone)
    converted: set[str] = set()
    source_days = {
        str(day).lower() for day in (days or ()) if str(day).lower() in _WEEKDAYS
    }
    if not source_days:
        return time_text, list(days or [])

    converted_times: set[str] = set()
    for source_day in source_days:
        day_index = _WEEKDAYS.index(source_day)
        source_date = date.fromordinal(_REFERENCE_MONDAY.toordinal() + day_index)
        source_datetime = datetime(
            source_date.year, source_date.month, source_date.day, hour, minute, tzinfo=source_zone
        )
        target_datetime = source_datetime.astimezone(target_zone)
        target_day = _WEEKDAYS[target_datetime.weekday()]
        converted.add(target_day)
        converted_times.add(target_datetime.strftime("%H:%M"))

    # Timezones used by application schedules have stable offsets. If a future
    # timezone rule makes this vary by weekday, preserve the original clock and
    # let the caller handle that schedule explicitly rather than choose one.
    converted_time = (
        next(iter(converted_times)) if len(converted_times) == 1 else time_text
    )
    return converted_time, sorted(converted, key=_WEEKDAYS.index)


def clock_time_in_timezone(
    time_text: str | None,
    from_timezone: str,
    to_timezone: str = APP_TIMEZONE,
) -> str | None:
    """Convert a wall-clock value on a stable reference date between zones."""
    converted, _ = convert_weekly_schedule(
        time_text, ["mon"], from_timezone=from_timezone, to_timezone=to_timezone
    )
    return converted
