from __future__ import annotations

from abc import ABC, abstractmethod

from src.models import Market, OrderBook, Signal


class Strategy(ABC):
    """Base class for all trading strategies."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy name."""

    @abstractmethod
    async def evaluate(
        self,
        market: Market,
        up_book: OrderBook,
        down_book: OrderBook,
        price_history: list[float],
    ) -> Signal:
        """Evaluate market conditions and return a trading signal."""
