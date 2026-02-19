"""Tests for dynamic position sizing."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.config import Config
from src.engine.paper import PaperEngine, _TAKER_FEE_RATE
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

def _make_config(  # noqa: PLR0913
    *,
    capital: float = 1000.0,
    bet: float = 10.0,
    sizing_mode: str = "fixed",
    position_size_pct: float = 0.02,
    min_bet_size: float = 1.0,
    max_bet_size: float | None = None,
) -> Config:
    return Config(
        initial_capital=capital,
        bet_size=bet,
        max_bet_size=max_bet_size if max_bet_size is not None else bet,
        sizing_mode=sizing_mode,
        position_size_pct=position_size_pct,
        min_bet_size=min_bet_size,
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

class TestConfigDefaults:
    def test_default_sizing_mode(self) -> None:
        cfg = Config()
        assert cfg.sizing_mode == "fixed"

    def test_default_position_size_pct(self) -> None:
        cfg = Config()
        assert cfg.position_size_pct == 0.02

    def test_default_min_bet_size(self) -> None:
        cfg = Config()
        assert cfg.min_bet_size == 1.0


# ---------------------------------------------------------------------------
# _calculate_bet_size 단위 테스트
# ---------------------------------------------------------------------------

class TestCalculateBetSize:
    def test_fixed_mode_returns_bet_size(self) -> None:
        """fixed 모드에서는 항상 bet_size 반환."""
        engine = PaperEngine(_make_config(bet=5.0, sizing_mode="fixed"))
        assert engine._calculate_bet_size(0.6) == 5.0
        assert engine._calculate_bet_size(1.0) == 5.0

    def test_dynamic_base_calculation(self) -> None:
        """dynamic 모드: balance × pct × scale 기본 계산."""
        # balance=1000, pct=0.02, confidence=0.8 → base=20, scale=0.9, sized=18
        engine = PaperEngine(
            _make_config(
                capital=1000.0,
                sizing_mode="dynamic",
                position_size_pct=0.02,
                max_bet_size=100.0,
            )
        )
        result = engine._calculate_bet_size(0.8)
        expected = 1000.0 * 0.02 * (0.5 + 0.5 * 0.8)  # 20 * 0.9 = 18
        assert result == pytest.approx(expected)

    def test_dynamic_confidence_scaling(self) -> None:
        """confidence 높을수록 큰 베팅."""
        engine = PaperEngine(
            _make_config(
                capital=1000.0,
                sizing_mode="dynamic",
                position_size_pct=0.02,
                max_bet_size=100.0,
            )
        )
        low = engine._calculate_bet_size(0.6)
        high = engine._calculate_bet_size(1.0)
        assert high > low

    def test_dynamic_clamped_to_max(self) -> None:
        """max_bet_size 초과 방지."""
        engine = PaperEngine(
            _make_config(
                capital=100_000.0,
                sizing_mode="dynamic",
                position_size_pct=0.1,
                max_bet_size=50.0,
            )
        )
        result = engine._calculate_bet_size(1.0)
        assert result == 50.0

    def test_dynamic_clamped_to_min(self) -> None:
        """min_bet_size 미만 방지."""
        engine = PaperEngine(
            _make_config(
                capital=10.0,
                sizing_mode="dynamic",
                position_size_pct=0.001,
                min_bet_size=2.0,
                max_bet_size=100.0,
            )
        )
        # base = 10 * 0.001 = 0.01, scale ≈ 0.8 → sized ≈ 0.008 → clamped to 2.0
        result = engine._calculate_bet_size(0.6)
        assert result == 2.0

    def test_dynamic_low_balance(self) -> None:
        """잔액 적을 때 최소 베팅으로 클램핑."""
        engine = PaperEngine(
            _make_config(
                capital=5.0,
                sizing_mode="dynamic",
                position_size_pct=0.02,
                min_bet_size=1.0,
                max_bet_size=100.0,
            )
        )
        # base = 5 * 0.02 = 0.1 → clamped to min 1.0
        result = engine._calculate_bet_size(0.8)
        assert result == 1.0


# ---------------------------------------------------------------------------
# 동적 사이징 실행 통합 테스트
# ---------------------------------------------------------------------------

class TestDynamicExecution:
    @pytest.mark.asyncio
    async def test_directional_uses_dynamic_size(self) -> None:
        """방향성 거래에서 동적 사이징 적용."""
        engine = PaperEngine(
            _make_config(
                capital=1000.0,
                sizing_mode="dynamic",
                position_size_pct=0.02,
                max_bet_size=100.0,
            )
        )
        signal = _make_signal(confidence=0.8)
        market = _make_market()
        ob = _make_orderbook()

        trade = await engine.execute_order(signal, market, ob)

        expected_bet = 1000.0 * 0.02 * (0.5 + 0.5 * 0.8)  # 18.0
        assert trade is not None
        assert trade.amount == pytest.approx(expected_bet)
        assert trade.fee == pytest.approx(expected_bet * _TAKER_FEE_RATE)

    @pytest.mark.asyncio
    async def test_arbitrage_uses_dynamic_size(self) -> None:
        """차익 거래에서 동적 사이징 적용."""
        engine = PaperEngine(
            _make_config(
                capital=1000.0,
                sizing_mode="dynamic",
                position_size_pct=0.02,
                max_bet_size=100.0,
            )
        )
        signal = _make_signal(
            signal_type=SignalType.ARBITRAGE_BUY,
            confidence=0.9,
            arb_down_ask=0.40,
        )
        market = _make_market()
        ob = _make_orderbook()

        trade = await engine.execute_order(signal, market, ob)

        single_bet = 1000.0 * 0.02 * (0.5 + 0.5 * 0.9)  # 20 * 0.95 = 19.0
        assert trade is not None
        assert trade.amount == pytest.approx(single_bet * 2)  # 양쪽 합산
        assert trade.fee == pytest.approx(single_bet * _TAKER_FEE_RATE * 2)

    @pytest.mark.asyncio
    async def test_insufficient_balance_still_rejected(self) -> None:
        """잔액 부족 시 여전히 None 반환."""
        engine = PaperEngine(
            _make_config(
                capital=0.5,
                sizing_mode="dynamic",
                position_size_pct=0.02,
                min_bet_size=1.0,
                max_bet_size=100.0,
            )
        )
        signal = _make_signal(confidence=0.8)
        market = _make_market()
        ob = _make_orderbook()

        # min_bet_size=1.0이지만 잔액=0.5 → 거래 불가
        trade = await engine.execute_order(signal, market, ob)
        assert trade is None
