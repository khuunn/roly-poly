from __future__ import annotations

import logging

from src.config import Config
from src.models import Direction, Market, OrderBook, Signal, SignalType
from src.strategy.base import Strategy

logger = logging.getLogger(__name__)


class OrderbookImbalanceStrategy(Strategy):
    """Detects directional bias from bid/ask volume imbalance in the orderbook."""

    def __init__(self, config: Config) -> None:
        self._threshold = config.imbalance_threshold

    @property
    def name(self) -> str:
        return "OrderbookImbalance"

    async def evaluate(
        self,
        market: Market,
        up_book: OrderBook,
        down_book: OrderBook,
        price_history: list[float],
    ) -> Signal:
        bid_vol = sum(lvl.size for lvl in up_book.bids)
        ask_vol = sum(lvl.size for lvl in up_book.asks)

        if bid_vol == 0 and ask_vol == 0:
            return Signal(signal_type=SignalType.SKIP, reason="empty orderbook")

        if ask_vol == 0:
            return Signal(
                signal_type=SignalType.BUY_UP,
                direction=Direction.UP,
                confidence=1.0,
                reason=f"bid_vol={bid_vol:.1f} ask_vol=0 ratio=inf",
            )

        if bid_vol == 0:
            return Signal(
                signal_type=SignalType.BUY_DOWN,
                direction=Direction.DOWN,
                confidence=1.0,
                reason=f"bid_vol=0 ask_vol={ask_vol:.1f} ratio=0",
            )

        ratio = bid_vol / ask_vol

        if ratio >= self._threshold:
            confidence = min(1.0, (ratio - 1) / 2)
            logger.info(
                "BUY_UP signal — bid/ask ratio=%.2f threshold=%.2f confidence=%.2f",
                ratio, self._threshold, confidence,
            )
            return Signal(
                signal_type=SignalType.BUY_UP,
                direction=Direction.UP,
                confidence=confidence,
                reason=f"bid/ask={ratio:.2f}",
            )

        inverse = 1 / self._threshold
        if ratio <= inverse:
            confidence = min(1.0, (1 / ratio - 1) / 2)
            logger.info(
                "BUY_DOWN signal — bid/ask ratio=%.2f threshold=%.2f confidence=%.2f",
                ratio, self._threshold, confidence,
            )
            return Signal(
                signal_type=SignalType.BUY_DOWN,
                direction=Direction.DOWN,
                confidence=confidence,
                reason=f"bid/ask={ratio:.2f}",
            )

        logger.debug("No imbalance — bid/ask ratio=%.2f", ratio)
        return Signal(signal_type=SignalType.SKIP, reason=f"no imbalance (ratio={ratio:.2f})")
