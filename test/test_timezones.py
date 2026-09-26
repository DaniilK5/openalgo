import importlib
import json
from datetime import UTC, datetime

from utils import timezones
from utils.timezones import APP_TIMEZONE, convert_weekly_schedule, format_app_datetime


def test_format_app_datetime_uses_almaty_for_utc_timestamps():
    timestamp = datetime(2026, 9, 26, 0, 0, tzinfo=UTC)

    assert format_app_datetime(timestamp) == "26-09-2026 05:00:00"


def test_format_app_datetime_interprets_naive_values_as_utc():
    timestamp = datetime(2026, 9, 26, 0, 0)

    assert format_app_datetime(timestamp) == "26-09-2026 05:00:00"


def test_weekly_schedule_conversion_keeps_weekday_when_clock_crosses_midnight():
    time_text, days = convert_weekly_schedule("00:15", ["mon"], "Asia/Kolkata")

    assert (time_text, days) == ("23:45", ["sun"])
    assert APP_TIMEZONE == "Asia/Almaty"


def test_application_timezone_can_be_overridden_by_environment(monkeypatch):
    monkeypatch.setenv("APP_TIMEZONE", "UTC")
    try:
        assert importlib.reload(timezones).APP_TIMEZONE == "UTC"
    finally:
        monkeypatch.setenv("APP_TIMEZONE", "Asia/Almaty")
        importlib.reload(timezones)


def test_frontend_runtime_config_uses_application_timezone():
    from blueprints.react_app import react_app_timezone
    from utils.timezones import APP_TIMEZONE

    response = react_app_timezone()

    assert response.get_data(as_text=True) == (
        f"window.OPENALGO_APP_TIMEZONE = {json.dumps(APP_TIMEZONE)};"
    )
