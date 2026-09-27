import sys
from types import SimpleNamespace
from unittest.mock import Mock

from flask import Flask
from sqlalchemy import event, text
from sqlalchemy.orm import scoped_session, sessionmaker

from database.engine_factory import create_db_engine
from utils import db_sessions


def test_bybit_master_session_is_registered_for_request_cleanup(monkeypatch):
    module_path = "broker.bybit.database.master_contract_db"
    session_entry = (module_path, "db_session")
    assert session_entry in db_sessions.SCOPED_SESSION_MODULES

    scoped_session = Mock()
    monkeypatch.setitem(sys.modules, module_path, SimpleNamespace(db_session=scoped_session))
    monkeypatch.setattr(db_sessions, "SCOPED_SESSION_MODULES", [session_entry])

    db_sessions.remove_all_scoped_sessions()

    scoped_session.remove.assert_called_once_with()


def test_request_teardown_releases_bybit_master_db_connections(tmp_path, monkeypatch):
    module_path = "broker.bybit.database.master_contract_db"
    session_entry = (module_path, "db_session")
    db_path = tmp_path / "bybit-search-cleanup.db"
    engine = create_db_engine(f"sqlite:///{db_path.as_posix()}")
    request_session = scoped_session(sessionmaker(bind=engine))
    monkeypatch.setitem(
        sys.modules,
        module_path,
        SimpleNamespace(db_session=request_session),
    )
    monkeypatch.setattr(db_sessions, "SCOPED_SESSION_MODULES", [session_entry])
    connections = {"checkout": 0, "checkin": 0}
    event.listen(
        engine,
        "checkout",
        lambda *_args: connections.__setitem__("checkout", connections["checkout"] + 1),
    )
    event.listen(
        engine,
        "checkin",
        lambda *_args: connections.__setitem__("checkin", connections["checkin"] + 1),
    )

    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="session-cleanup-tests")
    app.teardown_appcontext(lambda _exception: db_sessions.remove_all_scoped_sessions())

    @app.get("/search")
    def search():
        request_session.execute(text("SELECT 1")).scalar_one()
        return "ok"

    try:
        client = app.test_client()
        for _ in range(100):
            assert client.get("/search").status_code == 200

        assert connections == {"checkout": 100, "checkin": 100}
    finally:
        request_session.remove()
        engine.dispose()
