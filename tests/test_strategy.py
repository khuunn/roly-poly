"""Tests for directional and arbitrage strategy modules."""

from __future__ import annotations

from datetime import datetime, timezone


from src.models import Direction, Market, MarketStatus, OrderBook, OrderBookLevel, SignalType
from src.strategy.arbitrage import ArbitrageStrategy
from src.strategy.directional import DirectionalStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_market() -> Market:
    return Market(
        market_id="test-market-1",
        slug="test-market",
        question="Will it happen?",
        status=MarketStatus.ACTIVE,
        up_token_id="up-token",
        down_token_id="down-token",
        end_time=datetime(2026, 12, 31, tzinfo=timezone.utc),
    )


def _make_orderbook(
    token_id: str = "token",
    best_ask: float | None = None,
    best_bid: float | None = None,
) -> OrderBook:
    asks = [OrderBookLevel(price=best_ask, size=100.0)] if best_ask is not None else []
    bids = [OrderBookLevel(price=best_bid, size=100.0)] if best_bid is not None else []
    return OrderBook(token_id=token_id, bids=bids, asks=asks)


def _rising_prices(length: int = 10) -> list[float]:
    """Generate steadily rising prices from 0.40 to ~0.58."""
    return [0.40 + i * 0.02 for i in range(length)]


def _falling_prices(length: int = 10) -> list[float]:
    """Generate steadily falling prices from 0.60 to ~0.42."""
    return [0.60 - i * 0.02 for i in range(length)]


# ===========================================================================
# DirectionalStrategy
# ===========================================================================

class TestDirectionalStrategy:
    strategy = DirectionalStrategy()
    market = _make_market()
    up_book = _make_orderbook("up-token", best_ask=0.50)
    down_book = _make_orderbook("down-token", best_ask=0.50)

    async def test_buy_up_on_rising_prices(self):
        prices = _rising_prices(10)
        signal = await self.strategy.evaluate(self.market, self.up_book, self.down_book, prices)

        assert signal.signal_type == SignalType.BUY_UP
        assert signal.direction == Direction.UP
        assert signal.confidence > 0

    async def test_buy_down_on_falling_prices(self):
        prices = _falling_prices(10)
        signal = await self.strategy.evaluate(self.market, self.up_book, self.down_book, prices)

        assert signal.signal_type == SignalType.BUY_DOWN
        assert signal.direction == Direction.DOWN
        assert signal.confidence > 0

    async def test_skip_when_insufficient_history(self):
        prices = [0.50, 0.51, 0.52]  # only 3 points, need >= 8
        signal = await self.strategy.evaluate(self.market, self.up_book, self.down_book, prices)

        assert signal.signal_type == SignalType.SKIP
        assert "insufficient" in signal.reason

    async def test_skip_when_mixed_signals(self):
        # Negative momentum (end < start) but fast EMA above slow EMA.
        # Start high, deep dip, strong recovery that doesn't reach start.
        prices = [0.60, 0.40, 0.35, 0.30, 0.28, 0.30, 0.45, 0.55]
        signal = await self.strategy.evaluate(self.market, self.up_book, self.down_book, prices)

        assert signal.signal_type == SignalType.SKIP
        assert "no clear" in signal.reason

    async def test_confidence_clamped_to_one(self):
        # Extreme price movement to push momentum + ema_diff > 1.0
        prices = [0.10, 0.12, 0.15, 0.20, 0.30, 0.50, 0.80, 0.95]
        signal = await self.strategy.evaluate(self.market, self.up_book, self.down_book, prices)

        assert signal.signal_type == SignalType.BUY_UP
        assert signal.confidence == 1.0


# ===========================================================================
# ArbitrageStrategy
# ===========================================================================

class TestArbitrageStrategy:
    strategy = ArbitrageStrategy()
    market = _make_market()
    price_history: list[float] = []  # arbitrage doesn't use price history

    async def test_arbitrage_buy_when_profitable(self):
        # up 0.40 + down 0.40 = 0.80, raw profit 0.20
        # fee = 0.01 * 0.80 * 2 = 0.016, net = 0.184
        up_book = _make_orderbook("up-token", best_ask=0.40)
        down_book = _make_orderbook("down-token", best_ask=0.40)
        signal = await self.strategy.evaluate(self.market, up_book, down_book, self.price_history)

        assert signal.signal_type == SignalType.ARBITRAGE_BUY
        assert signal.confidence > 0

    async def test_skip_when_no_opportunity(self):
        # up 0.55 + down 0.50 = 1.05 >= 1.0
        up_book = _make_orderbook("up-token", best_ask=0.55)
        down_book = _make_orderbook("down-token", best_ask=0.50)
        signal = await self.strategy.evaluate(self.market, up_book, down_book, self.price_history)

        assert signal.signal_type == SignalType.SKIP

    async def test_skip_when_profit_below_fee_threshold(self):
        # up 0.49 + down 0.49 = 0.98, raw profit = 0.02
        # fee = 0.01 * 0.98 * 2 = 0.0196, net = 0.0004 > 0 — barely profitable
        # Try: up 0.495 + down 0.495 = 0.99, raw profit = 0.01
        # fee = 0.01 * 0.99 * 2 = 0.0198, net = -0.0098 <= 0
        up_book = _make_orderbook("up-token", best_ask=0.495)
        down_book = _make_orderbook("down-token", best_ask=0.495)
        signal = await self.strategy.evaluate(self.market, up_book, down_book, self.price_history)

        assert signal.signal_type == SignalType.SKIP
        assert "no profitable" in signal.reason

    async def test_skip_when_no_asks(self):
        up_book = _make_orderbook("up-token", best_ask=None)
        down_book = _make_orderbook("down-token", best_ask=0.45)
        signal = await self.strategy.evaluate(self.market, up_book, down_book, self.price_history)

        assert signal.signal_type == SignalType.SKIP
        assert "missing" in signal.reason

    async def test_confidence_scales_with_net_profit(self):
        # Small profit: up 0.48 + down 0.48 = 0.96, raw = 0.04, fee = 0.0192, net = 0.0208
        # confidence = 0.0208 / 0.05 = 0.416
        small_up = _make_orderbook("up-token", best_ask=0.48)
        small_down = _make_orderbook("down-token", best_ask=0.48)
        small_signal = await self.strategy.evaluate(
            self.market, small_up, small_down, self.price_history
        )

        # Larger profit: up 0.43 + down 0.43 = 0.86, raw = 0.14, fee = 0.0172, net = 0.1228
        # confidence = min(1.0, 0.1228 / 0.05) = 1.0
        large_up = _make_orderbook("up-token", best_ask=0.43)
        large_down = _make_orderbook("down-token", best_ask=0.43)
        large_signal = await self.strategy.evaluate(
            self.market, large_up, large_down, self.price_history
        )

        assert 0 < small_signal.confidence < 1.0
        assert large_signal.confidence > small_signal.confidence
        assert large_signal.confidence == 1.0
