import sys
from types import SimpleNamespace
from unittest.mock import Mock

import blueprints.scalping as scalping_blueprint
import utils.session as session_utils
from blueprints.scalping import scalping_bp
from utils import latency_monitor


def _client(monkeypatch, broker="bybit"):
    from flask import Flask

    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="test")
    app.register_blueprint(scalping_bp)
    monkeypatch.setattr(session_utils, "is_session_valid", lambda: True)
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["broker"] = broker
        flask_session["logged_in"] = True
        flask_session["user"] = "test-user"
    return client


def _spot_instrument():
    return SimpleNamespace(
        category="spot",
        base_coin="BTC",
        quote_coin="USDT",
        quote_precision=0.01,
        min_order_amt=5,
        qty_step=0.00001,
        min_qty=0.00001,
        max_qty=100,
        max_market_qty=100,
    )


def test_bybit_instrument_search_requires_bybit_session(monkeypatch):
    client = _client(monkeypatch, broker="zerodha")

    response = client.get("/scalping/api/bybit/instruments?category=spot&query=BTC")

    assert response.status_code == 400
    assert "Bybit" in response.get_json()["message"]


def test_bybit_instrument_search_validates_category(monkeypatch):
    client = _client(monkeypatch)

    response = client.get("/scalping/api/bybit/instruments?category=NFO&query=NIFTY")

    assert response.status_code == 400
    assert response.get_json()["status"] == "error"


def test_bybit_instrument_search_filters_master_by_category(monkeypatch):
    from broker.bybit.database import master_contract_db

    row = SimpleNamespace(
        symbol="BTCUSDT",
        exchange="CRYPTO",
        category="spot",
        name="BTC",
        expiry="",
        strike=0,
        instrumenttype="SPOT",
        tick_size=0.1,
        qty_step=0.000001,
        min_qty=0.00001,
        min_order_amt=5,
        base_coin="BTC",
        quote_coin="USDT",
        settle_coin=None,
    )
    query_result = Mock()
    query_result.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
        row
    ]
    session = Mock()
    session.query.return_value = query_result
    monkeypatch.setattr(master_contract_db, "db_session", session)
    client = _client(monkeypatch)

    response = client.get("/scalping/api/bybit/instruments?category=spot&query=BTC")

    assert response.status_code == 200
    assert response.get_json()["data"] == [
        {
            "symbol": "BTCUSDT",
            "exchange": "CRYPTO",
            "category": "spot",
            "name": "BTC",
            "expiry": "",
            "strike": 0,
            "instrumenttype": "SPOT",
            "tick_size": 0.1,
            "qty_step": 0.000001,
            "min_qty": 0.00001,
            "min_order_amt": 5,
            "base_coin": "BTC",
            "quote_coin": "USDT",
            "settle_coin": None,
        }
    ]
    session.query.assert_called_once_with(master_contract_db.SymToken)


def test_bybit_spot_order_rejects_market_unit_mismatch(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(scalping_blueprint, "_bybit_instrument", lambda symbol: _spot_instrument())

    response = client.post(
        "/scalping/api/order",
        json={
            "symbol": "BTCUSDT",
            "exchange": "CRYPTO",
            "category": "spot",
            "action": "BUY",
            "quantity": 25,
            "product": "CNC",
            "market_unit": "baseCoin",
        },
    )

    assert response.status_code == 400
    assert "quote-currency budget" in response.get_json()["message"]


def test_bybit_spot_order_forwards_quote_budget_to_bybit_order_service(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(scalping_blueprint, "_bybit_instrument", lambda symbol: _spot_instrument())
    monkeypatch.setattr(
        scalping_blueprint,
        "_resolve_session_auth",
        lambda: (None, None, "openalgo-key", None, None),
    )
    monkeypatch.setattr(scalping_blueprint, "_current_mode", lambda: "live")
    monkeypatch.setattr(latency_monitor._latency_log_executor, "submit", lambda *args, **kwargs: None)
    order_call = {}
    reservations = []
    monkeypatch.setattr(
        "services.bybit_scalping_inventory_service.reserve_bybit_scalping_spot_order",
        lambda **kwargs: (
            reservations.append(kwargs)
            or {"order_link_id": "sc-test-link", "status": "reserved"}
        ),
    )

    def place_order(**kwargs):
        order_call.update(kwargs)
        return True, {"status": "success", "orderid": "test-order"}, 200

    monkeypatch.setitem(
        sys.modules,
        "services.place_order_service",
        SimpleNamespace(place_order=place_order),
    )
    monkeypatch.setattr("database.scalping_db.track_symbol", lambda *args, **kwargs: None)

    response = client.post(
        "/scalping/api/order",
        json={
            "symbol": "BTCUSDT",
            "exchange": "CRYPTO",
            "category": "spot",
            "action": "BUY",
            "quantity": 25,
            "product": "CNC",
            "market_unit": "quoteCoin",
        },
    )

    assert response.status_code == 200
    assert order_call["order_data"]["quantity"] == 25.0
    assert order_call["order_data"]["category"] == "spot"
    assert order_call["order_data"]["market_unit"] == "quoteCoin"
    assert order_call["order_data"]["order_link_id"] == "sc-test-link"
    assert reservations[0]["user_id"] == "test-user"


def test_bybit_live_spot_sell_is_refused_when_inventory_is_unavailable(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(scalping_blueprint, "_bybit_instrument", lambda symbol: _spot_instrument())
    monkeypatch.setattr(scalping_blueprint, "_current_mode", lambda: "live")
    monkeypatch.setattr(
        scalping_blueprint,
        "_resolve_session_auth",
        lambda: (None, None, "openalgo-key", None, None),
    )
    monkeypatch.setattr(
        "services.bybit_scalping_inventory_service.reserve_bybit_scalping_spot_order",
        Mock(side_effect=ValueError("Sell quantity exceeds scalping-owned Spot inventory")),
    )
    monkeypatch.setattr(
        latency_monitor._latency_log_executor, "submit", lambda *args, **kwargs: None
    )
    place_order = Mock()
    monkeypatch.setitem(
        sys.modules,
        "services.place_order_service",
        SimpleNamespace(place_order=place_order),
    )

    response = client.post(
        "/scalping/api/order",
        json={
            "symbol": "BTCUSDT",
            "exchange": "CRYPTO",
            "category": "spot",
            "action": "SELL",
            "quantity": 0.01,
            "product": "CNC",
            "market_unit": "baseCoin",
        },
    )

    assert response.status_code == 400
    assert "scalping-owned" in response.get_json()["message"]
    place_order.assert_not_called()


def test_bybit_inventory_endpoint_returns_only_current_mode_ledger(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(
        scalping_blueprint,
        "_bybit_instrument",
        lambda symbol: _spot_instrument(),
    )
    monkeypatch.setattr(scalping_blueprint, "_current_mode", lambda: "live")
    inventory = {
        "symbol": "BTCUSDT",
        "exchange": "CRYPTO",
        "mode": "live",
        "base_coin": "BTC",
        "owned_quantity": "0.25",
        "reserved_quantity": "0.05",
        "available_quantity": "0.2",
    }
    monkeypatch.setattr(
        "services.bybit_scalping_inventory_service.get_bybit_scalping_spot_inventory",
        lambda **kwargs: inventory,
    )

    response = client.get("/scalping/api/bybit/inventory?symbol=BTCUSDT")

    assert response.status_code == 200
    assert response.get_json()["data"] == inventory
