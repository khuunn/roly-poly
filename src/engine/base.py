from __future__ import annotations

from abc import ABC, abstractmethod

from src.models import Market, OrderBook, Resolution, Signal, Trade


class ExecutionEngine(ABC):
    """Abstract execution engine — swap PaperEngine/LiveEngine via config."""

    @abstractmethod
    async def execute_order(
        self, signal: Signal, market: Market, orderbook: OrderBook
    ) -> Trade | None:
        """Execute an order based on the signal. Returns Trade or None if skipped."""

    @abstractmethod
    async def get_balance(self) -> float:
        """Return current available balance."""

    @abstractmethod
    async def check_resolution(self, market: Market) -> Resolution | None:
        """Check if a market has resolved. Returns Resolution or None."""

    async def credit_resolution_payout(self, payout: float) -> None:
        """Credit payout back to engine balance after market resolution."""

    async def topup(self, amount: float) -> None:
        """Top up engine balance. Only meaningful for paper trading."""

    async def restore_balance(self, balance: float) -> None:
        """Restore engine balance from persisted snapshot. No-op for live engines."""
