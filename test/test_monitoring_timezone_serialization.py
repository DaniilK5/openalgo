from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify

from blueprints import health, latency
from limiter import limiter

TIMESTAMP = datetime(2026, 9, 26, 0, 0, tzinfo=UTC)
EXPECTED_TIMESTAMP = "2026-09-26T05:00:00+05:00"


@pytest.fixture
def monitoring_client(monkeypatch):
    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setenv("DISABLE_SESSION_EXPIRY", "true")

    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="monitoring-tests")
    app.register_blueprint(latency.latency_bp)
    app.register_blueprint(health.health_bp)
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["logged_in"] = True
        flask_session["login_time"] = datetime.now(UTC).isoformat()
    return client


def _latency_log():
    return SimpleNamespace(
        id=1,
        order_id="order-1",
        broker="bybit",
        symbol="BTCUSDT",
        order_type="LIMIT",
        rtt_ms=12.0,
        validation_latency_ms=1.0,
        response_latency_ms=10.0,
        overhead_ms=1.0,
        total_latency_ms=12.0,
        status="success",
        error=None,
        timestamp=TIMESTAMP,
    )


def test_latency_logs_api_serializes_timestamp_in_application_timezone(
    monitoring_client, monkeypatch
):
    monkeypatch.setattr(
        latency.OrderLatency, "get_recent_logs", staticmethod(lambda limit: [_latency_log()])
    )

    response = monitoring_client.get("/latency/api/logs")

    assert response.status_code == 200
    assert response.json[0]["timestamp"] == EXPECTED_TIMESTAMP


def test_latency_dashboard_serializes_timestamp_in_application_timezone(
    monitoring_client, monkeypatch
):
    class EmptyQuery:
        def with_entities(self, *_args):
            return self

        def distinct(self):
            return self

        def all(self):
            return []

    monkeypatch.setattr(latency.OrderLatency, "query", EmptyQuery())
    monkeypatch.setattr(latency.OrderLatency, "get_latency_stats", staticmethod(lambda: {}))
    monkeypatch.setattr(
        latency.OrderLatency, "get_recent_logs", staticmethod(lambda limit: [_latency_log()])
    )
    monkeypatch.setattr(
        latency,
        "render_template",
        lambda _template, **context: jsonify(context["logs_json"]),
    )

    response = monitoring_client.get("/latency/")

    assert response.status_code == 200
    assert response.json[0]["timestamp"] == EXPECTED_TIMESTAMP


@pytest.mark.parametrize(
    ("path", "monkeypatch_target", "method_name", "result", "expected_key"),
    [
        (
            "/health/api/current",
            health.HealthMetric,
            "get_current_metrics",
            SimpleNamespace(
                timestamp=TIMESTAMP,
                fd_count=1,
                fd_limit=100,
                fd_usage_percent=1.0,
                fd_status="healthy",
                memory_rss_mb=1.0,
                memory_vms_mb=1.0,
                memory_percent=1.0,
                memory_available_mb=1.0,
                memory_swap_mb=0.0,
                memory_status="healthy",
                db_connections_total=1,
                db_connections=[],
                db_status="healthy",
                ws_connections_total=0,
                ws_connections=[],
                ws_total_symbols=0,
                ws_status="healthy",
                thread_count=1,
                stuck_threads=0,
                thread_status="healthy",
                thread_details=[],
                process_details=[],
                overall_status="healthy",
            ),
            "timestamp",
        ),
        (
            "/health/api/history",
            health.HealthMetric,
            "get_metrics_history",
            [
                SimpleNamespace(
                    timestamp=TIMESTAMP,
                    fd_count=1,
                    memory_rss_mb=1.0,
                    db_connections_total=1,
                    ws_connections_total=0,
                    thread_count=1,
                    overall_status="healthy",
                )
            ],
            None,
        ),
        (
            "/health/api/alerts",
            health.HealthAlert,
            "get_active_alerts",
            [
                SimpleNamespace(
                    id=1,
                    timestamp=TIMESTAMP,
                    alert_type="threshold",
                    severity="warning",
                    metric_name="memory",
                    metric_value=90.0,
                    threshold_value=80.0,
                    message="High memory usage",
                    acknowledged=False,
                    resolved=False,
                )
            ],
            None,
        ),
    ],
)
def test_health_apis_serialize_timestamps_in_application_timezone(
    monitoring_client, monkeypatch, path, monkeypatch_target, method_name, result, expected_key
):
    monkeypatch.setattr(
        monkeypatch_target, method_name, staticmethod(lambda *args, **kwargs: result)
    )

    response = monitoring_client.get(path)

    assert response.status_code == 200
    timestamps = (
        [response.json[expected_key]]
        if expected_key
        else [row["timestamp"] for row in response.json]
    )
    assert timestamps == [EXPECTED_TIMESTAMP]
