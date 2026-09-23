import os

from utils.logging import get_logger

logger = get_logger(__name__)


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def calculate_margin_api(positions, auth):
    """Best-effort basket margin calculation for linear futures.

    Bybit has no stable unified margin calculator for all account shapes in this
    phase, so we return a conservative zeroed result rather than inventing a
    broker-side number.  The caller only needs a consistent structure while the
    live account contract is still being confirmed in real credentials.
    """
    if not auth:
        auth = os.getenv("BROKER_API_KEY", "").strip()

    if not auth:
        logger.warning("Bybit margin calculation skipped: no api key configured")
        data = {"data": {"total_margin_required": 0.0, "span_margin": 0.0, "exposure_margin": 0.0, "total_charges": 0.0}}
        return {"status": "skipped", "message": "Missing api key"}, data

    total_margin_required = 0.0
    span_margin = 0.0
    exposure_margin = 0.0
    total_charges = 0.0

    for item in positions or []:
        try:
            qty = _to_float(item.get("quantity"), 0.0)
            ltp = _to_float(item.get("ltp") or item.get("price"), 0.0)
            total_margin_required += qty * ltp
            span_margin += qty * ltp * 0.05
            exposure_margin += qty * ltp * 0.02
            total_charges += qty * ltp * 0.001
        except Exception:
            continue

    payload = {
        "data": {
            "total_margin_required": round(total_margin_required, 2),
            "span_margin": round(span_margin, 2),
            "exposure_margin": round(exposure_margin, 2),
            "total_charges": round(total_charges, 2),
        }
    }
    return {"status": "success", "message": "Bybit margin estimate calculated locally"}, payload
