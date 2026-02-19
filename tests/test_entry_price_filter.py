"""Tests for max entry price filter."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.config import Config
from src.engine.paper import PaperEngine
from src.models import (
    Market,
    MarketStatus,
    OrderBook,
    OrderBookLevel,
    Signal,
    SignalType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    *,
    capital: float = 1000.0,
    max_entry_price: float = 0.70,
) -> Config:
    return Config(
        initial_capital=capital,
        bet_size=5.0,
        max_bet_size=5.0,
        max_entry_price=max_entry_price,
    )


def _make_market() -> Market:
    return Market(
        market_id="mkt-1",
        slug="test-market",
        question="Will BTC go up?",
        status=MarketStatus.ACTIVE,
        up_token_id="tok-up",
        down_token_id="tok-down",
        end_time=datetime(2026, 12, 31, tzinfo=timezone.utc),
        up_price=0.55,
        down_price=0.45,
    )


def _make_orderbook(best_ask: float = 0.55) -> OrderBook:
    asks = [OrderBookLevel(price=best_ask, size=100.0)]
    return OrderBook(token_id="tok-up", asks=asks)


def _make_signal(
    signal_type: SignalType = SignalType.BUY_UP,
    confidence: float = 0.8,
    arb_down_ask: float | None = None,
) -> Signal:
    return Signal(
        signal_type=signal_type,
        confidence=confidence,
        reason="test",
        arb_down_ask=arb_down_ask,
    )


# ---------------------------------------------------------------------------
# Config 기본값 테스트
# ---------------------------------------------------------------------------

class TestConfigDefault:
    def test_default_max_entry_price(self) -> None:
        cfg = Config()
        assert cfg.max_entry_price == 0.70


# ---------------------------------------------------------------------------
# 매입가 필터 테스트
# ---------------------------------------------------------------------------

class TestEntryPriceFilter:
    @pytest.mark.asyncio
    async def test_directional_rejected_when_ask_too_high(self) -> None:
        """ask > max_entry_price → None 반환."""
        engine = PaperEngine(_make_config(max_entry_price=0.70))
        trade = await engine.execute_order(
            _make_signal(), _make_market(), _make_orderbook(best_ask=0.75),
        )
        assert trade is None

    @pytest.mark.asyncio
    async def test_directional_accepted_when_ask_at_limit(self) -> None:
        """ask == max_entry_price → 정상 거래."""
        engine = PaperEngine(_make_config(max_entry_price=0.70))
        trade = await engine.execute_order(
            _make_signal(), _make_market(), _make_orderbook(best_ask=0.70),
        )
        assert trade is not None

    @pytest.mark.asyncio
    async def test_directional_accepted_when_ask_below_limit(self) -> None:
        """ask < max_entry_price → 정상 거래."""
        engine = PaperEngine(_make_config(max_entry_price=0.70))
        trade = await engine.execute_order(
            _make_signal(), _make_market(), _make_orderbook(best_ask=0.55),
        )
        assert trade is not None

    @pytest.mark.asyncio
    async def test_arbitrage_rejected_when_ask_too_high(self) -> None:
        """arb up_ask > max_entry_price → None 반환."""
        engine = PaperEngine(_make_config(max_entry_price=0.70))
        signal = _make_signal(
            signal_type=SignalType.ARBITRAGE_BUY,
            arb_down_ask=0.40,
        )
        trade = await engine.execute_order(
            signal, _make_market(), _make_orderbook(best_ask=0.75),
        )
        assert trade is None

    @pytest.mark.asyncio
    async def test_arbitrage_accepted_when_ask_below_limit(self) -> None:
        """arb up_ask < max_entry_price → 정상 거래."""
        engine = PaperEngine(_make_config(max_entry_price=0.70))
        signal = _make_signal(
            signal_type=SignalType.ARBITRAGE_BUY,
            arb_down_ask=0.40,
        )
        trade = await engine.execute_order(
            signal, _make_market(), _make_orderbook(best_ask=0.55),
        )
        assert trade is not None

    @pytest.mark.asyncio
    async def test_custom_max_entry_price(self) -> None:
        """다른 max_entry_price 설정값으로 필터 작동 확인."""
        engine = PaperEngine(_make_config(max_entry_price=0.50))
        # ask=0.55 > max=0.50 → 거부
        trade = await engine.execute_order(
            _make_signal(), _make_market(), _make_orderbook(best_ask=0.55),
        )
        assert trade is None

        # ask=0.45 < max=0.50 → 허용
        trade = await engine.execute_order(
            _make_signal(), _make_market(), _make_orderbook(best_ask=0.45),
        )
        assert trade is not None
