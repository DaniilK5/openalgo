from types import SimpleNamespace

import pytest

from broker.bybit.api import auth_api, baseurl, rest_client
from broker.bybit.streaming.bybit_websocket import BybitWebSocket


def test_request_dispatches_to_typed_pybit_method(monkeypatch):
    calls = []

    class FakeClient:
        def get_order_history(self, **kwargs):
            calls.append(("get_order_history", kwargs))
            return {"retCode": 0, "result": {"list": []}}

    monkeypatch.setattr(rest_client, "get_client", lambda api_key=None: FakeClient())

    result = rest_client.request(
        "/v5/order/history",
        params={"category": "linear", "limit": 50},
        api_key="test-key",
    )

    assert result == {"retCode": 0, "result": {"list": []}}
    assert calls == [
        ("get_order_history", {"category": "linear", "limit": 50}),
    ]


def test_pybit_requests_use_eventlet_thread_pool_when_eventlet_is_loaded(monkeypatch):
    calls = []

    class FakeClient:
        def get_server_time(self):
            calls.append("pybit request")
            return {"retCode": 0, "time": "123"}

    class FakeThreadPool:
        @staticmethod
        def execute(function, **kwargs):
            calls.append("thread pool")
            return function(**kwargs)

    monkeypatch.setitem(
        rest_client.sys.modules,
        "eventlet",
        SimpleNamespace(tpool=FakeThreadPool()),
    )
    monkeypatch.setattr(rest_client, "get_client", lambda api_key=None: FakeClient())

    result = rest_client.request("/v5/market/time")

    assert result == {"retCode": 0, "time": "123"}
    assert calls == ["thread pool", "pybit request"]


def test_pybit_session_is_reused_reconfigured_and_closed(monkeypatch):
    created = []
    closed = []

    class FakeHTTP:
        def __init__(self, **kwargs):
            self.config = kwargs
            self.endpoint = None
            self.client = SimpleNamespace(close=lambda: closed.append(self))
            created.append(self)

    monkeypatch.setattr(rest_client, "HTTP", FakeHTTP)
    monkeypatch.setenv("BROKER_API_KEY", "test-key")
    monkeypatch.setenv("BROKER_API_SECRET", "test-secret")
    monkeypatch.setenv("BYBIT_TESTNET", "false")
    monkeypatch.setenv("BYBIT_BASE_URL", "https://api.bybit.kz")
    rest_client.close_client()

    mainnet_client = rest_client.get_client()
    assert rest_client.get_client() is mainnet_client
    assert mainnet_client.endpoint == "https://api.bybit.kz"
    assert mainnet_client.config["recv_window"] == 10000
    assert mainnet_client.config["max_retries"] == 3
    assert mainnet_client.config["log_requests"] is False

    monkeypatch.setenv("BYBIT_TESTNET", "true")
    testnet_client = rest_client.get_client()

    assert testnet_client is not mainnet_client
    assert testnet_client.endpoint == "https://api-testnet.bybit.com"
    assert closed == [mainnet_client]
    rest_client.close_client()
    assert closed == [mainnet_client, testnet_client]


def test_pybit_api_errors_are_logged_and_normalized(monkeypatch):
    warnings = []

    class FakeClient:
        def get_wallet_balance(self, **kwargs):
            raise rest_client.InvalidRequestError(
                request="GET /v5/account/wallet-balance",
                message="Error sign: origin_string[123TOP_SECRET_KEY5000accountType=UNIFIED]",
                status_code=10004,
                time="12:00:00",
                resp_headers={},
            )

    monkeypatch.setattr(rest_client, "get_client", lambda api_key=None: FakeClient())
    monkeypatch.setattr(
        rest_client.logger,
        "warning",
        lambda message, *args: warnings.append(message % args),
    )

    with pytest.raises(ValueError, match="rejected the account request"):
        rest_client.request(
            "/v5/account/wallet-balance",
            params={"accountType": "UNIFIED"},
            api_key="test-key",
        )

    assert warnings == [
        "Bybit request rejected for /v5/account/wallet-balance "
        "(retCode=10004 retMsg=signature validation failed (signed details redacted))"
    ]
    assert "TOP_SECRET_KEY" not in " ".join(warnings)


def test_testnet_switch_routes_rest_and_websocket_to_testnet(monkeypatch):
    monkeypatch.setenv("BYBIT_TESTNET", "true")
    monkeypatch.setenv("BYBIT_BASE_URL", "https://api.bybit.com")

    assert baseurl.get_url("/v5/market/time") == "https://api-testnet.bybit.com/v5/market/time"
    assert baseurl.get_public_ws_url("linear") == "wss://stream-testnet.bybit.com/v5/public/linear"
    assert baseurl.get_private_ws_url() == "wss://stream-testnet.bybit.com/v5/private"
    assert BybitWebSocket().url == baseurl.get_public_ws_url("linear")
    assert BybitWebSocket(authenticate=True).url == baseurl.get_private_ws_url()


def test_mainnet_base_url_override_and_boolean_validation(monkeypatch):
    monkeypatch.setenv("BYBIT_TESTNET", "false")
    monkeypatch.setenv("BYBIT_BASE_URL", "https://api.bybit.kz/")
    assert baseurl.get_url("/v5/market/time") == "https://api.bybit.kz/v5/market/time"
    assert baseurl.get_public_ws_url("linear") == "wss://stream.bybit.com/v5/public/linear"

    monkeypatch.setenv("BYBIT_TESTNET", "maybe")
    with pytest.raises(ValueError, match="BYBIT_TESTNET"):
        baseurl.get_url("/v5/market/time")


def test_request_logging_is_enabled_by_default(monkeypatch):
    monkeypatch.delenv("BYBIT_LOG_REQUESTS", raising=False)

    assert rest_client._request_logging_enabled() is True


def test_authenticate_broker_accepts_successful_wallet_response(monkeypatch):
    monkeypatch.setenv("BROKER_API_KEY", "test-api-key")
    monkeypatch.setenv("BROKER_API_SECRET", "test-api-secret")
    monkeypatch.setenv("BYBIT_LOG_REQUESTS", "false")
    monkeypatch.setattr(auth_api, "get_server_time_ms", lambda **kwargs: 1_700_000_000_000)
    monkeypatch.setattr(
        auth_api,
        "request",
        lambda endpoint, **kwargs: {"retCode": 0, "retMsg": "OK"},
    )

    token, error = auth_api.authenticate_broker("bybit")

    assert token == "test-api-key"
    assert error is None


def test_authenticate_broker_surfaces_empty_http_error(monkeypatch):
    monkeypatch.setenv("BROKER_API_KEY", "test-api-key")
    monkeypatch.setenv("BROKER_API_SECRET", "test-api-secret")
    monkeypatch.setenv("BYBIT_LOG_REQUESTS", "false")
    monkeypatch.setattr(auth_api, "get_server_time_ms", lambda **kwargs: 1_700_000_000_000)
    monkeypatch.setattr(
        auth_api,
        "request",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("Bybit could not provide the requested data.")
        ),
    )

    token, error = auth_api.authenticate_broker("bybit")

    assert token is None
    assert error == "Bybit could not provide the requested data."


def test_auth_request_logs_exclude_credentials(monkeypatch):
    messages = []
    monkeypatch.setenv("BROKER_API_KEY", "test-api-key")
    monkeypatch.setenv("BROKER_API_SECRET", "test-api-secret")
    monkeypatch.setenv("BYBIT_LOG_REQUESTS", "true")
    monkeypatch.setattr(auth_api, "get_server_time_ms", lambda **kwargs: 1_700_000_000_000)
    monkeypatch.setattr(
        auth_api,
        "request",
        lambda endpoint, **kwargs: {"retCode": 0, "retMsg": "OK"},
    )
    monkeypatch.setattr(
        auth_api.logger, "info", lambda message, *args: messages.append(message % args)
    )
    monkeypatch.setattr(
        rest_client.logger, "info", lambda message, *args: messages.append(message % args)
    )

    token, error = auth_api.authenticate_broker("bybit")
    logs = "\n".join(messages)

    assert token == "test-api-key"
    assert error is None
    assert "test-api-key" not in logs
    assert "test-api-secret" not in logs
    assert "X-BAPI-SIGN" not in logs
