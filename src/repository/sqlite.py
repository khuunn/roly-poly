from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

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
from src.repository.base import Repository

logger = logging.getLogger(__name__)

_CREATE_TRADES = """
CREATE TABLE IF NOT EXISTS trades (
    trade_id    TEXT PRIMARY KEY,
    market_id   TEXT NOT NULL,
    direction   TEXT NOT NULL,
    token_id    TEXT NOT NULL,
    amount      REAL NOT NULL,
    price       REAL NOT NULL,
    fee         REAL NOT NULL,
    signal_type TEXT NOT NULL,
    pnl         REAL,
    resolved    INTEGER NOT NULL DEFAULT 0,
    timestamp   TEXT NOT NULL,
    alt_price   REAL
)
"""

_CREATE_PORTFOLIO_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    balance       REAL NOT NULL,
    total_trades  INTEGER NOT NULL,
    wins          INTEGER NOT NULL,
    losses        INTEGER NOT NULL,
    total_pnl     REAL NOT NULL,
    max_drawdown  REAL NOT NULL,
    timestamp     TEXT NOT NULL
)
"""

_CREATE_MARKETS = """
CREATE TABLE IF NOT EXISTS markets (
    market_id     TEXT PRIMARY KEY,
    slug          TEXT NOT NULL,
    question      TEXT NOT NULL,
    status        TEXT NOT NULL,
    up_token_id   TEXT NOT NULL,
    down_token_id TEXT NOT NULL,
    end_time      TEXT NOT NULL,
    up_price      REAL NOT NULL,
    down_price    REAL NOT NULL,
    resolution    TEXT
)
"""


def _parse_dt(value: str) -> datetime:
    """Parse an ISO-format datetime string into a timezone-aware datetime."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _dt_to_str(dt: datetime) -> str:
    return dt.isoformat()


def _row_to_trade(row: aiosqlite.Row) -> Trade:
    return Trade(
        trade_id=row["trade_id"],
        market_id=row["market_id"],
        direction=Direction(row["direction"]),
        token_id=row["token_id"],
        amount=row["amount"],
        price=row["price"],
        fee=row["fee"],
        signal_type=SignalType(row["signal_type"]),
        pnl=row["pnl"],
        resolved=bool(row["resolved"]),
        timestamp=_parse_dt(row["timestamp"]),
        alt_price=row["alt_price"],
    )


def _row_to_snapshot(row: aiosqlite.Row) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        balance=row["balance"],
        total_trades=row["total_trades"],
        wins=row["wins"],
        losses=row["losses"],
        total_pnl=row["total_pnl"],
        max_drawdown=row["max_drawdown"],
        timestamp=_parse_dt(row["timestamp"]),
    )


def _row_to_market(row: aiosqlite.Row) -> Market:
    resolution = ResolutionOutcome(row["resolution"]) if row["resolution"] else None
    return Market(
        market_id=row["market_id"],
        slug=row["slug"],
        question=row["question"],
        status=MarketStatus(row["status"]),
        up_token_id=row["up_token_id"],
        down_token_id=row["down_token_id"],
        end_time=_parse_dt(row["end_time"]),
        up_price=row["up_price"],
        down_price=row["down_price"],
        resolution=resolution,
    )


class SQLiteRepository(Repository):
    """Async SQLite implementation of the Repository interface."""

    def __init__(self, config: Config) -> None:
        self._db_path: Path = config.sqlite_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute(_CREATE_TRADES)
        await self._db.execute(_CREATE_PORTFOLIO_SNAPSHOTS)
        await self._db.execute(_CREATE_MARKETS)
        await self._db.commit()
        logger.info("SQLite database initialized at %s", self._db_path)

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Repository not initialized — call initialize() first")
        return self._db

    # ── Trades ──────────────────────────────────────────────────────

    async def save_trade(self, trade: Trade) -> None:
        await self.db.execute(
            """
            INSERT OR REPLACE INTO trades
                (trade_id, market_id, direction, token_id, amount, price,
                 fee, signal_type, pnl, resolved, timestamp, alt_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade.trade_id,
                trade.market_id,
                trade.direction.value,
                trade.token_id,
                trade.amount,
                trade.price,
                trade.fee,
                trade.signal_type.value,
                trade.pnl,
                int(trade.resolved),
                _dt_to_str(trade.timestamp),
                trade.alt_price,
            ),
        )
        await self.db.commit()
        logger.debug("Saved trade %s", trade.trade_id)

    async def get_trades(self, limit: int = 50) -> list[Trade]:
        cursor = await self.db.execute(
            "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [_row_to_trade(r) for r in rows]

    async def get_resolved_trades(self) -> list[Trade]:
        cursor = await self.db.execute(
            "SELECT * FROM trades WHERE resolved = 1 ORDER BY timestamp DESC"
        )
        rows = await cursor.fetchall()
        return [_row_to_trade(r) for r in rows]

    async def update_trade_resolution(self, trade_id: str, pnl: float) -> None:
        await self.db.execute(
            "UPDATE trades SET resolved = 1, pnl = ? WHERE trade_id = ?",
            (pnl, trade_id),
        )
        await self.db.commit()
        logger.debug("Resolved trade %s with pnl=%.4f", trade_id, pnl)

    async def get_trades_since(self, since: datetime) -> list[Trade]:
        cursor = await self.db.execute(
            "SELECT * FROM trades WHERE timestamp >= ? ORDER BY timestamp DESC",
            (_dt_to_str(since),),
        )
        rows = await cursor.fetchall()
        return [_row_to_trade(r) for r in rows]

    async def get_snapshots(self, limit: int = 100) -> list[PortfolioSnapshot]:
        cursor = await self.db.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY id DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [_row_to_snapshot(r) for r in rows]

    async def get_open_trades_for_market(self, market_id: str) -> list[Trade]:
        cursor = await self.db.execute(
            "SELECT * FROM trades WHERE market_id = ? AND resolved = 0 ORDER BY timestamp DESC",
            (market_id,),
        )
        rows = await cursor.fetchall()
        return [_row_to_trade(r) for r in rows]

    # ── Portfolio ───────────────────────────────────────────────────

    async def save_portfolio_snapshot(self, snapshot: PortfolioSnapshot) -> None:
        await self.db.execute(
            """
            INSERT INTO portfolio_snapshots
                (balance, total_trades, wins, losses, total_pnl, max_drawdown, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.balance,
                snapshot.total_trades,
                snapshot.wins,
                snapshot.losses,
                snapshot.total_pnl,
                snapshot.max_drawdown,
                _dt_to_str(snapshot.timestamp),
            ),
        )
        await self.db.commit()
        logger.debug("Saved portfolio snapshot")

    async def get_latest_snapshot(self) -> PortfolioSnapshot | None:
        cursor = await self.db.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        return _row_to_snapshot(row) if row else None

    # ── Markets ─────────────────────────────────────────────────────

    async def save_market(self, market: Market) -> None:
        await self.db.execute(
            """
            INSERT OR REPLACE INTO markets
                (market_id, slug, question, status, up_token_id, down_token_id,
                 end_time, up_price, down_price, resolution)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                market.market_id,
                market.slug,
                market.question,
                market.status.value,
                market.up_token_id,
                market.down_token_id,
                _dt_to_str(market.end_time),
                market.up_price,
                market.down_price,
                market.resolution.value if market.resolution else None,
            ),
        )
        await self.db.commit()
        logger.debug("Saved market %s", market.market_id)

    async def get_market(self, market_id: str) -> Market | None:
        cursor = await self.db.execute(
            "SELECT * FROM markets WHERE market_id = ?", (market_id,)
        )
        row = await cursor.fetchone()
        return _row_to_market(row) if row else None

    # ── Lifecycle ───────────────────────────────────────────────────

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None
            logger.info("SQLite connection closed")
