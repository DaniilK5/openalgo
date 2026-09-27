from types import SimpleNamespace

from utils import auth_utils


def test_failed_master_download_is_not_reported_as_success(monkeypatch):
    statuses = []
    module = SimpleNamespace(master_contract_download=lambda: False)
    monkeypatch.setattr(auth_utils.importlib, "import_module", lambda _path: module)
    monkeypatch.setattr(
        auth_utils,
        "update_status",
        lambda broker, status, message: statuses.append((broker, status, message)),
    )

    result = auth_utils.async_master_contract_download("bybit")

    assert result["status"] == "error"
    assert statuses[-1][0:2] == ("bybit", "error")
    assert "instrument list" in statuses[-1][2]
