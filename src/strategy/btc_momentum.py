from __future__ import annotations

import logging

from src.models import Direction, Market, OrderBook, Signal, SignalType
from src.strategy.base import Strategy

logger = logging.getLogger(__name__)


class BtcMomentumStrategy(Strategy):
    """Intracandle BTC momentum — compares current tick price vs last closed candle.

    DirectionalStrategy looks at multi-minute EMA trends (slow signal).
    This strategy catches sub-minute spikes — fast price moves within the
    current 1-minute candle — making it genuinely independent.

    Data source: PriceFeed (Binance WebSocket, already running).
    - price_feed.latest_price → current tick price (updates ~1s)
    - price_history[-1]       → last closed 1-minute candle close price

    Example:
        BTC closed at $67,000 one minute ago.
        Current tick is $67,200 (+0.30%).
        → Strong BUY_UP signal before the candle closes.
    """

    def __init__(
        self,
        price_feed,  # src.price_feed.PriceFeed (avoid circular import)
        threshold_pct: float = 0.05,
    ) -> None:
        """
        Args:
            price_feed:     Running PriceFeed instance (WebSocket, Binance).
            threshold_pct:  Minimum intracandle % change to emit a signal.
                            Default 0.05 means 0.05% within the current candle.
        """
        self._price_feed = price_feed
        self._threshold = threshold_pct / 100.0

    @property
    def name(self) -> str:
        return "BtcMomentum"

    async def evaluate(
        self,
        market: Market,
        up_book: OrderBook,
        down_book: OrderBook,
        price_history: list[float],
    ) -> Signal:
        current_price = self._price_feed.latest_price
        if current_price is None:
            return Signal(signal_type=SignalType.SKIP, reason="BTC tick price unavailable")

        if not price_history:
            return Signal(signal_type=SignalType.SKIP, reason="no candle history yet")

        last_close = price_history[-1]
        if last_close == 0:
            return Signal(signal_type=SignalType.SKIP, reason="zero last close")

        change = (current_price - last_close) / last_close

        if change > self._threshold:
            confidence = min(1.0, abs(change) / (self._threshold * 3))
            logger.info(
                "BUY_UP — intracandle BTC +%.4f%% (last_close=%.2f → tick=%.2f) confidence=%.2f",
                change * 100, last_close, current_price, confidence,
            )
            return Signal(
                signal_type=SignalType.BUY_UP,
                direction=Direction.UP,
                confidence=confidence,
                reason=f"intracandle+{change*100:.3f}% ({last_close:.0f}→{current_price:.0f})",
            )

        if change < -self._threshold:
            confidence = min(1.0, abs(change) / (self._threshold * 3))
            logger.info(
                "BUY_DOWN — intracandle BTC %.4f%% (last_close=%.2f → tick=%.2f) confidence=%.2f",
                change * 100, last_close, current_price, confidence,
            )
            return Signal(
                signal_type=SignalType.BUY_DOWN,
                direction=Direction.DOWN,
                confidence=confidence,
                reason=f"intracandle{change*100:.3f}% ({last_close:.0f}→{current_price:.0f})",
            )

        logger.debug(
            "BTC intracandle neutral — Δ=%.4f%% (threshold=%.4f%%)",
            change * 100, self._threshold * 100,
        )
        return Signal(
            signal_type=SignalType.SKIP,
            reason=f"intracandle neutral (Δ={change*100:.4f}%)",
        )
