"""Add category and quantity-rule fields to the shared symtoken table.

The new fields are nullable so existing broker rows retain their current meaning.
Bybit instrument masters populate them for category-aware routing and validation.

Usage:
    uv run migrate_bybit_instrument_metadata.py
    uv run migrate_bybit_instrument_metadata.py --status
"""

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import _pragmas  # noqa: E402,F401
from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

TABLE = "symtoken"
COLUMNS = {
    "category": "VARCHAR(16)",
    "qty_step": "REAL",
    "min_qty": "REAL",
    "max_qty": "REAL",
    "base_precision": "REAL",
    "quote_precision": "REAL",
    "min_order_amt": "REAL",
    "max_market_qty": "REAL",
    "max_limit_qty": "REAL",
    "base_coin": "VARCHAR(20)",
    "quote_coin": "VARCHAR(20)",
    "settle_coin": "VARCHAR(20)",
}


def get_database_url():
    return os.getenv("DATABASE_URL", "sqlite:///db/openalgo.db")


def resolve_sqlite_path(db_url):
    prefix = "sqlite:///"
    if not db_url.startswith(prefix):
        return db_url
    path = db_url[len(prefix) :]
    if not path or path == ":memory:" or os.path.isabs(path):
        return db_url
    if len(path) >= 3 and path[1] == ":" and path[2] in {"/", "\\"}:
        return db_url
    return prefix + str(PROJECT_ROOT / path)


def missing_columns(engine):
    if TABLE not in set(inspect(engine).get_table_names()):
        return list(COLUMNS)
    existing = {column["name"] for column in inspect(engine).get_columns(TABLE)}
    return [name for name in COLUMNS if name not in existing]


def report_status(engine):
    missing = missing_columns(engine)
    if TABLE not in set(inspect(engine).get_table_names()):
        print("symtoken table does not exist; no changes are needed yet.")
        return True
    if not missing:
        print("All Bybit instrument metadata columns are present.")
        return True
    print("The following nullable symtoken columns would be added:")
    for name in missing:
        print(f"  {name}: {COLUMNS[name]}")
    return True


def apply_migration(engine):
    if TABLE not in set(inspect(engine).get_table_names()):
        print("symtoken table does not exist; no changes are needed yet.")
        return True

    missing = missing_columns(engine)
    if not missing:
        print("All Bybit instrument metadata columns are already present.")
        return True

    try:
        with engine.begin() as connection:
            for name in missing:
                connection.execute(
                    text(f"ALTER TABLE {TABLE} ADD COLUMN {name} {COLUMNS[name]}")
                )
    except Exception as exc:
        print(f"Migration failed while adding symtoken metadata: {exc}")
        return False

    for name in missing:
        print(f"Added nullable symtoken column: {name}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Add Bybit instrument metadata columns")
    parser.add_argument(
        "--status", action="store_true", help="Report changes without applying them"
    )
    args = parser.parse_args()
    db_url = resolve_sqlite_path(get_database_url())
    if args.status and db_url.startswith("sqlite:///"):
        path = Path(db_url[len("sqlite:///") :])
        if not path.exists() and path != Path(":memory:"):
            print("Database file does not exist yet. Not created: --status changes nothing.")
            return 0

    engine = create_engine(db_url, poolclass=NullPool)
    try:
        success = report_status(engine) if args.status else apply_migration(engine)
        return 0 if success else 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
