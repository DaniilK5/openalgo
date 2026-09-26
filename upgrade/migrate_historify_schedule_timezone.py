#!/usr/bin/env python3
"""Add explicit timezones to Historify schedules without moving existing runs."""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.timezones import LEGACY_SCHEDULE_TIMEZONE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.getenv("HISTORIFY_DATABASE_PATH", "db/historify.duckdb")
if not os.path.isabs(DB_PATH):
    DB_PATH = os.path.join(ROOT, DB_PATH)


def table_exists(connection) -> bool:
    result = connection.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'historify_schedules'"
    ).fetchone()
    return bool(result and result[0])


def migrate_connection(connection) -> bool:
    """Add the timezone field and mark prior clock values with their old zone."""
    if not table_exists(connection):
        return False

    columns = {
        row[1] for row in connection.execute("PRAGMA table_info('historify_schedules')").fetchall()
    }
    if "timezone" not in columns:
        connection.execute(
            "ALTER TABLE historify_schedules "
            "ADD COLUMN timezone VARCHAR DEFAULT 'Asia/Kolkata'"
        )
    connection.execute(
        "UPDATE historify_schedules SET timezone = ? "
        "WHERE timezone IS NULL OR TRIM(timezone) = ''",
        [LEGACY_SCHEDULE_TIMEZONE],
    )
    return True


def status() -> bool:
    if not os.path.exists(DB_PATH):
        print("Historify database is not present; no schedule migration is needed.")
        return True

    import duckdb

    connection = duckdb.connect(DB_PATH, read_only=True)
    try:
        if not table_exists(connection):
            print("Historify schedule table is not present; no schedule migration is needed.")
            return True
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info('historify_schedules')").fetchall()
        }
        if "timezone" not in columns:
            print("Historify schedules need a timezone column; existing schedules will remain in IST.")
            return False
        missing = connection.execute(
            "SELECT COUNT(*) FROM historify_schedules "
            "WHERE timezone IS NULL OR TRIM(timezone) = ''"
        ).fetchone()[0]
        print(f"Historify schedule timezone column is present; {missing} rows need backfilling.")
        return missing == 0
    finally:
        connection.close()


def upgrade() -> bool:
    if not os.path.exists(DB_PATH):
        print("Historify database is not present; no schedule migration is needed.")
        return True

    import duckdb

    connection = duckdb.connect(DB_PATH)
    try:
        if not migrate_connection(connection):
            print("Historify schedule table is not present; no schedule migration is needed.")
            return True
        print("Historify schedules now retain their existing Asia/Kolkata execution timezone.")
        return True
    except Exception as exc:
        print(f"Historify schedule timezone migration failed: {exc}")
        return False
    finally:
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    sys.exit(0 if (status() if args.status else upgrade()) else 1)
