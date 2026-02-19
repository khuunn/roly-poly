"""Tests for EnsembleStrategy voting logic."""

from __future__ import annotations

from datetime import datetime, timezone

from src.models import Direction, Market, MarketStatus, OrderBook, Signal, SignalType
from src.strategy.base import Strategy
from src.strategy.ensemble import EnsembleStrategy


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


class StubStrategy(Strategy):
    """Strategy that returns a fixed signal."""

    def __init__(self, stub_name: str, signal: Signal) -> None:
        self._name = stub_name
        self._signal = signal

    @property
    def name(self) -> str:
        return self._name

    async def evaluate(self, market, up_book, down_book, price_history) -> Signal:
        return self._signal


class ErrorStrategy(Strategy):
    """Strategy that raises an exception."""

    @property
    def name(self) -> str:
        return "Error"

    async def evaluate(self, market, up_book, down_book, price_history) -> Signal:
        raise RuntimeError("boom")


def _skip() -> Signal:
    return Signal(signal_type=SignalType.SKIP, reason="skip")


def _up(confidence: float = 0.7) -> Signal:
    return Signal(
        signal_type=SignalType.BUY_UP,
        direction=Direction.UP,
        confidence=confidence,
        reason="up",
    )


def _down(confidence: float = 0.7) -> Signal:
    return Signal(
        signal_type=SignalType.BUY_DOWN,
        direction=Direction.DOWN,
        confidence=confidence,
        reason="down",
    )


class TestEnsembleStrategy:
    market = _make_market()
    up_book = OrderBook(token_id="up-token")
    down_book = OrderBook(token_id="down-token")
    prices: list[float] = []

    async def test_majority_up(self):
        """EMA=UP, Orderbook=UP, LLM=SKIP → 2/2 UP → BUY_UP"""
        ensemble = EnsembleStrategy(
            strategies=[
                StubStrategy("EMA", _up(0.72)),
                StubStrategy("OB", _up(0.65)),
                StubStrategy("LLM", _skip()),
            ],
            min_votes=2,
        )
        signal = await ensemble.evaluate(self.market, self.up_book, self.down_book, self.prices)

        assert signal.signal_type == SignalType.BUY_UP
        assert signal.direction == Direction.UP
        # avg confidence of agreeing: (0.72 + 0.65) / 2 = 0.685
        assert abs(signal.confidence - 0.685) < 0.01

    async def test_majority_down(self):
        """EMA=DOWN, Orderbook=DOWN, LLM=SKIP → 2/2 DOWN → BUY_DOWN"""
        ensemble = EnsembleStrategy(
            strategies=[
                StubStrategy("EMA", _down(0.80)),
                StubStrategy("OB", _down(0.60)),
                StubStrategy("LLM", _skip()),
            ],
            min_votes=2,
        )
        signal = await ensemble.evaluate(self.market, self.up_book, self.down_book, self.prices)

        assert signal.signal_type == SignalType.BUY_DOWN
        assert signal.direction == Direction.DOWN

    async def test_tie_results_in_skip(self):
        """EMA=UP, Orderbook=DOWN, LLM=SKIP → 1 UP vs 1 DOWN → SKIP"""
        ensemble = EnsembleStrategy(
            strategies=[
                StubStrategy("EMA", _up()),
                StubStrategy("OB", _down()),
                StubStrategy("LLM", _skip()),
            ],
            min_votes=2,
        )
        signal = await ensemble.evaluate(self.market, self.up_book, self.down_book, self.prices)

        assert signal.signal_type == SignalType.SKIP
        assert "tie" in signal.reason

    async def test_insufficient_votes_skip(self):
        """Only 1 non-SKIP signal with min_votes=2 → SKIP"""
        ensemble = EnsembleStrategy(
            strategies=[
                StubStrategy("EMA", _up()),
                StubStrategy("OB", _skip()),
                StubStrategy("LLM", _skip()),
            ],
            min_votes=2,
        )
        signal = await ensemble.evaluate(self.market, self.up_book, self.down_book, self.prices)

        assert signal.signal_type == SignalType.SKIP
        assert "1/3 active" in signal.reason

    async def test_all_skip(self):
        """All strategies SKIP → SKIP"""
        ensemble = EnsembleStrategy(
            strategies=[
                StubStrategy("EMA", _skip()),
                StubStrategy("OB", _skip()),
                StubStrategy("LLM", _skip()),
            ],
            min_votes=2,
        )
        signal = await ensemble.evaluate(self.market, self.up_book, self.down_book, self.prices)

        assert signal.signal_type == SignalType.SKIP

    async def test_two_up_one_down(self):
        """EMA=UP, Orderbook=DOWN, LLM=UP → 2/3 UP → BUY_UP"""
        ensemble = EnsembleStrategy(
            strategies=[
                StubStrategy("EMA", _up(0.72)),
                StubStrategy("OB", _down(0.60)),
                StubStrategy("LLM", _up(0.80)),
            ],
            min_votes=2,
        )
        signal = await ensemble.evaluate(self.market, self.up_book, self.down_book, self.prices)

        assert signal.signal_type == SignalType.BUY_UP
        assert signal.direction == Direction.UP
        # avg of agreeing UP signals: (0.72 + 0.80) / 2 = 0.76
        assert abs(signal.confidence - 0.76) < 0.01

    async def test_reason_contains_vote_details(self):
        """Reason string should include individual strategy votes."""
        ensemble = EnsembleStrategy(
            strategies=[
                StubStrategy("EMA", _up(0.72)),
                StubStrategy("OB", _up(0.65)),
                StubStrategy("LLM", _skip()),
            ],
            min_votes=2,
        )
        signal = await ensemble.evaluate(self.market, self.up_book, self.down_book, self.prices)

        assert "EMA" in signal.reason
        assert "OB" in signal.reason
        assert "LLM" in signal.reason

    async def test_handles_strategy_exception(self):
        """Strategy that throws should be treated as absent, not crash ensemble."""
        ensemble = EnsembleStrategy(
            strategies=[
                StubStrategy("EMA", _up(0.72)),
                StubStrategy("OB", _up(0.65)),
                ErrorStrategy(),
            ],
            min_votes=2,
        )
        signal = await ensemble.evaluate(self.market, self.up_book, self.down_book, self.prices)

        assert signal.signal_type == SignalType.BUY_UP

    async def test_min_votes_one(self):
        """With min_votes=1, a single non-SKIP signal is enough."""
        ensemble = EnsembleStrategy(
            strategies=[
                StubStrategy("EMA", _up(0.72)),
                StubStrategy("OB", _skip()),
                StubStrategy("LLM", _skip()),
            ],
            min_votes=1,
        )
        signal = await ensemble.evaluate(self.market, self.up_book, self.down_book, self.prices)

        assert signal.signal_type == SignalType.BUY_UP

    async def test_three_up_unanimous(self):
        """All three agree UP → BUY_UP with averaged confidence."""
        ensemble = EnsembleStrategy(
            strategies=[
                StubStrategy("EMA", _up(0.60)),
                StubStrategy("OB", _up(0.80)),
                StubStrategy("LLM", _up(0.70)),
            ],
            min_votes=2,
        )
        signal = await ensemble.evaluate(self.market, self.up_book, self.down_book, self.prices)

        assert signal.signal_type == SignalType.BUY_UP
        # avg = (0.60 + 0.80 + 0.70) / 3 = 0.70
        assert abs(signal.confidence - 0.70) < 0.01
