"""Execution events shared by live brokers and ledger consumers."""

from dataclasses import dataclass
from decimal import Decimal

from utils.event_bus import Event


@dataclass
class ExecutionUpdateEvent(Event):
    """One broker execution, preserving decimal quantities, prices, and fees."""

    topic: str = "execution.update"
    exec_id: str = ""
    order_id: str = ""
    user_id: str = ""
    symbol: str = ""
    exchange: str = ""
    side: str = ""
    quantity: Decimal = Decimal("0")
    price: Decimal = Decimal("0")
    fee_amount: Decimal = Decimal("0")
    fee_currency: str = ""
    broker: str = ""
    order_link_id: str = ""
