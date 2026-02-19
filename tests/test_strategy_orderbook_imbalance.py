"""Tests for OrderbookImbalanceStrategy."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.models import Direction, Market, MarketStatus, OrderBook, OrderBookLevel, SignalType
from src.strategy.orderbook_imbalance import OrderbookImbalanceStrategy


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


def _make_config(threshold: float = 1.5) -> MagicMock:
    config = MagicMock()
    config.imbalance_threshold = threshold
    return config


def _make_orderbook(
    token_id: str = "token",
    bids: list[tuple[float, float]] | None = None,
    asks: list[tuple[float, float]] | None = None,
) -> OrderBook:
    bid_levels = [OrderBookLevel(price=p, size=s) for p, s in (bids or [])]
    ask_levels = [OrderBookLevel(price=p, size=s) for p, s in (asks or [])]
    return OrderBook(token_id=token_id, bids=bid_levels, asks=ask_levels)


class TestOrderbookImbalanceStrategy:
    market = _make_market()
    down_book = _make_orderbook("down-token")
    price_history: list[float] = []

    async def test_buy_up_when_bids_dominate(self):
        config = _make_config(threshold=1.5)
        strategy = OrderbookImbalanceStrategy(config)

        # bid_vol=300, ask_vol=100 → ratio=3.0 >= 1.5
        up_book = _make_orderbook(
            "up-token",
            bids=[(0.50, 100), (0.49, 100), (0.48, 100)],
            asks=[(0.51, 100)],
        )
        signal = await strategy.evaluate(self.market, up_book, self.down_book, self.price_history)

        assert signal.signal_type == SignalType.BUY_UP
        assert signal.direction == Direction.UP
        assert signal.confidence > 0

    async def test_buy_down_when_asks_dominate(self):
        config = _make_config(threshold=1.5)
        strategy = OrderbookImbalanceStrategy(config)

        # bid_vol=100, ask_vol=300 → ratio=0.333 <= 1/1.5=0.667
        up_book = _make_orderbook(
            "up-token",
            bids=[(0.50, 100)],
            asks=[(0.51, 100), (0.52, 100), (0.53, 100)],
        )
        signal = await strategy.evaluate(self.market, up_book, self.down_book, self.price_history)

        assert signal.signal_type == SignalType.BUY_DOWN
        assert signal.direction == Direction.DOWN
        assert signal.confidence > 0

    async def test_skip_when_balanced(self):
        config = _make_config(threshold=1.5)
        strategy = OrderbookImbalanceStrategy(config)

        # bid_vol=100, ask_vol=100 → ratio=1.0
        up_book = _make_orderbook(
            "up-token",
            bids=[(0.50, 100)],
            asks=[(0.51, 100)],
        )
        signal = await strategy.evaluate(self.market, up_book, self.down_book, self.price_history)

        assert signal.signal_type == SignalType.SKIP
        assert "no imbalance" in signal.reason

    async def test_skip_when_empty_orderbook(self):
        config = _make_config(threshold=1.5)
        strategy = OrderbookImbalanceStrategy(config)

        up_book = _make_orderbook("up-token", bids=[], asks=[])
        signal = await strategy.evaluate(self.market, up_book, self.down_book, self.price_history)

        assert signal.signal_type == SignalType.SKIP
        assert "empty" in signal.reason

    async def test_buy_up_when_no_asks(self):
        config = _make_config(threshold=1.5)
        strategy = OrderbookImbalanceStrategy(config)

        # No asks → ask_vol=0 → special case
        up_book = _make_orderbook(
            "up-token",
            bids=[(0.50, 100)],
            asks=[],
        )
        signal = await strategy.evaluate(self.market, up_book, self.down_book, self.price_history)

        assert signal.signal_type == SignalType.BUY_UP
        assert signal.confidence == 1.0

    async def test_confidence_scales_with_ratio(self):
        config = _make_config(threshold=1.5)
        strategy = OrderbookImbalanceStrategy(config)

        # Moderate imbalance: ratio=2.0 → confidence = (2.0-1)/2 = 0.5
        moderate = _make_orderbook(
            "up-token",
            bids=[(0.50, 200)],
            asks=[(0.51, 100)],
        )
        sig_moderate = await strategy.evaluate(
            self.market, moderate, self.down_book, self.price_history
        )

        # Strong imbalance: ratio=5.0 → confidence = min(1.0, (5.0-1)/2) = 1.0
        strong = _make_orderbook(
            "up-token",
            bids=[(0.50, 500)],
            asks=[(0.51, 100)],
        )
        sig_strong = await strategy.evaluate(
            self.market, strong, self.down_book, self.price_history
        )

        assert 0 < sig_moderate.confidence < sig_strong.confidence
        assert sig_strong.confidence == 1.0

    async def test_threshold_configurable(self):
        # Stricter threshold
        strict = OrderbookImbalanceStrategy(_make_config(threshold=3.0))

        # ratio=2.0 < 3.0 → SKIP with strict threshold
        up_book = _make_orderbook(
            "up-token",
            bids=[(0.50, 200)],
            asks=[(0.51, 100)],
        )
        signal = await strict.evaluate(self.market, up_book, self.down_book, self.price_history)
        assert signal.signal_type == SignalType.SKIP

        # Lenient threshold → same ratio triggers signal
        lenient = OrderbookImbalanceStrategy(_make_config(threshold=1.5))
        signal2 = await lenient.evaluate(self.market, up_book, self.down_book, self.price_history)
        assert signal2.signal_type == SignalType.BUY_UP
