from sqlalchemy import inspect

from database.engine_factory import create_db_engine
from upgrade.migrate_bybit_scalping_inventory import (
    TABLES,
    apply_migration,
    pending_changes,
)


def test_bybit_scalping_inventory_migration_is_idempotent(tmp_path):
    database_path = tmp_path / "scalping-ledger.db"
    target_engine = create_db_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        assert pending_changes(target_engine) == list(TABLES)
        assert apply_migration(target_engine)
        assert pending_changes(target_engine) == []

        columns = {
            column["name"]
            for column in inspect(target_engine).get_columns("scalping_bybit_spot_execution")
        }
        assert "order_link_id" in columns
        assert apply_migration(target_engine)
        assert pending_changes(target_engine) == []
    finally:
        target_engine.dispose()
