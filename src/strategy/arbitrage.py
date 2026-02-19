from __future__ import annotations

import logging

from src.models import Market, OrderBook, Signal, SignalType
from src.strategy.base import Strategy

logger = logging.getLogger(__name__)

# Polymarket taker fee per side
_TAKER_FEE_RATE = 0.01


class ArbitrageStrategy(Strategy):
    """Detects arbitrage when buying both sides costs less than $1.00 minus fees."""

    @property
    def name(self) -> str:
        return "Arbitrage"

    async def evaluate(
        self,
        market: Market,
        up_book: OrderBook,
        down_book: OrderBook,
        price_history: list[float],
    ) -> Signal:
        up_ask = up_book.best_ask
        down_ask = down_book.best_ask

        if up_ask is None or down_ask is None:
            logger.debug("Missing orderbook data — up_ask=%s down_ask=%s", up_ask, down_ask)
            return Signal(signal_type=SignalType.SKIP, reason="missing orderbook data")

        total_cost = up_ask + down_ask
        raw_profit = 1.0 - total_cost
        fee_estimate = _TAKER_FEE_RATE * total_cost * 2  # fee on each side
        net_profit = raw_profit - fee_estimate

        if net_profit <= 0:
            logger.debug(
                "No arbitrage — total_cost=%.4f raw_profit=%.4f fee=%.4f net=%.4f",
                total_cost, raw_profit, fee_estimate, net_profit,
            )
            return Signal(signal_type=SignalType.SKIP, reason="no profitable arbitrage")

        confidence = min(1.0, net_profit / 0.05)  # scale: 5% net profit = max confidence
        logger.info(
            "ARBITRAGE signal — up_ask=%.4f down_ask=%.4f net_profit=%.4f confidence=%.2f",
            up_ask, down_ask, net_profit, confidence,
        )
        return Signal(
            signal_type=SignalType.ARBITRAGE_BUY,
            confidence=confidence,
            reason=f"up_ask={up_ask:.4f} down_ask={down_ask:.4f} net_profit={net_profit:.4f}",
            arb_down_ask=down_ask,
        )
