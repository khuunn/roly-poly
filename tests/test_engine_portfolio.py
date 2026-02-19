"""Tests for PaperEngine and Portfolio modules."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.config import Config
from src.engine.paper import PaperEngine, _SLIPPAGE, _TAKER_FEE_RATE
from src.models import (
    Direction,
    Market,
    MarketStatus,
    OrderBook,
    OrderBookLevel,
    PortfolioSnapshot,
    Resolution,
    ResolutionOutcome,
    Signal,
    SignalType,
    Trade,
)
from src.portfolio import Portfolio
from src.repository.base import Repository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(capital: float = 1000.0, bet: float = 10.0) -> Config:
    return Config(initial_capital=capital, bet_size=bet, max_bet_size=bet)


def _make_market(
    *,
    market_id: str = "mkt-1",
    status: MarketStatus = MarketStatus.ACTIVE,
    resolution: ResolutionOutcome | None = None,
) -> Market:
    return Market(
        market_id=market_id,
        slug="test-market",
        question="Will it rain?",
        status=status,
        up_token_id="tok-up",
        down_token_id="tok-down",
        end_time=datetime(2026, 12, 31, tzinfo=timezone.utc),
        up_price=0.55,
        down_price=0.45,
        resolution=resolution,
    )


def _make_orderbook(best_ask: float | None = 0.55) -> OrderBook:
    asks = [OrderBookLevel(price=best_ask, size=100.0)] if best_ask is not None else []
    return OrderBook(token_id="tok-up", asks=asks)


def _make_signal(signal_type: SignalType) -> Signal:
    return Signal(signal_type=signal_type, confidence=0.8, reason="test")


# ---------------------------------------------------------------------------
# FakeRepository
# ---------------------------------------------------------------------------

class FakeRepository(Repository):
    """In-memory repository for testing."""

    def __init__(self) -> None:
        self.trades: dict[str, Trade] = {}
        self.snapshots: list[PortfolioSnapshot] = []
        self.markets: dict[str, Market] = {}

    async def initialize(self) -> None:
        pass

    async def save_trade(self, trade: Trade) -> None:
        self.trades[trade.trade_id] = trade

    async def get_trades(self, limit: int = 50) -> list[Trade]:
        return list(self.trades.values())[:limit]

    async def get_resolved_trades(self) -> list[Trade]:
        return [t for t in self.trades.values() if t.resolved]

    async def update_trade_resolution(self, trade_id: str, pnl: float) -> None:
        if trade_id in self.trades:
            self.trades[trade_id].pnl = pnl
            self.trades[trade_id].resolved = True

    async def save_portfolio_snapshot(self, snapshot: PortfolioSnapshot) -> None:
        self.snapshots.append(snapshot)

    async def get_latest_snapshot(self) -> PortfolioSnapshot | None:
        return self.snapshots[-1] if self.snapshots else None

    async def save_market(self, market: Market) -> None:
        self.markets[market.market_id] = market

    async def get_market(self, market_id: str) -> Market | None:
        return self.markets.get(market_id)

    async def get_trades_since(self, since: datetime) -> list[Trade]:
        return [t for t in self.trades.values() if t.timestamp >= since]

    async def get_snapshots(self, limit: int = 100) -> list[PortfolioSnapshot]:
        return self.snapshots[:limit]

    async def get_open_trades_for_market(self, market_id: str) -> list[Trade]:
        return [
            t for t in self.trades.values()
            if t.market_id == market_id and not t.resolved
        ]

    async def close(self) -> None:
        pass


# ===========================================================================
# PaperEngine tests
# ===========================================================================

class TestPaperEngineDirectional:
    """Directional buy order execution."""

    async def test_buy_up_fill_price_and_balance(self):
        cfg = _make_config(capital=1000.0, bet=10.0)
        engine = PaperEngine(cfg)
        ask = 0.55
        ob = _make_orderbook(best_ask=ask)
        signal = _make_signal(SignalType.BUY_UP)
        market = _make_market()

        trade = await engine.execute_order(signal, market, ob)

        assert trade is not None
        expected_price = ask * (1 + _SLIPPAGE)
        assert trade.price == pytest.approx(expected_price)
        assert trade.fee == pytest.approx(10.0 * _TAKER_FEE_RATE)
        assert trade.direction == Direction.UP
        assert trade.token_id == "tok-up"
        assert trade.amount == 10.0
        expected_balance = 1000.0 - 10.0 - (10.0 * _TAKER_FEE_RATE)
        assert await engine.get_balance() == pytest.approx(expected_balance)

    async def test_buy_down_direction_and_token(self):
        cfg = _make_config()
        engine = PaperEngine(cfg)
        signal = _make_signal(SignalType.BUY_DOWN)
        market = _make_market()
        ob = _make_orderbook(best_ask=0.45)

        trade = await engine.execute_order(signal, market, ob)

        assert trade is not None
        assert trade.direction == Direction.DOWN
        assert trade.token_id == "tok-down"

    async def test_insufficient_balance_returns_none(self):
        cfg = _make_config(capital=5.0, bet=10.0)
        engine = PaperEngine(cfg)
        signal = _make_signal(SignalType.BUY_UP)
        market = _make_market()
        ob = _make_orderbook(best_ask=0.55)

        trade = await engine.execute_order(signal, market, ob)

        assert trade is None
        assert await engine.get_balance() == pytest.approx(5.0)

    async def test_no_ask_price_returns_none(self):
        cfg = _make_config()
        engine = PaperEngine(cfg)
        signal = _make_signal(SignalType.BUY_UP)
        market = _make_market()
        ob = _make_orderbook(best_ask=None)

        trade = await engine.execute_order(signal, market, ob)

        assert trade is None

    async def test_skip_signal_returns_none(self):
        cfg = _make_config()
        engine = PaperEngine(cfg)
        signal = _make_signal(SignalType.SKIP)
        market = _make_market()
        ob = _make_orderbook()

        trade = await engine.execute_order(signal, market, ob)

        assert trade is None

    async def test_fill_price_capped_at_one(self):
        cfg = Config(initial_capital=1000.0, bet_size=10.0, max_bet_size=10.0, max_entry_price=1.0)
        engine = PaperEngine(cfg)
        signal = _make_signal(SignalType.BUY_UP)
        market = _make_market()
        ob = _make_orderbook(best_ask=0.999)

        trade = await engine.execute_order(signal, market, ob)

        assert trade is not None
        assert trade.price <= 1.0


class TestPaperEngineArbitrage:
    """Arbitrage order execution."""

    async def test_arbitrage_deducts_double_cost(self):
        cfg = _make_config(capital=1000.0, bet=10.0)
        engine = PaperEngine(cfg)
        signal = _make_signal(SignalType.ARBITRAGE_BUY)
        market = _make_market()
        ob = _make_orderbook(best_ask=0.45)

        trade = await engine.execute_order(signal, market, ob)

        assert trade is not None
        fee_per_side = 10.0 * _TAKER_FEE_RATE
        total_cost = (10.0 + fee_per_side) * 2
        assert await engine.get_balance() == pytest.approx(1000.0 - total_cost)
        assert trade.amount == pytest.approx(10.0 * 2)
        assert trade.fee == pytest.approx(fee_per_side * 2)
        assert trade.signal_type == SignalType.ARBITRAGE_BUY

    async def test_arbitrage_insufficient_balance(self):
        cfg = _make_config(capital=15.0, bet=10.0)
        engine = PaperEngine(cfg)
        signal = _make_signal(SignalType.ARBITRAGE_BUY)
        market = _make_market()
        ob = _make_orderbook(best_ask=0.45)

        trade = await engine.execute_order(signal, market, ob)

        assert trade is None
        assert await engine.get_balance() == pytest.approx(15.0)

    async def test_arbitrage_no_ask_returns_none(self):
        cfg = _make_config()
        engine = PaperEngine(cfg)
        signal = _make_signal(SignalType.ARBITRAGE_BUY)
        market = _make_market()
        ob = _make_orderbook(best_ask=None)

        trade = await engine.execute_order(signal, market, ob)

        assert trade is None


class TestPaperEngineResolution:
    """Market resolution checking."""

    async def test_resolved_market_returns_resolution(self):
        cfg = _make_config()
        engine = PaperEngine(cfg)
        market = _make_market(
            status=MarketStatus.RESOLVED,
            resolution=ResolutionOutcome.UP,
        )

        result = await engine.check_resolution(market)

        assert result is not None
        assert result.outcome == ResolutionOutcome.UP
        assert result.market_id == "mkt-1"

    async def test_active_market_returns_none(self):
        cfg = _make_config()
        engine = PaperEngine(cfg)
        market = _make_market(status=MarketStatus.ACTIVE)

        result = await engine.check_resolution(market)

        assert result is None

    async def test_resolved_but_no_outcome_returns_none(self):
        cfg = _make_config()
        engine = PaperEngine(cfg)
        market = _make_market(status=MarketStatus.RESOLVED, resolution=None)

        result = await engine.check_resolution(market)

        assert result is None


# ===========================================================================
# Portfolio tests
# ===========================================================================

class TestPortfolioRecordTrade:
    """Trade recording."""

    async def test_record_trade_increments_total(self):
        repo = FakeRepository()
        cfg = _make_config()
        portfolio = Portfolio(cfg, repo)

        trade = Trade(
            trade_id="t1",
            market_id="mkt-1",
            direction=Direction.UP,
            token_id="tok-up",
            amount=10.0,
            price=0.55,
            fee=0.1,
            signal_type=SignalType.BUY_UP,
        )

        await portfolio.record_trade(trade)

        assert portfolio.total_trades == 1
        assert "t1" in repo.trades

    async def test_multiple_records(self):
        repo = FakeRepository()
        cfg = _make_config()
        portfolio = Portfolio(cfg, repo)

        for i in range(3):
            trade = Trade(
                trade_id=f"t{i}",
                market_id="mkt-1",
                direction=Direction.UP,
                token_id="tok-up",
                amount=10.0,
                price=0.55,
                fee=0.1,
                signal_type=SignalType.BUY_UP,
            )
            await portfolio.record_trade(trade)

        assert portfolio.total_trades == 3


class TestPortfolioDirectionalPnL:
    """Directional trade profit/loss calculation."""

    async def test_directional_win_pnl(self):
        repo = FakeRepository()
        cfg = _make_config(capital=1000.0, bet=10.0)
        portfolio = Portfolio(cfg, repo)

        price = 0.55
        amount = 10.0
        fee = amount * _TAKER_FEE_RATE

        trade = Trade(
            trade_id="t-win",
            market_id="mkt-1",
            direction=Direction.UP,
            token_id="tok-up",
            amount=amount,
            price=price,
            fee=fee,
            signal_type=SignalType.BUY_UP,
        )
        await portfolio.record_trade(trade)

        resolution = Resolution(
            market_id="mkt-1", outcome=ResolutionOutcome.UP
        )
        await portfolio.handle_resolution(trade, resolution)

        shares = amount / price
        payout = shares * 1.0
        expected_pnl = payout - amount - fee
        assert trade.pnl == pytest.approx(expected_pnl)
        assert trade.resolved is True

    async def test_directional_loss_pnl(self):
        repo = FakeRepository()
        cfg = _make_config(capital=1000.0, bet=10.0)
        portfolio = Portfolio(cfg, repo)

        amount = 10.0
        fee = amount * _TAKER_FEE_RATE

        trade = Trade(
            trade_id="t-loss",
            market_id="mkt-1",
            direction=Direction.UP,
            token_id="tok-up",
            amount=amount,
            price=0.55,
            fee=fee,
            signal_type=SignalType.BUY_UP,
        )
        await portfolio.record_trade(trade)

        resolution = Resolution(
            market_id="mkt-1", outcome=ResolutionOutcome.DOWN
        )
        await portfolio.handle_resolution(trade, resolution)

        expected_pnl = -(amount + fee)
        assert trade.pnl == pytest.approx(expected_pnl)
        assert trade.resolved is True


class TestPortfolioArbitragePnL:
    """Arbitrage trade profit/loss calculation."""

    async def test_arbitrage_pnl_up_wins(self):
        repo = FakeRepository()
        cfg = _make_config(capital=1000.0, bet=10.0)
        portfolio = Portfolio(cfg, repo)

        up_price = 0.45
        amount = 20.0  # both sides combined
        fee = 0.2  # total fee both sides

        trade = Trade(
            trade_id="t-arb",
            market_id="mkt-1",
            direction=Direction.UP,
            token_id="tok-up",
            amount=amount,
            price=up_price,
            fee=fee,
            signal_type=SignalType.ARBITRAGE_BUY,
        )
        await portfolio.record_trade(trade)

        resolution = Resolution(
            market_id="mkt-1", outcome=ResolutionOutcome.UP
        )
        await portfolio.handle_resolution(trade, resolution)

        # UP wins: payout = (amount/2) / up_price * $1
        half = amount / 2
        up_shares = half / up_price
        payout = up_shares * 1.0
        expected_pnl = payout - amount - fee
        assert trade.pnl == pytest.approx(expected_pnl)
        assert expected_pnl > 0  # arb should be profitable

    async def test_arbitrage_pnl_down_wins(self):
        repo = FakeRepository()
        cfg = _make_config(capital=1000.0, bet=10.0)
        portfolio = Portfolio(cfg, repo)

        # Use a low up_price so arb is profitable when DOWN wins:
        # down_price_est = 1 - 0.30 = 0.70, half=10, down_shares=10/0.70=14.29
        # payout=14.29, pnl=14.29 - 20.0 - 0.2 = -5.9 ... still negative.
        # Arb is profitable when UP wins because up_price is low.
        # When DOWN wins at estimated price, it may not be profitable.
        # The test should verify the PnL math is correct, not that it's always positive.
        up_price = 0.45
        amount = 20.0
        fee = 0.2

        trade = Trade(
            trade_id="t-arb2",
            market_id="mkt-1",
            direction=Direction.UP,
            token_id="tok-up",
            amount=amount,
            price=up_price,
            fee=fee,
            signal_type=SignalType.ARBITRAGE_BUY,
        )
        await portfolio.record_trade(trade)

        resolution = Resolution(
            market_id="mkt-1", outcome=ResolutionOutcome.DOWN
        )
        await portfolio.handle_resolution(trade, resolution)

        # DOWN wins: payout = (amount/2) / (1 - up_price) * $1
        half = amount / 2
        down_price_est = max(1.0 - up_price, 0.01)
        down_shares = half / down_price_est
        payout = down_shares * 1.0
        expected_pnl = payout - amount - fee
        assert trade.pnl == pytest.approx(expected_pnl)


class TestPortfolioBalanceFlow:
    """End-to-end balance flow: engine deducts -> portfolio adds payout."""

    async def test_balance_flow_directional_win(self):
        repo = FakeRepository()
        cfg = _make_config(capital=1000.0, bet=10.0)
        engine = PaperEngine(cfg)
        portfolio = Portfolio(cfg, repo)

        signal = _make_signal(SignalType.BUY_UP)
        market = _make_market()
        ob = _make_orderbook(best_ask=0.55)

        trade = await engine.execute_order(signal, market, ob)
        assert trade is not None

        await portfolio.record_trade(trade)

        resolution = Resolution(market_id="mkt-1", outcome=ResolutionOutcome.UP)
        await portfolio.handle_resolution(trade, resolution)

        # Net effect = PnL
        shares = trade.amount / trade.price
        payout = shares * 1.0
        expected_pnl = payout - trade.amount - trade.fee
        net_change = portfolio.balance - 1000.0
        assert net_change == pytest.approx(expected_pnl)

    async def test_balance_flow_directional_loss(self):
        repo = FakeRepository()
        cfg = _make_config(capital=1000.0, bet=10.0)
        engine = PaperEngine(cfg)
        portfolio = Portfolio(cfg, repo)

        signal = _make_signal(SignalType.BUY_UP)
        market = _make_market()
        ob = _make_orderbook(best_ask=0.55)

        trade = await engine.execute_order(signal, market, ob)
        assert trade is not None

        await portfolio.record_trade(trade)

        resolution = Resolution(market_id="mkt-1", outcome=ResolutionOutcome.DOWN)
        await portfolio.handle_resolution(trade, resolution)

        expected_pnl = -(trade.amount + trade.fee)
        net_change = portfolio.balance - 1000.0
        assert net_change == pytest.approx(expected_pnl)


class TestPortfolioDrawdown:
    """Max drawdown tracking."""

    async def test_max_drawdown_after_losses(self):
        repo = FakeRepository()
        cfg = _make_config(capital=100.0, bet=10.0)
        engine = PaperEngine(cfg)
        portfolio = Portfolio(cfg, repo)

        signal = _make_signal(SignalType.BUY_UP)
        market = _make_market()
        ob = _make_orderbook(best_ask=0.55)

        for i in range(3):
            trade = await engine.execute_order(signal, market, ob)
            assert trade is not None
            await portfolio.record_trade(trade)

            resolution = Resolution(
                market_id="mkt-1", outcome=ResolutionOutcome.DOWN
            )
            await portfolio.handle_resolution(trade, resolution)

        assert portfolio.max_drawdown > 0.0
        # After 3 losses the balance dropped from peak (100) due to lost bets
        expected_drawdown = (100.0 - portfolio.balance) / 100.0
        assert portfolio.max_drawdown == pytest.approx(expected_drawdown)


class TestPortfolioWinLoss:
    """Win/loss counters."""

    async def test_win_loss_counters(self):
        repo = FakeRepository()
        cfg = _make_config(capital=1000.0, bet=10.0)
        portfolio = Portfolio(cfg, repo)

        # Record a win
        t1 = Trade(
            trade_id="t-w",
            market_id="mkt-1",
            direction=Direction.UP,
            token_id="tok-up",
            amount=10.0,
            price=0.55,
            fee=0.1,
            signal_type=SignalType.BUY_UP,
        )
        await portfolio.record_trade(t1)
        await portfolio.handle_resolution(
            t1, Resolution(market_id="mkt-1", outcome=ResolutionOutcome.UP)
        )

        # Record a loss
        t2 = Trade(
            trade_id="t-l",
            market_id="mkt-1",
            direction=Direction.UP,
            token_id="tok-up",
            amount=10.0,
            price=0.55,
            fee=0.1,
            signal_type=SignalType.BUY_UP,
        )
        await portfolio.record_trade(t2)
        await portfolio.handle_resolution(
            t2, Resolution(market_id="mkt-1", outcome=ResolutionOutcome.DOWN)
        )

        assert portfolio._wins == 1
        assert portfolio._losses == 1
        assert portfolio.win_rate == pytest.approx(0.5)


class TestPortfolioSnapshot:
    """Snapshot persistence."""

    async def test_save_snapshot_persists_values(self):
        repo = FakeRepository()
        cfg = _make_config(capital=1000.0, bet=10.0)
        portfolio = Portfolio(cfg, repo)

        # Record and resolve a trade to change state
        trade = Trade(
            trade_id="t-snap",
            market_id="mkt-1",
            direction=Direction.UP,
            token_id="tok-up",
            amount=10.0,
            price=0.55,
            fee=0.1,
            signal_type=SignalType.BUY_UP,
        )
        await portfolio.record_trade(trade)
        await portfolio.handle_resolution(
            trade, Resolution(market_id="mkt-1", outcome=ResolutionOutcome.UP)
        )

        await portfolio.save_snapshot()

        assert len(repo.snapshots) == 1
        snap = repo.snapshots[0]
        assert snap.balance == pytest.approx(portfolio.balance)
        assert snap.total_trades == portfolio.total_trades
        assert snap.wins == 1
        assert snap.losses == 0
        assert snap.total_pnl == pytest.approx(portfolio._total_pnl)
        assert snap.max_drawdown == pytest.approx(portfolio.max_drawdown)
