"""Edge case tests — zero values, boundary conditions, division safety."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.config import Config
from src.engine.paper import PaperEngine
from src.models import (
    Direction,
    Market,
    MarketStatus,
    OrderBook,
    OrderBookLevel,
    Resolution,
    ResolutionOutcome,
    Signal,
    SignalType,
    Trade,
)
from src.portfolio import Portfolio
from src.repository.base import Repository
from src.strategy.directional import DirectionalStrategy


class FakeRepository(Repository):
    def __init__(self):
        self._trades = []
        self._snapshots = []

    async def initialize(self): pass
    async def save_trade(self, trade): self._trades.append(trade)
    async def get_trades(self, limit=50): return self._trades[:limit]
    async def get_resolved_trades(self): return [t for t in self._trades if t.resolved]
    async def update_trade_resolution(self, trade_id, pnl):
        for t in self._trades:
            if t.trade_id == trade_id:
                t.pnl = pnl
                t.resolved = True
    async def save_portfolio_snapshot(self, snap): self._snapshots.append(snap)
    async def get_latest_snapshot(self): return self._snapshots[-1] if self._snapshots else None
    async def save_market(self, market): pass
    async def get_market(self, market_id): return None
    async def get_trades_since(self, since):
        return [t for t in self._trades if t.timestamp >= since]
    async def get_snapshots(self, limit=100):
        return self._snapshots[:limit]
    async def get_open_trades_for_market(self, market_id):
        return [t for t in self._trades if t.market_id == market_id and not t.resolved]
    async def close(self): pass


def _make_config(**kw) -> Config:
    bet = kw.get("bet", 10.0)
    return Config(
        initial_capital=kw.get("capital", 1000.0),
        bet_size=bet,
        max_bet_size=kw.get("max_bet_size", bet),
    )


def _make_market() -> Market:
    return Market(
        market_id="mkt-1", slug="btc-updown-5m-test",
        question="Up or Down?", status=MarketStatus.ACTIVE,
        up_token_id="tok-up", down_token_id="tok-down",
        end_time=datetime(2025, 6, 1, 12, 5, 0, tzinfo=timezone.utc),
    )


def _make_orderbook(best_ask: float | None = 0.50) -> OrderBook:
    asks = [OrderBookLevel(price=best_ask, size=100.0)] if best_ask is not None else []
    return OrderBook(token_id="tok-up", asks=asks)


# === Portfolio zero/edge cases ===

class TestZeroPriceGuard:
    """Division-by-zero guard when trade.price == 0."""

    async def test_directional_win_with_zero_price(self):
        repo = FakeRepository()
        portfolio = Portfolio(_make_config(), repo)
        trade = Trade(
            trade_id="t-zero", market_id="mkt-1",
            direction=Direction.UP, token_id="tok-up",
            amount=10.0, price=0.0, fee=0.1,
            signal_type=SignalType.BUY_UP,
        )
        await portfolio.record_trade(trade)
        resolution = Resolution(market_id="mkt-1", outcome=ResolutionOutcome.UP)
        # Should not raise ZeroDivisionError
        await portfolio.handle_resolution(trade, resolution)
        assert trade.pnl == 0.0

    async def test_arbitrage_with_zero_up_price(self):
        repo = FakeRepository()
        portfolio = Portfolio(_make_config(), repo)
        trade = Trade(
            trade_id="t-arb0", market_id="mkt-1",
            direction=Direction.UP, token_id="tok-up",
            amount=20.0, price=0.0, fee=0.2,
            signal_type=SignalType.ARBITRAGE_BUY,
            alt_price=0.50,
        )
        await portfolio.record_trade(trade)
        resolution = Resolution(market_id="mkt-1", outcome=ResolutionOutcome.UP)
        await portfolio.handle_resolution(trade, resolution)
        # up_shares = 0 (guarded), payout = 0
        assert trade.pnl == pytest.approx(0.0 - 20.0 - 0.2)

    async def test_arbitrage_with_zero_down_price(self):
        repo = FakeRepository()
        portfolio = Portfolio(_make_config(), repo)
        trade = Trade(
            trade_id="t-arb0d", market_id="mkt-1",
            direction=Direction.UP, token_id="tok-up",
            amount=20.0, price=0.50, fee=0.2,
            signal_type=SignalType.ARBITRAGE_BUY,
            alt_price=0.0,
        )
        await portfolio.record_trade(trade)
        resolution = Resolution(market_id="mkt-1", outcome=ResolutionOutcome.DOWN)
        await portfolio.handle_resolution(trade, resolution)
        # down_shares = 0 (guarded), payout = 0
        assert trade.pnl == pytest.approx(0.0 - 20.0 - 0.2)


class TestUnknownResolution:
    """Portfolio handles UNKNOWN resolution gracefully."""

    async def test_directional_unknown_is_loss(self):
        repo = FakeRepository()
        portfolio = Portfolio(_make_config(), repo)
        trade = Trade(
            trade_id="t-unk", market_id="mkt-1",
            direction=Direction.UP, token_id="tok-up",
            amount=10.0, price=0.55, fee=0.1,
            signal_type=SignalType.BUY_UP,
        )
        await portfolio.record_trade(trade)
        resolution = Resolution(market_id="mkt-1", outcome=ResolutionOutcome.UNKNOWN)
        await portfolio.handle_resolution(trade, resolution)
        # Neither UP nor DOWN matched → loss
        assert trade.pnl == pytest.approx(-(10.0 + 0.1))

    async def test_arbitrage_unknown_conservative(self):
        repo = FakeRepository()
        portfolio = Portfolio(_make_config(), repo)
        trade = Trade(
            trade_id="t-arb-unk", market_id="mkt-1",
            direction=Direction.UP, token_id="tok-up",
            amount=20.0, price=0.48, fee=0.2,
            signal_type=SignalType.ARBITRAGE_BUY,
            alt_price=0.48,
        )
        await portfolio.record_trade(trade)
        resolution = Resolution(market_id="mkt-1", outcome=ResolutionOutcome.UNKNOWN)
        await portfolio.handle_resolution(trade, resolution)
        # UNKNOWN → payout = half = 10, pnl = 10 - 20 - 0.2 = -10.2
        assert trade.pnl == pytest.approx(10.0 - 20.0 - 0.2)


# === Engine boundary cases ===

class TestEngineBalanceBoundary:
    """Balance exactly at or just below required cost."""

    async def test_balance_exactly_equals_cost(self):
        # bet=10, fee=0.10, total=10.10
        cfg = _make_config(capital=10.10, bet=10.0)
        engine = PaperEngine(cfg)
        signal = Signal(signal_type=SignalType.BUY_UP, direction=Direction.UP, confidence=0.9)
        trade = await engine.execute_order(signal, _make_market(), _make_orderbook(0.55))
        assert trade is not None
        assert await engine.get_balance() == pytest.approx(0.0)

    async def test_balance_one_cent_below(self):
        cfg = _make_config(capital=10.09, bet=10.0)
        engine = PaperEngine(cfg)
        signal = Signal(signal_type=SignalType.BUY_UP, direction=Direction.UP, confidence=0.9)
        trade = await engine.execute_order(signal, _make_market(), _make_orderbook(0.55))
        assert trade is None
        assert await engine.get_balance() == pytest.approx(10.09)  # unchanged

    async def test_sequential_trades_deplete_balance(self):
        cfg = _make_config(capital=25.0, bet=10.0)
        engine = PaperEngine(cfg)
        signal = Signal(signal_type=SignalType.BUY_UP, direction=Direction.UP, confidence=0.9)
        market = _make_market()
        ob = _make_orderbook(0.55)

        t1 = await engine.execute_order(signal, market, ob)
        assert t1 is not None  # cost 10.10, balance 14.90

        t2 = await engine.execute_order(signal, market, ob)
        assert t2 is not None  # cost 10.10, balance 4.80

        t3 = await engine.execute_order(signal, market, ob)
        assert t3 is None  # insufficient balance


# === Directional strategy edge cases ===

class TestDirectionalEdgeCases:
    async def test_zero_start_price_skips(self):
        strategy = DirectionalStrategy()
        prices = [0.0] + [100.0] * 9  # start price is 0
        market = _make_market()
        ob = _make_orderbook(0.50)
        signal = await strategy.evaluate(market, ob, ob, prices)
        assert signal.signal_type == SignalType.SKIP
        assert "zero" in signal.reason.lower()

    async def test_empty_price_history(self):
        strategy = DirectionalStrategy()
        market = _make_market()
        ob = _make_orderbook(0.50)
        signal = await strategy.evaluate(market, ob, ob, [])
        assert signal.signal_type == SignalType.SKIP

    async def test_exactly_8_prices_works(self):
        strategy = DirectionalStrategy()
        # 8 ascending prices → should produce a signal (not SKIP due to insufficient)
        prices = [100.0 + i * 10 for i in range(8)]
        market = _make_market()
        ob = _make_orderbook(0.50)
        signal = await strategy.evaluate(market, ob, ob, prices)
        # Should not skip due to insufficient history (has exactly _SLOW_PERIOD=8)
        assert signal.signal_type != SignalType.SKIP or "insufficient" not in signal.reason


# === Portfolio win rate / profit factor edge cases ===

class TestPortfolioMetricsEdgeCases:
    async def test_win_rate_zero_trades(self):
        portfolio = Portfolio(_make_config(), FakeRepository())
        assert portfolio.win_rate == 0.0

    async def test_profit_factor_no_losses(self):
        portfolio = Portfolio(_make_config(), FakeRepository())
        portfolio._wins = 5
        portfolio._losses = 0
        assert portfolio.profit_factor == float("inf")

    async def test_profit_factor_no_trades(self):
        portfolio = Portfolio(_make_config(), FakeRepository())
        assert portfolio.profit_factor == 0.0
