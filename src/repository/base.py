from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from src.models import Market, PortfolioSnapshot, Trade


class Repository(ABC):
    """Abstract persistence layer — swap SQLite/Postgres via config."""

    @abstractmethod
    async def initialize(self) -> None:
        """Create tables / run migrations."""

    @abstractmethod
    async def save_trade(self, trade: Trade) -> None:
        """Persist a trade record."""

    @abstractmethod
    async def get_trades(self, limit: int = 50) -> list[Trade]:
        """Retrieve recent trades, newest first."""

    @abstractmethod
    async def get_resolved_trades(self) -> list[Trade]:
        """Retrieve all resolved trades for P&L calculation."""

    @abstractmethod
    async def update_trade_resolution(
        self, trade_id: str, pnl: float
    ) -> None:
        """Mark a trade as resolved with its P&L."""

    @abstractmethod
    async def save_portfolio_snapshot(self, snapshot: PortfolioSnapshot) -> None:
        """Save a portfolio state snapshot."""

    @abstractmethod
    async def get_latest_snapshot(self) -> PortfolioSnapshot | None:
        """Get the most recent portfolio snapshot for state recovery."""

    @abstractmethod
    async def save_market(self, market: Market) -> None:
        """Save or update a market record."""

    @abstractmethod
    async def get_market(self, market_id: str) -> Market | None:
        """Retrieve a market by ID."""

    @abstractmethod
    async def get_trades_since(self, since: datetime) -> list[Trade]:
        """지정 시점 이후 거래 조회."""

    @abstractmethod
    async def get_snapshots(self, limit: int = 100) -> list[PortfolioSnapshot]:
        """최근 스냅샷 N건 조회 (차트용)."""

    @abstractmethod
    async def get_open_trades_for_market(self, market_id: str) -> list[Trade]:
        """Get unresolved trades for a specific market."""

    @abstractmethod
    async def close(self) -> None:
        """Clean up connections."""
