from __future__ import annotations

import logging

from src.models import Direction, Market, OrderBook, Signal, SignalType
from src.strategy.base import Strategy

logger = logging.getLogger(__name__)

# EMA periods
_FAST_PERIOD = 3
_SLOW_PERIOD = 8


def _ema(values: list[float], period: int) -> list[float]:
    """Calculate exponential moving average over a list of values."""
    if not values:
        return []
    k = 2.0 / (period + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


class DirectionalStrategy(Strategy):
    """Momentum-based directional strategy using price change rate and EMA crossover."""

    @property
    def name(self) -> str:
        return "Directional"

    async def evaluate(
        self,
        market: Market,
        up_book: OrderBook,
        down_book: OrderBook,
        price_history: list[float],
    ) -> Signal:
        if len(price_history) < _SLOW_PERIOD:
            logger.debug("Not enough price history (%d points)", len(price_history))
            return Signal(signal_type=SignalType.SKIP, reason="insufficient price history")

        # Momentum: rate of change over the full window
        start_price = price_history[0]
        end_price = price_history[-1]
        if start_price == 0:
            return Signal(signal_type=SignalType.SKIP, reason="zero start price")
        momentum = (end_price - start_price) / start_price

        # EMA crossover
        fast_ema = _ema(price_history, _FAST_PERIOD)
        slow_ema = _ema(price_history, _SLOW_PERIOD)
        fast_current = fast_ema[-1]
        slow_current = slow_ema[-1]
        ema_diff = fast_current - slow_current

        # Bullish: positive momentum + fast EMA above slow EMA
        if momentum > 0 and ema_diff > 0:
            confidence = min(1.0, abs(momentum) + abs(ema_diff))
            logger.info(
                "BUY_UP signal — momentum=%.4f ema_diff=%.4f confidence=%.2f",
                momentum, ema_diff, confidence,
            )
            return Signal(
                signal_type=SignalType.BUY_UP,
                direction=Direction.UP,
                confidence=confidence,
                reason=f"momentum={momentum:.4f} ema_diff={ema_diff:.4f}",
            )

        # Bearish: negative momentum + fast EMA below slow EMA
        if momentum < 0 and ema_diff < 0:
            confidence = min(1.0, abs(momentum) + abs(ema_diff))
            logger.info(
                "BUY_DOWN signal — momentum=%.4f ema_diff=%.4f confidence=%.2f",
                momentum, ema_diff, confidence,
            )
            return Signal(
                signal_type=SignalType.BUY_DOWN,
                direction=Direction.DOWN,
                confidence=confidence,
                reason=f"momentum={momentum:.4f} ema_diff={ema_diff:.4f}",
            )

        logger.debug("No clear signal — momentum=%.4f ema_diff=%.4f", momentum, ema_diff)
        return Signal(signal_type=SignalType.SKIP, reason="no clear directional signal")
