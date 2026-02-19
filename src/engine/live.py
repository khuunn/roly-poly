"""LiveEngine — real CLOB API order execution (future implementation)."""

from __future__ import annotations

from src.engine.base import ExecutionEngine
from src.models import Market, OrderBook, Resolution, Signal, Trade


class LiveEngine(ExecutionEngine):
    """Placeholder for live trading via py-clob-client.

    Requires PRIVATE_KEY and FUNDER_ADDRESS in .env.
    Will use py_clob_client.ClobClient for order placement.
    """

    def __init__(self, config) -> None:
        raise NotImplementedError(
            "LiveEngine is not yet implemented. Use TRADING_MODE=paper."
        )

    async def execute_order(
        self, signal: Signal, market: Market, orderbook: OrderBook
    ) -> Trade | None:
        raise NotImplementedError

    async def get_balance(self) -> float:
        raise NotImplementedError

    async def check_resolution(self, market: Market) -> Resolution | None:
        raise NotImplementedError
