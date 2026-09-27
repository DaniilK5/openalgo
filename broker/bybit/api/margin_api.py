from utils.logging import get_logger

logger = get_logger(__name__)


def calculate_margin_api(positions, auth):
    """Bybit has no verified basket margin calculator in this integration.

    Unified Account margin depends on cross/isolated mode, tiered maintenance
    margin rates per category and portfolio-margin risk offsets that are not
    confirmed against a live Bybit response. A locally fabricated number would
    look like a genuine broker margin figure to a trader making a sizing
    decision, which is worse than refusing outright.

    Raising NotImplementedError lets ``services/margin_service.py`` report a
    clear, capability-based 501 instead of masking this as a zero or
    success-shaped result.
    """
    logger.info("Bybit margin calculation is not supported: no verified broker-side calculator")
    raise NotImplementedError(
        "Bybit does not support margin calculation in this integration"
    )
