from __future__ import annotations

import logging
import time
from collections import deque
from typing import ClassVar, Deque

import httpx

from src.models import Direction, Market, OrderBook, Signal, SignalType
from src.strategy.base import Strategy

logger = logging.getLogger(__name__)

_BINANCE_URL = "https://api.binance.com/api/v3/ticker/price"


class BtcMomentumStrategy(Strategy):
    """Directional signal from real-time BTC/USDT spot price momentum (Binance).

    Fetches BTC spot price from Binance (free, no auth).
    Computes rate-of-change over a rolling window and emits UP/DOWN signals.

    Class-level cache ensures we only call the API once per tick
    even when evaluate() is called for multiple markets simultaneously.
    """

    # Shared across all instances — one fetch per tick for all markets
    _last_fetch_ts: ClassVar[float] = 0.0
    _last_price: ClassVar[float | None] = None
    _price_history: ClassVar[Deque[tuple[float, float]]] = deque()

    def __init__(
        self,
        lookback_sec: int = 60,
        threshold_pct: float = 0.05,
        cache_ttl: float = 5.0,
    ) -> None:
        """
        Args:
            lookback_sec:   Window length in seconds to measure BTC momentum.
            threshold_pct:  Minimum % change required to emit a signal (e.g. 0.05 = 0.05%).
            cache_ttl:      Seconds before re-fetching BTC price from Binance.
        """
        self._lookback = lookback_sec
        self._threshold = threshold_pct / 100.0
        self._cache_ttl = cache_ttl
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "BtcMomentum"

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=5.0)
        return self._client

    async def _fetch_price(self) -> float | None:
        now = time.time()

        # Throttle: reuse last price if still fresh
        if (
            now - BtcMomentumStrategy._last_fetch_ts < self._cache_ttl
            and BtcMomentumStrategy._last_price is not None
        ):
            return BtcMomentumStrategy._last_price

        try:
            resp = await self._get_client().get(
                _BINANCE_URL, params={"symbol": "BTCUSDT"}
            )
            resp.raise_for_status()
            price = float(resp.json()["price"])
        except Exception as exc:
            logger.warning("BTC price fetch failed: %s", exc)
            return BtcMomentumStrategy._last_price  # fall back to last known

        BtcMomentumStrategy._last_price = price
        BtcMomentumStrategy._last_fetch_ts = now
        BtcMomentumStrategy._price_history.append((now, price))

        # Prune: keep at most 5 minutes of history
        cutoff = now - 300.0
        while (
            BtcMomentumStrategy._price_history
            and BtcMomentumStrategy._price_history[0][0] < cutoff
        ):
            BtcMomentumStrategy._price_history.popleft()

        return price

    async def evaluate(
        self,
        market: Market,
        up_book: OrderBook,
        down_book: OrderBook,
        price_history: list[float],
    ) -> Signal:
        current_price = await self._fetch_price()
        if current_price is None:
            return Signal(signal_type=SignalType.SKIP, reason="BTC price unavailable")

        now = time.time()
        cutoff = now - self._lookback
        window = [
            (t, p)
            for t, p in BtcMomentumStrategy._price_history
            if t >= cutoff
        ]

        if len(window) < 2:
            return Signal(
                signal_type=SignalType.SKIP,
                reason=f"insufficient BTC history ({len(window)} points)",
            )

        oldest_price = window[0][1]
        change = (current_price - oldest_price) / oldest_price

        if change > self._threshold:
            # confidence: scales linearly; capped at 1.0 when change = 3× threshold
            confidence = min(1.0, abs(change) / (self._threshold * 3))
            logger.info(
                "BUY_UP — BTC +%.4f%% over %ds | confidence=%.2f",
                change * 100, self._lookback, confidence,
            )
            return Signal(
                signal_type=SignalType.BUY_UP,
                direction=Direction.UP,
                confidence=confidence,
                reason=f"BTC+{change*100:.3f}% in {self._lookback}s",
            )

        if change < -self._threshold:
            confidence = min(1.0, abs(change) / (self._threshold * 3))
            logger.info(
                "BUY_DOWN — BTC %.4f%% over %ds | confidence=%.2f",
                change * 100, self._lookback, confidence,
            )
            return Signal(
                signal_type=SignalType.BUY_DOWN,
                direction=Direction.DOWN,
                confidence=confidence,
                reason=f"BTC{change*100:.3f}% in {self._lookback}s",
            )

        logger.debug("BTC momentum neutral — change=%.4f%%", change * 100)
        return Signal(
            signal_type=SignalType.SKIP,
            reason=f"BTC neutral (Δ={change*100:.4f}%)",
        )
