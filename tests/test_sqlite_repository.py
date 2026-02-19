"""Tests for SQLiteRepository — DB round-trip, serialization, edge cases."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.config import Config
from src.models import (
    Direction,
    Market,
    MarketStatus,
    PortfolioSnapshot,
    ResolutionOutcome,
    SignalType,
    Trade,
)
from src.repository.sqlite import SQLiteRepository


def _make_config(tmp_path: Path) -> Config:
    return Config(data_dir=tmp_path)


def _make_trade(**overrides) -> Trade:
    defaults = dict(
        trade_id="t-001",
        market_id="mkt-1",
        direction=Direction.UP,
        token_id="tok-up",
        amount=10.0,
        price=0.55,
        fee=0.10,
        signal_type=SignalType.BUY_UP,
        timestamp=datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return Trade(**defaults)


def _make_market(**overrides) -> Market:
    defaults = dict(
        market_id="mkt-1",
        slug="btc-updown-5m-123",
        question="Bitcoin Up or Down?",
        status=MarketStatus.ACTIVE,
        up_token_id="tok-up",
        down_token_id="tok-down",
        end_time=datetime(2025, 6, 1, 12, 5, 0, tzinfo=timezone.utc),
        up_price=0.52,
        down_price=0.48,
    )
    defaults.update(overrides)
    return Market(**defaults)


class TestInitialize:
    async def test_creates_tables(self, tmp_path):
        cfg = _make_config(tmp_path)
        repo = SQLiteRepository(cfg)
        await repo.initialize()

        # Verify tables exist by running queries
        trades = await repo.get_trades(limit=1)
        assert trades == []
        snap = await repo.get_latest_snapshot()
        assert snap is None
        mkt = await repo.get_market("nonexistent")
        assert mkt is None
        await repo.close()

    async def test_db_property_before_init_raises(self, tmp_path):
        cfg = _make_config(tmp_path)
        repo = SQLiteRepository(cfg)
        with pytest.raises(RuntimeError, match="not initialized"):
            _ = repo.db


class TestTradeRoundTrip:
    async def test_save_and_retrieve(self, tmp_path):
        cfg = _make_config(tmp_path)
        repo = SQLiteRepository(cfg)
        await repo.initialize()

        trade = _make_trade()
        await repo.save_trade(trade)
        trades = await repo.get_trades(limit=10)

        assert len(trades) == 1
        t = trades[0]
        assert t.trade_id == "t-001"
        assert t.direction == Direction.UP
        assert t.amount == 10.0
        assert t.price == 0.55
        assert t.fee == 0.10
        assert t.signal_type == SignalType.BUY_UP
        assert t.resolved is False
        assert t.pnl is None
        await repo.close()

    async def test_update_resolution(self, tmp_path):
        cfg = _make_config(tmp_path)
        repo = SQLiteRepository(cfg)
        await repo.initialize()

        await repo.save_trade(_make_trade())
        await repo.update_trade_resolution("t-001", pnl=5.50)

        trades = await repo.get_trades(limit=1)
        assert trades[0].resolved is True
        assert trades[0].pnl == pytest.approx(5.50)
        await repo.close()

    async def test_get_open_trades_for_market(self, tmp_path):
        cfg = _make_config(tmp_path)
        repo = SQLiteRepository(cfg)
        await repo.initialize()

        await repo.save_trade(_make_trade(trade_id="t-open"))
        await repo.save_trade(_make_trade(trade_id="t-resolved", resolved=True, pnl=1.0))
        await repo.save_trade(_make_trade(trade_id="t-other", market_id="mkt-2"))

        open_trades = await repo.get_open_trades_for_market("mkt-1")
        assert len(open_trades) == 1
        assert open_trades[0].trade_id == "t-open"
        await repo.close()

    async def test_resolved_trades_filter(self, tmp_path):
        cfg = _make_config(tmp_path)
        repo = SQLiteRepository(cfg)
        await repo.initialize()

        await repo.save_trade(_make_trade(trade_id="t-1"))
        await repo.save_trade(_make_trade(trade_id="t-2"))
        await repo.update_trade_resolution("t-1", pnl=3.0)

        resolved = await repo.get_resolved_trades()
        assert len(resolved) == 1
        assert resolved[0].trade_id == "t-1"
        await repo.close()

    async def test_alt_price_round_trip(self, tmp_path):
        cfg = _make_config(tmp_path)
        repo = SQLiteRepository(cfg)
        await repo.initialize()

        trade = _make_trade(
            signal_type=SignalType.ARBITRAGE_BUY,
            alt_price=0.48,
        )
        await repo.save_trade(trade)
        trades = await repo.get_trades(limit=1)
        assert trades[0].alt_price == pytest.approx(0.48)
        await repo.close()

    async def test_alt_price_null(self, tmp_path):
        cfg = _make_config(tmp_path)
        repo = SQLiteRepository(cfg)
        await repo.initialize()

        await repo.save_trade(_make_trade())
        trades = await repo.get_trades(limit=1)
        assert trades[0].alt_price is None
        await repo.close()


class TestSnapshotRoundTrip:
    async def test_save_and_retrieve(self, tmp_path):
        cfg = _make_config(tmp_path)
        repo = SQLiteRepository(cfg)
        await repo.initialize()

        snap = PortfolioSnapshot(
            balance=950.0,
            total_trades=10,
            wins=6,
            losses=4,
            total_pnl=-50.0,
            max_drawdown=0.08,
            timestamp=datetime(2025, 6, 1, 23, 59, 0, tzinfo=timezone.utc),
        )
        await repo.save_portfolio_snapshot(snap)
        latest = await repo.get_latest_snapshot()

        assert latest is not None
        assert latest.balance == pytest.approx(950.0)
        assert latest.total_trades == 10
        assert latest.wins == 6
        assert latest.losses == 4
        assert latest.total_pnl == pytest.approx(-50.0)
        assert latest.max_drawdown == pytest.approx(0.08)
        await repo.close()

    async def test_latest_returns_most_recent(self, tmp_path):
        cfg = _make_config(tmp_path)
        repo = SQLiteRepository(cfg)
        await repo.initialize()

        for i, bal in enumerate([1000, 990, 980]):
            snap = PortfolioSnapshot(
                balance=float(bal), total_trades=i, wins=0,
                losses=i, total_pnl=float(bal - 1000), max_drawdown=0.0,
            )
            await repo.save_portfolio_snapshot(snap)

        latest = await repo.get_latest_snapshot()
        assert latest.balance == pytest.approx(980.0)
        await repo.close()


class TestMarketRoundTrip:
    async def test_save_and_retrieve(self, tmp_path):
        cfg = _make_config(tmp_path)
        repo = SQLiteRepository(cfg)
        await repo.initialize()

        market = _make_market()
        await repo.save_market(market)
        result = await repo.get_market("mkt-1")

        assert result is not None
        assert result.slug == "btc-updown-5m-123"
        assert result.status == MarketStatus.ACTIVE
        assert result.up_token_id == "tok-up"
        assert result.resolution is None
        await repo.close()

    async def test_market_with_resolution(self, tmp_path):
        cfg = _make_config(tmp_path)
        repo = SQLiteRepository(cfg)
        await repo.initialize()

        market = _make_market(
            status=MarketStatus.RESOLVED,
            resolution=ResolutionOutcome.UP,
        )
        await repo.save_market(market)
        result = await repo.get_market("mkt-1")

        assert result.status == MarketStatus.RESOLVED
        assert result.resolution == ResolutionOutcome.UP
        await repo.close()

    async def test_upsert_updates_existing(self, tmp_path):
        cfg = _make_config(tmp_path)
        repo = SQLiteRepository(cfg)
        await repo.initialize()

        await repo.save_market(_make_market(status=MarketStatus.ACTIVE))
        await repo.save_market(
            _make_market(status=MarketStatus.RESOLVED, resolution=ResolutionOutcome.DOWN)
        )

        result = await repo.get_market("mkt-1")
        assert result.status == MarketStatus.RESOLVED
        assert result.resolution == ResolutionOutcome.DOWN
        await repo.close()


class TestCloseWithoutInit:
    async def test_close_without_initialize(self, tmp_path):
        cfg = _make_config(tmp_path)
        repo = SQLiteRepository(cfg)
        await repo.close()  # should not raise
