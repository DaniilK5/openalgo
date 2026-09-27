from datetime import datetime
from zoneinfo import ZoneInfo

import duckdb

from upgrade.migrate_historify_schedule_timezone import migrate_connection


def test_old_daily_schedule_keeps_its_execution_instant_after_timezone_migration():
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("""
            CREATE TABLE historify_schedules (
                id VARCHAR PRIMARY KEY,
                schedule_type VARCHAR NOT NULL,
                time_of_day VARCHAR
            )
        """)
        connection.execute(
            "INSERT INTO historify_schedules VALUES ('legacy', 'daily', '09:15')"
        )
        expected = datetime(2026, 9, 28, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata")).timestamp()

        assert migrate_connection(connection)
        assert migrate_connection(connection)

        schedule = connection.execute(
            "SELECT time_of_day, timezone FROM historify_schedules WHERE id = 'legacy'"
        ).fetchone()
        actual = datetime(
            2026, 9, 28, *map(int, schedule[0].split(":")), tzinfo=ZoneInfo(schedule[1])
        ).timestamp()

        assert schedule == ("09:15", "Asia/Kolkata")
        assert actual == expected
    finally:
        connection.close()
