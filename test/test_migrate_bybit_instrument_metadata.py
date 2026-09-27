import importlib.util
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "upgrade" / "migrate_bybit_instrument_metadata.py"
sys.path.insert(0, str(MIGRATION_PATH.parent))
SPEC = importlib.util.spec_from_file_location("migrate_bybit_instrument_metadata", MIGRATION_PATH)
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


def test_migration_is_dry_runnable_idempotent_and_preserves_old_rows(tmp_path, capsys):
    database_path = tmp_path / "openalgo.db"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        poolclass=NullPool,
    )
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE symtoken ("
                    "id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, brsymbol TEXT NOT NULL, "
                    "exchange TEXT)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO symtoken (id, symbol, brsymbol, exchange) "
                    "VALUES (1, 'NIFTY', 'NIFTY', 'NFO')"
                )
            )

        assert MIGRATION.report_status(engine) is True
        assert "would be added" in capsys.readouterr().out
        old_columns = {column["name"] for column in inspect(engine).get_columns("symtoken")}
        assert not old_columns.intersection(MIGRATION.COLUMNS)

        assert MIGRATION.apply_migration(engine) is True
        assert MIGRATION.apply_migration(engine) is True

        columns = {column["name"] for column in inspect(engine).get_columns("symtoken")}
        assert set(MIGRATION.COLUMNS).issubset(columns)
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT symbol, brsymbol, exchange, category, qty_step, min_qty, max_qty "
                    "FROM symtoken WHERE id = 1"
                )
            ).one()
        assert tuple(row) == ("NIFTY", "NIFTY", "NFO", None, None, None, None)
    finally:
        engine.dispose()


def test_status_cli_does_not_create_or_modify_database(tmp_path, monkeypatch, capsys):
    database_path = tmp_path / "openalgo.db"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        poolclass=NullPool,
    )
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE symtoken ("
                    "id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, brsymbol TEXT NOT NULL)"
                )
            )
        before = {column["name"] for column in inspect(engine).get_columns("symtoken")}
    finally:
        engine.dispose()

    monkeypatch.setattr(MIGRATION, "get_database_url", lambda: f"sqlite:///{database_path}")
    monkeypatch.setattr("sys.argv", ["migrate_bybit_instrument_metadata.py", "--status"])

    assert MIGRATION.main() == 0
    after_engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        poolclass=NullPool,
    )
    try:
        after = {column["name"] for column in inspect(after_engine).get_columns("symtoken")}
    finally:
        after_engine.dispose()

    assert after == before
    assert "would be added" in capsys.readouterr().out
