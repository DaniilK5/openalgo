#!/usr/bin/env python3
"""Create the Bybit Scalping Spot inventory ledger tables.

Usage:
    uv run upgrade/migrate_bybit_scalping_inventory.py
    uv run upgrade/migrate_bybit_scalping_inventory.py --status
"""

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import inspect, text  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")
os.environ.setdefault("DATABASE_URL", "sqlite:///db/openalgo.db")

from database.scalping_db import Base, engine  # noqa: E402

TABLES = (
    "scalping_bybit_spot_order",
    "scalping_bybit_spot_inventory",
    "scalping_bybit_spot_execution",
)
EXTRA_COLUMNS = {
    "scalping_bybit_spot_inventory": {
        "fees_by_currency": "TEXT NOT NULL DEFAULT '{}'",
    },
    "scalping_bybit_spot_execution": {
        "order_link_id": "VARCHAR(36)",
    },
}


def pending_changes(target_engine) -> list[str]:
    inspector = inspect(target_engine)
    existing_tables = set(inspector.get_table_names())
    changes = [name for name in TABLES if name not in existing_tables]
    for table_name, columns in EXTRA_COLUMNS.items():
        if table_name not in existing_tables:
            continue
        existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
        changes.extend(
            f"{table_name}.{column_name}"
            for column_name in columns
            if column_name not in existing_columns
        )
    return changes


def report_status(target_engine) -> bool:
    changes = pending_changes(target_engine)
    if not changes:
        print("Bybit Scalping Spot inventory schema is up to date.")
        return True
    print("The following Bybit Scalping Spot inventory schema changes are pending:")
    for name in changes:
        print(f"  {name}")
    return False


def apply_migration(target_engine) -> bool:
    changes = pending_changes(target_engine)
    if not changes:
        print("Bybit Scalping Spot inventory schema is already present.")
        return True
    try:
        Base.metadata.create_all(
            target_engine,
            tables=[Base.metadata.tables[name] for name in TABLES],
            checkfirst=True,
        )
        inspector = inspect(target_engine)
        existing_columns = {
            table: {column["name"] for column in inspector.get_columns(table)}
            for table in EXTRA_COLUMNS
            if table in set(inspector.get_table_names())
        }
        with target_engine.begin() as connection:
            for table_name, columns in EXTRA_COLUMNS.items():
                for column_name, definition in columns.items():
                    if (
                        table_name in existing_columns
                        and column_name not in existing_columns[table_name]
                    ):
                        connection.execute(
                            text(
                                f"ALTER TABLE {table_name} "
                                f"ADD COLUMN {column_name} {definition}"
                            )
                        )
    except Exception as exc:
        print(f"Bybit Scalping Spot inventory migration failed: {exc}")
        return False
    print("Bybit Scalping Spot inventory schema is ready.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="Report pending changes only")
    args = parser.parse_args()
    database_url = str(engine.url)
    if args.status and database_url.startswith("sqlite:///"):
        path = Path(database_url.removeprefix("sqlite:///"))
        if not path.is_absolute() and not (PROJECT_ROOT / path).exists():
            print("Database file does not exist yet; no migration changes were made.")
            return 0
    success = report_status(engine) if args.status else apply_migration(engine)
    return 0 if success or not args.status else 1


if __name__ == "__main__":
    sys.exit(main())
