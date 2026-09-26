import hashlib
import hmac

import pytest

from broker.bybit.api import auth_api, baseurl
from broker.bybit.streaming.bybit_websocket import BybitWebSocket


def test_auth_headers_use_bybit_v5_hmac_contract(monkeypatch):
    monkeypatch.setattr(baseurl.time, "time", lambda: 1658384314.791)
    params = {"symbol": "BTCUSDT", "category": "linear"}
    key = "test-api-key"
    secret = "test-api-secret"
    recv_window = 5000

    headers = baseurl.get_auth_headers(
        "GET",
        "/v5/market/tickers",
        params=params,
        api_key=key,
        api_secret=secret,
        recv_window=recv_window,
    )
    signed = "1658384314791test-api-key5000category=linear&symbol=BTCUSDT"
    expected = hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()

    assert headers["X-BAPI-API-KEY"] == key
    assert headers["X-BAPI-SIGN"] == expected
    assert headers["X-BAPI-TIMESTAMP"] == "1658384314791"
    assert headers["X-BAPI-RECV-WINDOW"] == str(recv_window)
    assert "X-BAPI-APIKEY" not in headers
    assert secret not in headers.values()


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

    assert auth_api._request_logging_enabled() is True


def test_authenticate_broker_accepts_successful_wallet_response(monkeypatch):
    class Response:
        status_code = 200
        content = b'{"retCode":0,"retMsg":"OK"}'
        text = content.decode()

        @staticmethod
        def json():
            return {"retCode": 0, "retMsg": "OK"}

    class Client:
        @staticmethod
        def get(*args, **kwargs):
            return Response()

    monkeypatch.setenv("BROKER_API_KEY", "test-api-key")
    monkeypatch.setenv("BROKER_API_SECRET", "test-api-secret")
    monkeypatch.setenv("BYBIT_LOG_REQUESTS", "false")
    monkeypatch.setattr(auth_api, "get_httpx_client", lambda: Client())
    monkeypatch.setattr(auth_api, "get_server_time_ms", lambda client: 1_700_000_000_000)

    token, error = auth_api.authenticate_broker("bybit")

    assert token == "test-api-key"
    assert error is None


def test_authenticate_broker_surfaces_empty_http_error(monkeypatch):
    class Response:
        status_code = 401
        content = b""
        text = ""
        headers = {"content-type": "application/json"}
        http_version = "HTTP/2"

    class Client:
        @staticmethod
        def get(*args, **kwargs):
            return Response()

    monkeypatch.setenv("BROKER_API_KEY", "test-api-key")
    monkeypatch.setenv("BROKER_API_SECRET", "test-api-secret")
    monkeypatch.setenv("BYBIT_LOG_REQUESTS", "false")
    monkeypatch.setattr(auth_api, "get_httpx_client", lambda: Client())
    monkeypatch.setattr(auth_api, "get_server_time_ms", lambda client: 1_700_000_000_000)

    token, error = auth_api.authenticate_broker("bybit")

    assert token is None
    assert error == "HTTP 401 from Bybit (application/json): empty response body"


def test_auth_request_logs_exclude_credentials(monkeypatch):
    class Response:
        status_code = 200
        content = b'{"retCode":0,"retMsg":"OK"}'
        text = content.decode()
        headers = {"content-type": "application/json"}
        http_version = "HTTP/2"

        @staticmethod
        def json():
            return {"retCode": 0, "retMsg": "OK"}

    class Client:
        @staticmethod
        def get(*args, **kwargs):
            return Response()

    messages = []
    monkeypatch.setenv("BROKER_API_KEY", "test-api-key")
    monkeypatch.setenv("BROKER_API_SECRET", "test-api-secret")
    monkeypatch.setenv("BYBIT_LOG_REQUESTS", "true")
    monkeypatch.setattr(auth_api, "get_httpx_client", lambda: Client())
    monkeypatch.setattr(auth_api, "get_server_time_ms", lambda client: 1_700_000_000_000)
    monkeypatch.setattr(
        auth_api.logger, "info", lambda message, *args: messages.append(message % args)
    )

    token, error = auth_api.authenticate_broker("bybit")
    logs = "\n".join(messages)

    assert token == "test-api-key"
    assert error is None
    assert "test-api-key" not in logs
    assert "test-api-secret" not in logs
    assert "X-BAPI-SIGN" not in logs
